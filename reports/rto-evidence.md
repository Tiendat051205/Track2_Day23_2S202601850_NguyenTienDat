# RTO/RPO Evidence — Lab 23

Số liệu lấy từ drill mới nhất trong log. Drill 2 đã phục hồi sang region B; cảnh báo là DNS cutover được thực hiện trước khi health checker ghi nhận A `UNHEALTHY`.

## 1. Drill 1 — không có DR (baseline)

| Chỉ số | Giá trị | Cách đo | Evidence |
|---|---:|---|---|
| t_outage | `2026-08-25T09:22:16` | chaos kill region A | `chaos/chaos-events.jsonl:1` |
| Request fail đầu tiên | `+0.1s` | request `ok:false` đầu tiên | `reports/drill-1-nodr.jsonl:17` |
| Request thành công sau đó | không có | không có `ok:true` sau outage | `reports/drill-1-nodr.jsonl:32` |
| RTO | `NO_RECOVERY` | đo từ timestamp request | `reports/drill-1-nodr.jsonl:32` |

## 2. Drill 2 — có DR

| Mốc | +giây từ t_outage | Cách đo | Evidence |
|---|---:|---|---|
| t_outage | `0.0s` | `action:kill`, region A | `chaos/chaos-events.jsonl:17` |
| User thấy lỗi đầu tiên | `0.1s` | `ok:false` đầu tiên | `reports/drill-2-withdr.jsonl:25` |
| Health check phát hiện | `15.0s` | A chuyển `UNHEALTHY` | `reports/health-events.jsonl:3` |
| Snapshot restore xong | `4.4s` | `step:2_restore_snapshot` | `reports/failover-events.jsonl:47` |
| Region phụ ready | `4.5s` | `step:4_wait_ready`, status `ok` | `reports/failover-events.jsonl:49` |
| DNS cutover | `4.5s` | `step:5_dns_cutover` | `reports/failover-events.jsonl:50` |
| RTO đo được | `10.3s` | request đầu tiên `ok:true` từ B | `reports/drill-2-withdr.jsonl:30` |

| Chỉ số | Đo được | Mục tiêu | Verdict |
|---|---:|---:|---|
| RTO — Inference API | `10.3s` | `300s` | PASS |
| RPO — Vector DB | `20.03s` / `10` docs | `300s` | PASS |

## 3. Thành phần RTO

| Thành phần | Giây | Nguồn | Cách giảm |
|---|---:|---|---|
| Health-check detect floor | `15.0s` | interval `5s` × threshold `3` | Giảm interval/threshold, theo dõi flapping |
| Snapshot restore | `4.4s` tới event restore | `reports/failover-events.jsonl:47` | Snapshot gần primary hơn, restore song song |
| GPU pool warm-up | `0.0s` trong drill | target đã `full` và ready | Giữ warm pool |
| DNS/LB TTL cache | `5.8s` | `10.3s - 4.5s` | Giảm TTL hoặc invalidate cache |

## 4. Kết luận

RTO `10.3s` thấp hơn mục tiêu `300s` `289.7s`. RPO `20.03s` thấp hơn mục tiêu `300s` `279.97s`; snapshot thiếu `10` documents.