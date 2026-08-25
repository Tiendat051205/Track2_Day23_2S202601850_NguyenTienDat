"""BƯỚC 3a — SINH VIÊN VIẾT. Health checker cho 2 region.

Yêu cầu (đọc §4 "Kiến Trúc Health-Check-Based Failover" + §2 "DNS Failover"):
  1. Poll /readyz của CẢ HAI region mỗi `interval` giây (mặc định 5s).
     Dùng /readyz, KHÔNG dùng /healthz. /healthz chỉ nói "process còn sống" —
     region có process sống nhưng vector DB rỗng thì vẫn không serve được.
  2. Chỉ đổi trạng thái sau `threshold` lần fail LIÊN TIẾP (mặc định 3).
     Một lần fail không phải outage. Đây là chống flapping (§4 Anti-Patterns).
  3. Ghi 1 dòng JSONL MỖI LẦN ĐỔI TRẠNG THÁI (không ghi mỗi lần poll — log sẽ ngập).
     Dòng bắt buộc có: ts, region, to (HEALTHY|UNHEALTHY), reason,
     interval_s, threshold. Thiếu interval_s/threshold thì tools/measure_rto.py
     không tính được detect floor -> mất điểm.

Chạy:  python dr/health_checker.py --interval 5 --threshold 3 --duration 300 \
              --out reports/health-events.jsonl

CÂU HỎI PHẢI TRẢ LỜI TRƯỚC KHI VIẾT (ghi câu trả lời vào reports/postmortem.md):
  interval=5s, threshold=3 -> sớm nhất bạn có thể phát hiện outage là bao nhiêu giây?
  Con số đó nằm TRONG RTO của bạn. Muốn RTO 5 phút thì được phép chọn interval bao nhiêu?
"""
import argparse
import json
import pathlib
import time

import httpx

URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def probe(region: str, timeout: float) -> tuple[bool, str]:
    """Trả về (ready, reason). Timeout PHẢI có — netblock làm request treo mãi."""
    endpoint = f"{URL[region]}/readyz"
    try:
        response = httpx.get(endpoint, timeout=timeout)
        if response.status_code == 200:
            return True, "readyz 200"
        else:
            return False, f"readyz {response.status_code}"
    except httpx.TimeoutException:
        return False, "timeout"
    except httpx.RequestError as e:
        return False, f"request error: {type(e).__name__}"


def run(interval: float, timeout: float, threshold: int, duration: float, out: pathlib.Path):
    # 1. Khởi tạo trạng thái ban đầu của 2 region
    state = {
        "a": {"status": "HEALTHY", "fail_count": 0},
        "b": {"status": "HEALTHY", "fail_count": 0},
    }
    
    start_time = time.time()
    out.parent.mkdir(parents=True, exist_ok=True)

    with open(out, "w") as f:
        # 2. Vòng lặp chạy trong khoảng thời gian duration
        while time.time() - start_time < duration:
            loop_start = time.time()
            
            for region in ["a", "b"]:
                # Gọi probe() theo positional arguments
                ready, reason = probe(region, timeout)
                
                curr = state[region]
                
                if ready:
                    # Reset đếm lỗi nếu probe thành công
                    curr["fail_count"] = 0
                    new_status = "HEALTHY"
                else:
                    # Tăng số lần lỗi liên tiếp
                    curr["fail_count"] += 1
                    # Chỉ chuyển sang UNHEALTHY khi đủ threshold lần lỗi liên tiếp
                    if curr["fail_count"] >= threshold:
                        new_status = "UNHEALTHY"
                    else:
                        new_status = curr["status"]  # Giữ nguyên trạng thái cũ
                
                # 3. Kiểm tra xem có ĐỔI TRẠNG THÁI (state transition) hay không
                if new_status != curr["status"]:
                    event = {
                        "event": "state_change",
                        "ts": time.time(),
                        "region": region,
                        "to": new_status,
                        "reason": reason,
                        "interval_s": interval,
                        "threshold": threshold,
                        "consecutive_fails": curr["fail_count"],
                    }
                    # Ghi dòng JSONL
                    f.write(json.dumps(event) + "\n")
                    f.flush()
                    
                    # Cập nhật trạng thái mới
                    curr["status"] = new_status

            # 4. Duy trì chu kỳ polling `interval`
            elapsed = time.time() - loop_start
            sleep_time = max(0.0, interval - elapsed)
            time.sleep(sleep_time)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=float, default=5.0)
    p.add_argument("--timeout", type=float, default=2.0)
    p.add_argument("--threshold", type=int, default=3)
    p.add_argument("--duration", type=float, default=300)
    p.add_argument("--out", default="reports/health-events.jsonl")
    a = p.parse_args()
    run(a.interval, a.timeout, a.threshold, a.duration, pathlib.Path(a.out))