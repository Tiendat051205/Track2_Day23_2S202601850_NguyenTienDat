# Runbook — Region chính down

Runbook này dành cho on-call lúc 3h sáng. Mặc định dùng bare mode và backend
`fs`; chỉ chạy `--auto` trong incident đã được xác nhận. Không đổi DNS thủ công
trước khi region phụ trả readiness `200`.

| # | Bước | Lệnh copy-paste | Biết là xong khi | Ai làm |
|---|---|---|---|---|
| 1 | Xác nhận outage | `python3 chaos/kill_region.py status` | Region A không ready/alive; kiểm tra 3 lần liên tiếp, B vẫn alive | On-call |
| 2 | Mở incident + bấm giờ RTO | `python3 dr/runbook.py --primary a --target b --backend fs --auto` | Log có `thong_bao_incident` trong `reports/runbook-run.jsonl`; RTO clock bắt đầu | Incident commander |
| 3 | Restore state + scale pool | Lệnh ở bước 2 gọi `dr/failover.py` đúng một lần | Log có `2_restore_snapshot` và `3_scale_pool`; RPO hiện cả giây và docs lost | DR operator |
| 4 | Verify target ready | Được thực hiện trong failover; kiểm tra `curl -sS http://127.0.0.1:8002/readyz` | HTTP `200`, `ready:true`, có weights và vector DB | DR operator |
| 5 | DNS/LB cutover | Được thực hiện trong failover; kiểm tra `curl -sS http://127.0.0.1:8080/edge/state` | `active_region` là `b`; log có `5_dns_cutover` | Platform operator |
| 6 | Verify golden signals | Được thực hiện trong runbook: 10 request tới region B | Error rate `0.0%`, p95 `28.0ms` (evidence `reports/runbook-run.jsonl:6`) | SRE |
| 7 | Đo RTO + postmortem | `python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300` | `valid:true`, `rto_verdict:PASS`; cập nhật evidence và postmortem | Incident commander |

## Kết quả tham chiếu của drill gần nhất

- RTO: `28.7s`, mục tiêu `300s`, verdict `PASS`.
- RPO: `8.02s`, mất `4` documents.
- Health-check detect floor: `5s × 3 = 15s`.
- Golden signals: `10/10` request thành công, error rate `0.0%`, p95 `28.0ms`.

## Rollback / failback về region A

Chỉ failback khi cả ba điều kiện đúng:

1. Region A đã được khôi phục và `/readyz` trả HTTP `200` liên tục ít nhất 3 lần.
2. State/model của A đã được đồng bộ và kiểm tra không có lỗi dữ liệu.
3. Incident commander và platform operator cùng xác nhận; không failback tự động
   khi B đang chập chờn để tránh traffic flap giữa hai region.

Thực hiện:

```bash
curl -sS http://127.0.0.1:8001/readyz
printf a > edge/active_region
curl -sS http://127.0.0.1:8080/edge/state
```

Nếu A chưa ready hoặc golden signals lỗi, giữ traffic ở B, không ghi
`edge/active_region`, mở incident tiếp theo và khôi phục A trước khi thử lại.
