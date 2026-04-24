# ADR: Webhook Dedupe 與 Deployment 設計決策

- Status: Accepted
- Date: 2026-04-25
- Scope: `webhook` / `tv-webhook` 在單機 VPS + Gunicorn 多 worker 下的去重一致性與部署策略

## 1. Context

- 服務以 Gunicorn 執行，配置 `-w 2`，屬於多 process worker 模式。
- TradingView webhook 在網路層與上游行為下可能重送，短時間重複 payload 是可預期情境。
- 目前部署型態為單機 VPS（非多節點、非多容器叢集）。
- 現階段不引入 Redis / DB 作為 dedupe 協調層，以降低部署複雜度與外部依賴。

## 2. Problem

- 原實作採 `is_duplicate(...)` + `remember(...)` 分離流程，存在典型 TOCTOU（time-of-check to time-of-use）風險。
- 在 Gunicorn 多 worker 情境下，兩個 worker 可能同時通過 `is_duplicate`，再各自進入主流程。
- 風險結果是重複 decision，並帶來未來重複下單的潛在風險。

## 3. Decision

- 採用 `check_and_remember(endpoint, payload) -> bool` 作為 dedupe 核心 API（語意等同 claim-or-duplicate）。
- 使用檔案型共享 store：`output/webhook_dedupe.json`。
- 使用 `fcntl.flock` + 獨立 `.lock` 檔（例如 `output/webhook_dedupe.json.lock`）保護跨 process 臨界區。
- 以「先 claim，再處理」模型實作：
  - 非 duplicate：在鎖內完成 claim（寫入 TTL）後才進入主流程。
  - duplicate：直接返回 duplicate response，不進入主流程。

## 4. Trade-offs

優點：

- 可防止單機多 worker 下同 payload 被重複處理。
- 無需外部基礎設施（Redis/DB），部署與維運成本低。

缺點：

- claim 後若主流程處理失敗，TTL 視窗內同 payload 會被視為 duplicate，無法立即重試。
- 僅提供單機安全，不具多機全域一致性。
- 每次請求有 file lock + file IO 成本，存在額外延遲。

## 5. Alternatives Considered

- Redis（分散式鎖 / 原子操作）：一致性更強，但增加外部依賴與維運成本。
- DB unique constraint（或交易式 claim）：可提供更強一致性與查詢能力，但導入成本較高。
- memory-only 去重：已淘汰，無法覆蓋多 worker process。
- queue-based（例如先入佇列再單點消費）：未採用，現階段屬過度設計。

## 6. Current Scope

- 單機 VPS 部署。
- Gunicorn 多 worker（process-level concurrency）。
- 以 file lock + file store 提供目前需求下可接受的一致性保證。

## 7. Future Evolution

- 若走向多 VPS / horizontal scaling，改用 Redis / DB 作為共享去重與協調層。
- 視吞吐需求引入 webhook queue（Kafka / RabbitMQ 等）以解耦接收與處理。
- 可演進為兩階段狀態模型（`pending -> committed`）以降低 claim 後失敗的重試限制。

## Critical Assumptions

- 本設計僅保證「單機、多 process」環境下的 dedupe 正確性。
- 不適用於多台 VPS / 多容器部署（會產生重複處理風險）。
- 若系統擴展為多節點，必須改為 Redis / DB-based 去重。
