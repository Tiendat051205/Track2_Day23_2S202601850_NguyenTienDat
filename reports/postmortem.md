# Postmortem — DR Drill Lab 23

Đây là postmortem blameless. Drill mô phỏng region A bị network block và phục hồi traffic sang region B.

## 1. Timeline

| ISO time | Sự kiện | Evidence |
|---|---|---|
| 2026-08-25T17:19:49Z | A bị netblock; bắt đầu RTO clock | `chaos/chaos-events.jsonl:17` |
| 2026-08-25T17:19:49Z | User đầu tiên nhận `ReadTimeout` | `reports/drill-2-withdr.jsonl:25` |
| 2026-08-25T17:19:53Z | Restore B, RPO `20.03s`, mất `10` docs | `reports/failover-events.jsonl:47` |
| 2026-08-25T17:19:53Z | B ready và DNS cutover | `reports/failover-events.jsonl:49`, `reports/failover-events.jsonl:50` |
| 2026-08-25T17:19:59Z | Request đầu tiên thành công từ B | `reports/drill-2-withdr.jsonl:30` |
| 2026-08-25T17:20:04Z | Health checker ghi nhận A `UNHEALTHY` | `reports/health-events.jsonl:3` |

## 2. RTO/RPO đo được vs mục tiêu

- RTO mục tiêu `300s`; đo được `10.3s`; gap `289.7s` — PASS.
- RPO mục tiêu `300s`; đo được `20.03s`; mất `10` docs; gap `279.97s` — PASS.
- Thành phần tốn nhiều thời gian nhất trong trải nghiệm user là DNS/LB cache sau cutover, khoảng `5.8s`.
- Health detection hoàn tất ở `15.0s` nhưng không nằm trên critical path vì operator chạy failover trước đó; tool cảnh báo `t_cutover < t_detect`.

## 3. Root cause — 5 whys

1. User nhận lỗi vì edge vẫn trỏ vào A đang bị netblock.
2. Edge chưa chuyển vì DNS cutover chỉ xảy ra sau restore và readiness.
3. B sẵn sàng vì snapshot đã restore vector DB và model weights.
4. Lần chạy trước không recovery vì `failover.py` truyền sai keyword cho `snapshot.get()` và nuốt exception, khiến B thiếu state nhưng vẫn poll.
5. Lỗi không lộ sớm vì restore failure chỉ bị phát hiện sau timeout `/readyz` 60s.

## 4. Action items

| # | Action | Owner | Deadline | Tác động dự kiến |
|---|---|---|---|---|
| 1 | Fail fast khi restore lỗi; không nuốt `Exception` | Developer | Đã thực hiện | Tránh timeout giả `60s` |
| 2 | Tách automated failover khỏi manual cutover để đo health detection đúng critical path | DR owner | Trước drill kế tiếp | RTO tái lập được |
| 3 | Giảm DNS TTL hoặc invalidate cache sau cutover | Platform owner | Trước production review | Giảm khoảng `5.8s` |
| 4 | Tăng tần suất replication để giảm RPO | Data owner | Trước drill kế tiếp | Giảm `20.03s` và `10` docs |

## 5. Ba câu hỏi bắt buộc

1. `interval × threshold = 5 × 3 = 15s`, bằng `5.0%` RTO mục tiêu `300s`.
2. Hạ interval xuống `1s` giảm detect floor từ `15s` xuống `3s`, tức giảm lý thuyết `12s`, nhưng tăng probe load và nguy cơ flapping.
3. Nếu outage kéo dài 6 giờ, `docs_lost` là số document đã có ở primary nhưng chưa có trong snapshot restore. Drill này ghi nhận `10`; đó là dữ liệu có thể phải phục hồi từ nguồn khác hoặc chấp nhận mất.
