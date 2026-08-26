# RTO/RPO Evidence — Lab 23

Số liệu lấy từ drill mới nhất trong log. Drill 2 đã phục hồi sang region B; cảnh báo là DNS cutover được thực hiện trước khi health checker ghi nhận A `UNHEALTHY`.

## 1. Drill 1 — không có DR (baseline)

| Chỉ số | Giá trị | Cách đo | Evidence |
|---|---:|---|---|
| t_outage | `2026-08-25T18:32:59` | chaos kill region A | `chaos/chaos-events.jsonl:5` |
| Request fail đầu tiên | `+0.1s` | request `ok:false` đầu tiên | `reports/drill-1-nodr.jsonl:17` |
| Request thành công sau đó | không có | không có `ok:true` sau outage | `reports/drill-1-nodr.jsonl:32` |
| RTO | `NO_RECOVERY` | đo từ timestamp request | `reports/drill-1-nodr.jsonl:32` |

## 2. Drill 2 — có DR

| Mốc | +giây từ t_outage | Cách đo | Evidence |
|---|---:|---|---|
| t_outage | `0.0s` | `action:kill`, region A | `chaos/chaos-events.jsonl:1` |
| User thấy lỗi đầu tiên | `0.1s` | `ok:false` đầu tiên | `reports/drill-2-withdr.jsonl:25` |
| Health check phát hiện | `15.0s` | A chuyển `UNHEALTHY` | `reports/health-events.jsonl:2` |
| Snapshot restore xong | `20.5s` | `step:2_restore_snapshot` | `reports/failover-events.jsonl:2` |
| Region phụ ready | `26.7s` | `step:4_wait_ready`, status `ok` | `reports/failover-events.jsonl:4` |
| DNS cutover | `26.7s` | `step:5_dns_cutover` | `reports/failover-events.jsonl:5` |
| RTO đo được | `28.7s` | request đầu tiên `ok:true` từ B | `reports/drill-2-withdr.jsonl:39` |

| Chỉ số | Đo được | Mục tiêu | Verdict |
|---|---:|---:|---|
| RTO — Inference API | `28.7s` | `300s` | PASS |
| RPO — Vector DB | `8.02s` / `4` docs | `300s` | PASS |

## 3. Thành phần RTO

| Thành phần | Giây | Nguồn | Cách giảm |
|---|---:|---|---|
| Health-check detect floor | `15.0s` | interval `5s` × threshold `3` | Giảm interval/threshold, theo dõi flapping |
| Snapshot restore | `20.5s` tới event restore | `reports/failover-events.jsonl:2` | Snapshot gần primary hơn, restore song song |
| GPU pool warm-up | `6.2s` | `reports/failover-events.jsonl:4` | Giữ warm pool |
| DNS/LB TTL cache | `2.0s` | `28.7s - 26.7s` | Giảm TTL hoặc invalidate cache |

## 4. Kết luận

RTO `28.7s` thấp hơn mục tiêu `300s` `271.3s`. RPO `8.02s` thấp hơn mục tiêu `300s` `291.98s`; snapshot thiếu `4` documents.
