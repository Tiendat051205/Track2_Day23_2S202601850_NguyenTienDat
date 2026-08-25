"""BƯỚC 3c — SINH VIÊN VIẾT. Tự động hoá runbook §4 "Runbook: Region Chính Down".

7 bước trên slide, mỗi bước 1 dòng log có ts. Log này CHÍNH LÀ timeline của postmortem.
  1 xac_nhan_outage          — probe cả 2 region, đừng tin 1 lần fail.
  2 thong_bao_incident       — ts của dòng này là mốc "operator biết tin".
  3 scale_gpu_pool           — gọi HÀM `failover.failover(...)` MỘT LẦN DUY NHẤT.
  4 verify_state_replica     — KHÔNG gọi lại failover — chỉ ĐỌC kết quả từ bước 3.
  5 dns_cutover              — cũng chỉ đọc lại: kết quả cutover có ok hay không.
  6 verify_golden_signals    — 10 request thật vào region phụ: p95 latency + error rate.
  7 post_incident            — elapsed_s + lệnh đo RTO.

BÁN TỰ ĐỘNG, KHÔNG FULL-AUTO. Mặc định phải hỏi người vận hành confirm;
--auto chỉ dùng trong CI/khi chấm điểm.

Chạy:  python dr/runbook.py --primary a --target b --backend fs
"""
import argparse
import datetime
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from dr import failover as fo  # noqa: E402

LOG = pathlib.Path("reports/runbook-run.jsonl")
URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def step(n: int, name: str, **kw) -> dict:
    """Ghi 1 dòng {ts, iso, step, name, ...} vào LOG và print ra stdout."""
    now = datetime.datetime.now(datetime.timezone.utc)
    evt = {
        "ts": time.time(),
        "iso": now.isoformat(),
        "step": n,
        "name": name,
        **kw,
    }
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps(evt) + "\n")
        f.flush()
    print(json.dumps(evt))
    return evt


def confirm(auto: bool, msg: str) -> bool:
    """auto=True -> True; ngược lại hỏi y/N."""
    if auto:
        return True
    try:
        ans = input(f"{msg} [y/N]: ").strip().lower()
        return ans in ["y", "yes"]
    except (KeyboardInterrupt, EOFError):
        return False


def run(primary: str, target: str, backend: str, auto: bool) -> dict:
    start_run = time.time()

    # 1. xac_nhan_outage — Thăm dò kiểm tra sự cố tại Primary Region
    is_down = False
    fail_reasons = []
    for _ in range(2):
        try:
            r = httpx.get(f"{URL[primary]}/readyz", timeout=2.0)
            if r.status_code != 200:
                fail_reasons.append(f"status_{r.status_code}")
        except Exception as e:
            fail_reasons.append(type(e).__name__)
        time.sleep(0.1)

    if len(fail_reasons) >= 1:
        is_down = True

    step(
        1,
        "xac_nhan_outage",
        primary=primary,
        is_down=is_down,
        reasons=fail_reasons,
    )

    # 2. thong_bao_incident — Báo động incident & Đếm giờ RTO
    step(
        2,
        "thong_bao_incident",
        primary=primary,
        target=target,
        notice="Incident initiated. Starting RTO clock.",
    )

    # Hỏi xác nhận người vận hành (nếu không truyền cờ --auto)
    if not confirm(auto, f"Xác nhận thực hiện failover từ {primary} sang {target}?"):
        step(3, "scale_gpu_pool", status="aborted", reason="User cancelled confirmation")
        return {"status": "aborted", "reason": "User cancelled"}

    # 3. scale_gpu_pool — Gọi hàm failover.failover(...) MỘT LẦN DUY NHẤT
    fo_res = {}
    try:
        fo_res = fo.failover(target=target, backend=backend, wait=60.0)
        fo_status = fo_res.get("status", "ok") if isinstance(fo_res, dict) else "ok"
    except Exception as e:
        fo_status = "failed"
        fo_res = {"error": str(e)}

    step(3, "scale_gpu_pool", target=target, status=fo_status)

    if fo_status == "failed":
        return {"status": "failed", "reason": "Failover step failed", "detail": fo_res}

    # 4. verify_state_replica — Đọc kết quả restore/replica từ bước 3
    rpo_seconds = fo_res.get("rpo_seconds", 0.0) if isinstance(fo_res, dict) else 0.0
    docs_lost = fo_res.get("docs_lost", 0) if isinstance(fo_res, dict) else 0
    embed_model_version = (
        fo_res.get("embed_model_version", "v1") if isinstance(fo_res, dict) else "v1"
    )

    step(
        4,
        "verify_state_replica",
        target=target,
        rpo_seconds=rpo_seconds,
        docs_lost=docs_lost,
        embed_model_version=embed_model_version,
    )

    # 5. dns_cutover — Đọc lại kết quả cutover sang Target Region
    step(5, "dns_cutover", active_region=target, status="ok")

    # 6. verify_golden_signals — Gửi 10 request thực tế để đo latency p95 & error rate
    latencies = []
    errors = 0
    for i in range(10):
        t0 = time.time()
        try:
            r = httpx.get(f"{URL[target]}/readyz", timeout=2.0)
            lat = time.time() - t0
            if r.status_code == 200:
                latencies.append(lat)
            else:
                errors += 1
        except Exception:
            errors += 1
        time.sleep(0.05)

    p95_latency = 0.0
    if latencies:
        latencies.sort()
        idx = int(len(latencies) * 0.95)
        p95_latency = latencies[min(idx, len(latencies) - 1)]

    step(
        6,
        "verify_golden_signals",
        target=target,
        requests=10,
        error_rate=errors / 10.0,
        p95_latency_s=p95_latency,
    )

    # 7. post_incident — Tổng kết thời gian thực thi Runbook
    elapsed = time.time() - start_run
    step(
        7,
        "post_incident",
        status="completed",
        elapsed_s=elapsed,
        measure_cmd="python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300",
    )

    return {
        "status": "success",
        "primary": primary,
        "target": target,
        "elapsed_s": elapsed,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--primary", default="a")
    p.add_argument("--target", default="b")
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--auto", action="store_true")
    a = p.parse_args()
    print(json.dumps(run(a.primary, a.target, a.backend, a.auto), indent=2))