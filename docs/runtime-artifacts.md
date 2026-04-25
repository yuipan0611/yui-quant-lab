# Runtime Artifacts（Runtime 產物）

本文整理 `output/` 與其他本機 runtime artifacts 的用途與 git policy。

## Git Policy

Current `.gitignore` policy:

```gitignore
output/*
!output/.gitkeep
```

中文說明：這個設定符合目前 file-based runtime 設計。generated state、logs、commands、dedupe stores、order records 都不應該 commit。

## Output Directory（`output/` 目錄）

| Path | Producer | Purpose | Should be committed? |
|---|---|---|---:|
| `output/.gitkeep` | Repo placeholder | Keeps `output/` directory present in git. | Yes |
| `output/state.json` | `state_manager.py` | Current trading state、daily counters、cooldown、lock reason、last decision。 | No |
| `output/order_command.json` | `command_writer.py` | Latest actionable command for execution layer。 | No |
| `output/order_command.json.tmp` | `command_writer.py` | Atomic-write temporary file。 | No |
| `output/signal_log.jsonl` | `command_writer.py`, `app.py` | Webhook、decision、fill、route event log。 | No |
| `output/execution_events.jsonl` | `execution_tracker.py` | Execution lifecycle event log。 | No |
| `output/webhook_dedupe.json` | `webhook_dedupe.py` | Recent webhook fingerprints for idempotency。 | No |
| `output/webhook_dedupe.json.lock` | `webhook_dedupe.py` | File lock for dedupe store。 | No |
| `output/fill_dedupe.json` | `state_manager.py` | Processed fill ids/request ids。 | No |
| `output/manual_trade_lock.json` | `telegram_bot.py` | Manual trading lock set by Telegram commands/callbacks。 | No |
| `output/orders/*.json` | `execution_tracker.py` | Per-request order lifecycle records。 | No |
| `output/state.json.corrupt.*` | `state_manager.py` | Archived corrupt state payloads。 | No |

中文說明：`output/` 是 production runtime state 的集中位置，在目前架構中等同於簡化版資料庫（file-based database）。清除或覆寫前，要先確認服務是否正在執行，以及是否還需要這些檔案做 incident debug。

## Why These Files Stay Out of Git（為什麼不進 Git）

Runtime artifacts can contain:

- Live trading state.
- Request ids and broker/client order ids.
- Webhook payloads.
- Operational logs.
- Telegram/manual lock state.
- Environment-specific behavior.

中文說明：這些資料可能包含 live 狀態、交易上下文與環境差異。commit 後可能造成部署時帶入錯誤 state、外洩操作資料，或讓測試依賴本機歷史資料。

## Operational Notes（維運注意事項）

| Concern | Note |
|---|---|
| Worker count | File-based state is safest with Gunicorn `--workers 1`. |
| Atomic writes | `state_manager.py`, `command_writer.py`, and `execution_tracker.py` use atomic-write patterns for key JSON files. |
| Logs | JSONL logs can grow over time; rotation/archival is an operations concern. |
| Dedupe | Replaying identical webhook payloads within TTL can be ignored as duplicate. |
| Local testing | Tests should use temporary output directories or monkeypatch paths where possible. |

中文說明：目前設計依賴檔案寫入，因此 `--workers 1` 是重要維運前提。若未來改成多 worker 或多機部署，需要先改 persistence strategy。

## Safe Cleanup Guidance（安全清理建議）

Before cleaning runtime files:

1. Stop or pause the production service if cleanup affects live state.
2. Preserve `output/state.json` unless intentionally resetting state.
3. Preserve recent `signal_log.jsonl` and `execution_events.jsonl` when debugging an incident.
4. Do not delete `output/orders/` records while fills/order events may still arrive.
5. Keep `output/.gitkeep`.

中文說明：清理 runtime files 時，最危險的是誤刪 state 或 order lifecycle，導致後續 fill/order event 無法正確連結。正式環境清理前應先保留備份。

For local-only development cleanup, it is usually safe to remove generated files under `output/` after confirming no service is running and no debugging session needs the old logs.

## Notes

- 本文件描述的是現況（as-is）。
- 本文件不是重構設計（not target architecture）。
