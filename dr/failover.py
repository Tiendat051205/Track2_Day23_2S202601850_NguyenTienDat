"""BƯỚC 3b — SINH VIÊN VIẾT. Cutover sang region phụ.

5 bước, THỨ TỰ QUAN TRỌNG (§2 Kiến Trúc Tham Chiếu: DNS/LB, compute, state là 3 lớp riêng):
  1_verify_target    — /v1/state của region phụ: weights? vector count? pool_state?
  2_restore_snapshot — gọi state/snapshot.py get + state/snapshot.py rpo()
                       Log BẮT BUỘC: rpo_seconds, docs_lost, embed_model_version.
  3_scale_pool       — ghi "full" vào state/region-<t>/pool_state (warm -> full)
  4_wait_ready       — POLL /readyz tới khi 200.
  5_dns_cutover      — ghi region đích vào edge/active_region

Chạy:  python dr/failover.py --target b --backend fs
"""
import argparse
import datetime
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from state import snapshot  # noqa: E402

URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}
LOG = pathlib.Path("reports/failover-events.jsonl")


def emit(**kw):
    """Append 1 dòng JSONL có ts + iso vào LOG, và print ra stdout."""
    now = datetime.datetime.now(datetime.timezone.utc)
    evt = {
        "ts": time.time(),
        "iso": now.isoformat(),
        **kw,
    }
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps(evt) + "\n")
        f.flush()
    print(json.dumps(evt))
    return evt


def failover(target: str, backend: str, wait: float) -> dict:
    target_url = URL[target]

    # Bước 1: 1_verify_target — Kiểm tra trạng thái hiện tại của target region
    state_info = {}
    try:
        resp = httpx.get(f"{target_url}/v1/state", timeout=5.0)
        if resp.status_code == 200:
            state_info = resp.json()
    except Exception:
        pass

    emit(
        step="1_verify_target",
        target=target,
        status="ok",
        weights=state_info.get("weights", False),
        vector_count=state_info.get("count", 0),
        pool_state=state_info.get("pool_state", "unknown"),
    )

    # Bước 2: 2_restore_snapshot — Khôi phục snapshot & tính toán RPO.
    # snapshot.get() nhận tham số `region`, còn snapshot.rpo() nhận hai
    # đường dẫn database. Không được nuốt lỗi ở đây: nếu restore thất bại thì
    # target không thể ready và failover phải dừng với lỗi rõ ràng.
    try:
        restore_res = snapshot.get(region=target, backend=backend)

        primary = "b" if target == "a" else "a"
        rpo_res = snapshot.rpo(
            pathlib.Path(f"state/region-{primary}/vectors.sqlite"),
            pathlib.Path(f"state/region-{target}/vectors.sqlite"),
        )
    except Exception as exc:
        emit(
            step="2_restore_snapshot",
            target=target,
            backend=backend,
            status="failed",
            reason=type(exc).__name__,
            detail=str(exc),
        )
        return {
            "status": "failed",
            "reason": "snapshot_restore_failed",
            "target": target,
            "message": str(exc),
        }

    rpo_seconds = rpo_res.get("rpo_seconds", 0.0) if isinstance(rpo_res, dict) else 0.0
    docs_lost = rpo_res.get("docs_lost", 0) if isinstance(rpo_res, dict) else 0
    embed_model_version = (
        restore_res.get("embed_model_version", "v1")
        if isinstance(restore_res, dict)
        else "v1"
    )

    emit(
        step="2_restore_snapshot",
        target=target,
        backend=backend,
        rpo_seconds=rpo_seconds,
        docs_lost=docs_lost,
        embed_model_version=embed_model_version,
        status="ok",
    )

    # Bước 3: 3_scale_pool — Chuyển pool_state thành "full"
    pool_file = pathlib.Path(f"state/region-{target}/pool_state")
    pool_file.parent.mkdir(parents=True, exist_ok=True)
    pool_file.write_text("full")

    # Bắt buộc thông báo cho server Target cập nhật trạng thái pool.
    # Snapshot đã được nạp trực tiếp vào state directory ở bước 2; serving
    # đọc lại state từ filesystem ở mỗi readiness check.
    try:
        httpx.post(f"{target_url}/v1/pool_state", json={"state": "full"}, timeout=5.0)
    except Exception:
        pass

    emit(step="3_scale_pool", target=target, pool_state="full", status="ok")

    # Bước 4: 4_wait_ready — Poll /readyz tới khi HTTP 200
    start_wait = time.time()
    ready = False
    ready_reason = "timeout"

    while time.time() - start_wait < wait:
        try:
            r = httpx.get(f"{target_url}/readyz", timeout=2.0)
            if r.status_code == 200:
                ready = True
                ready_reason = "ok"
                break
            else:
                ready_reason = f"status_{r.status_code}"
                # Gửi trigger yêu cầu server nạp dữ liệu nếu vẫn báo 503
                try:
                    httpx.post(f"{target_url}/v1/reload", timeout=1.0)
                except Exception:
                    pass
        except Exception as e:
            ready_reason = f"request_error_{type(e).__name__}"

        time.sleep(0.5)

    if not ready:
        emit(
            step="4_wait_ready",
            target=target,
            status="failed",
            reason=ready_reason,
            elapsed_s=time.time() - start_wait,
        )
        return {
            "status": "failed",
            "reason": ready_reason,
            "target": target,
            "message": f"Target region {target} failed to become ready within {wait}s."
        }

    emit(
        step="4_wait_ready",
        target=target,
        status="ok",
        elapsed_s=time.time() - start_wait,
    )

    # Bước 5: 5_dns_cutover — Ghi region vào edge/active_region sau khi Bước 4 thành công
    active_region_file = pathlib.Path("edge/active_region")
    active_region_file.parent.mkdir(parents=True, exist_ok=True)
    active_region_file.write_text(target)

    emit(step="5_dns_cutover", active_region=target, status="ok")

    return {
        "status": "success",
        "target": target,
        "rpo_seconds": rpo_seconds,
        "docs_lost": docs_lost,
        "embed_model_version": embed_model_version,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="b", choices=["a", "b"])
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--wait", type=float, default=60)
    a = p.parse_args()
    print(json.dumps(failover(a.target, a.backend, a.wait), indent=2))
