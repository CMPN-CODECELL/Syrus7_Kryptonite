| Stage | Users | Requests | Failures | p50 ms | p95 ms | p99 ms | req/s | 429s | Jobs done | Jobs/min | Job p50 / p95 s | CPU avg / max % | RSS max MB |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ramp-01 | 1 | 44 | 0 | 5.0 | 10.0 | 31.0 | 1.14 | 0 | 0 | 0.0 | - / - | 1.3 / 27.5 | 121.4 |
| ramp-10 | 10 | 559 | 0 | 5.0 | 15.0 | 46.0 | 14.68 | 0 | 20 | 25.8 | 2.8 / 3.6 | 25.4 / 100.9 | 123.7 |
| ramp-25 | 25 | 1231 | 0 | 6.0 | 26.0 | 110.0 | 24.3 | 0 | 22 | 22.7 | 9.1 / 14.8 | 27.4 / 95.2 | 127.6 |
| ramp-50 | 50 | 2250 | 0 | 8.0 | 100.0 | 700.0 | 44.87 | 16 | 17 | 16.3 | 25.0 / 30.2 | 31.4 / 87.3 | 133.8 |
| soak-10 | 10 | 2527 | 0 | 6.0 | 14.0 | 59.0 | 14.18 | 0 | 85 | 27.5 | 2.8 / 5.8 | 25.8 / 99.7 | 133.7 |

| Scenario | OK | Key numbers |
|---|---|---|
| queue_saturation_429 | yes | `{"submitted": 30, "accepted": 8, "rejected_429": 22, "other_codes": [], "retry_after_present": true, "retry_after_values": ["10"], "burst_s": 0.16, "accepted_all_succeeded": true, "drain_s": 0.0}` |
| sse_fanout | yes | `{"clients": 40, "job_status": "succeeded", "events_per_client": 41, "all_identical": true, "matches_persisted_snapshot": true, "strictly_increasing": true, "first_event_ms_p50": 6206.4, "first_event_ms_p95": 7102.3, "wall_s": 11.4}` |
| cancel_storm | yes | `{"jobs": 8, "cancel_calls": 24, "codes": {"200": 24}, "final_statuses": {"cancelled": 8}, "all_terminal_s": 0.38}` |
| dropped_sse_connections | yes | `{"dropped_connections": 120, "job_status": "succeeded", "health_ms_p95_after": 2.9, "api_threads_before": 32, "api_threads_after": 4, "replay_events_after": 39}` |
| missing_cached_result | yes | `{"replay_before_delete": {"code": 200, "cached_from": "run-20261008-c8d76f25"}, "result_after_delete": 410, "report_after_delete": 410, "resubmit": {"code": 202, "cached_from": null, "status": "succeeded"}}` |
| worker_kill_restart | yes | `{"killed_supervisor_pid": 40944, "worker_ready_again_s": 0.43, "saw_not_ready": true, "running_job": {"status": "failed", "code": "interrupted_by_restart"}, "queued_job_after_restart": "succeeded", "orphans_of_old_tree": []}` |
| provider_outage_mocked | yes | `{"error": {"check_codes": [502], "expected": 502, "leaked_token": false, "health_ok": true, "health_ms_p95": 101.6}, "hang": {"check_codes": [504], "expected": 504, "leaked_token": false, "health_ok": true, "health_ms_p95": 57.6}, "health_ms_p95_baseline": 5.7}` |
