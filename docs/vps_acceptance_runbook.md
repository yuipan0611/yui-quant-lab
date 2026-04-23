# VPS 部署驗收 Runbook（完整版）

> 用途：部署後貼上實際輸出，作為最終驗收回報。  
> 架構：TradingView -> Nginx -> Gunicorn -> Flask

使用方式：
1. 依序執行各段指令
2. 將實際輸出貼入對應區塊
3. 勾選通過 / 未通過
4. 最終依第 5 節判定是否可上線

---

## 0) 執行環境

- 執行時間：
- VPS IP / 網域：
- Linux 使用者：
- 專案路徑（預期 `~/yui-quant-lab`）：
- 分支 / 版本（`git rev-parse --short HEAD`）：

---

## 1) 部署步驟執行結果

### 1.1 Python venv 與 gunicorn 安裝

執行指令：

```bash
cd ~/yui-quant-lab
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
pip install gunicorn
```

輸出摘要（貼重點）：

```text
# 在此貼上
```

結果：- [ ] 成功  - [ ] 失敗

---

### 1.2 systemd 服務啟用

執行指令：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now yui-quant-lab.service
sudo systemctl status yui-quant-lab.service --no-pager
```

輸出摘要（貼重點）：

```text
# 在此貼上
```

結果：- [ ] 成功  - [ ] 失敗

---

### 1.3 Nginx 設定與重啟

執行指令：

```bash
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl restart nginx
sudo systemctl status nginx --no-pager
```

輸出摘要（貼重點）：

```text
# 在此貼上
```

結果：- [ ] 成功  - [ ] 失敗

---

### 1.4 UFW 開放 80

執行指令：

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw --force enable
sudo ufw status
```

輸出摘要（貼重點）：

```text
# 在此貼上
```

結果：- [ ] 成功  - [ ] 失敗

---

## 2) 關鍵檢查

### 2.1 Gunicorn 執行路徑

檢查指令：

```bash
systemctl cat yui-quant-lab.service
```

確認重點：

- `ExecStart` 是否為 `.venv/bin/gunicorn`：- [ ] 是  - [ ] 否
- `--bind 127.0.0.1:8000`：- [ ] 是  - [ ] 否
- `app:app`：- [ ] 是  - [ ] 否

---

### 2.2 WorkingDirectory 與 `.env` 載入

檢查指令：

```bash
systemctl cat yui-quant-lab.service
ls -la ~/yui-quant-lab/.env
```

確認重點：

- `WorkingDirectory=/home/<user>/yui-quant-lab`：- [ ] 是  - [ ] 否
- 專案根目錄有 `.env`：- [ ] 是  - [ ] 否
- `ExecStartPre` 有檢查 `.env`：- [ ] 是  - [ ] 否

---

### 2.3 Nginx zones include

檢查指令：

```bash
ls -la /etc/nginx/conf.d/yui-quant-lab-zones.conf
sudo nginx -t
```

確認重點：

- `zones.conf` 在 `/etc/nginx/conf.d/`：- [ ] 是  - [ ] 否
- `nginx -t` 無 `unknown limit_req_zone`：- [ ] 是  - [ ] 否

---

## 3) 驗證結果（可直接判定是否上線）

### 3.1 Service + Port

執行指令：

```bash
sudo systemctl is-active yui-quant-lab.service
sudo systemctl is-active nginx
ss -ltnp | grep -E ':8000|:80'
```

輸出：

```text
# 在此貼上
```

結果：- [ ] 通過  - [ ] 未通過

---

### 3.2 健康檢查

執行指令：

```bash
curl -i http://127.0.0.1/health
curl -i http://127.0.0.1:8000/health
curl -i http://<VPS_PUBLIC_IP>/health
```

預期：上述請求皆為 HTTP `200`，且回應內容可辨識為健康檢查成功，不強制固定 JSON schema。

輸出：

```text
# 在此貼上
```

結果：- [ ] 通過  - [ ] 未通過

---

### 3.3 `/webhook` 驗證

執行指令：

```bash
curl -i -X POST http://127.0.0.1/webhook \
  -H "Content-Type: application/json" \
  -d '{"symbol":"MNQ","signal":"long_breakout","price":20150,"breakout_level":20145,"delta_strength":0.88}'
```

預期：HTTP `200` 且有 JSON 回應。

驗收重點：

- 不只看 API 回應。
- 必須確認 decision engine 有產生結果並寫入日誌。
- 驗收目標是交易決策流程，不是只有 HTTP 成功。

輸出：

```text
# 在此貼上
```

結果：- [ ] 通過  - [ ] 未通過

---

### 3.4 `/tv-webhook` 驗證

執行指令：

```bash
cd ~/yui-quant-lab
source .venv/bin/activate
TVS=$(python -c "from dotenv import dotenv_values; print(dotenv_values('.env').get('TV_WEBHOOK_SECRET',''))")
echo "TVS length: ${#TVS}"
curl -i -X POST http://127.0.0.1/tv-webhook \
  -H "Content-Type: application/json" \
  -d "{\"secret\":\"$TVS\",\"symbol\":\"MNQ\",\"signal\":\"long_breakout\",\"price\":20150,\"breakout_level\":20145,\"delta_strength\":0.88}"
```

預期：HTTP `200` 且 JSON `ok: true`。  
若 `TVS` 為空字串，先檢查 `~/yui-quant-lab/.env` 是否存在且包含 `TV_WEBHOOK_SECRET`（並確認值非空）。  
`TVS length > 0` 代表已成功讀取 `TV_WEBHOOK_SECRET`。

輸出：

```text
# 在此貼上
```

結果：- [ ] 通過  - [ ] 未通過

---

### 3.5 兩個 endpoint 共用 `decision_result` / `request_id` 檢查

適用範圍：`/webhook` 與 `/tv-webhook`（共用 decision pipeline）。

```bash
cd ~/yui-quant-lab
tail -n 20 output/signal_log.jsonl
```

檢查重點：

- 是否有新增 request_id：- [ ] 是  - [ ] 否
- 最新 `decision_result` 是否可辨識出 `decision`：- [ ] 是  - [ ] 否
- 最新 `decision_result` 是否可辨識出 `reason_code`（若有）：- [ ] 是  - [ ] 否

輸出摘要（貼重點）：

```text
# 在此貼上
```

結果：- [ ] 通過  - [ ] 未通過

---

### 3.6 TradingView 實際打入驗證

TradingView Webhook URL：

```text
http://<VPS_PUBLIC_IP>/tv-webhook
```

驗證紀錄：

- TradingView 發送時間：
- TradingView 顯示成功/失敗：
- `journalctl -u yui-quant-lab.service -f` 是否看到請求：
- `output/signal_log.jsonl` 是否出現新 request_id：

結果：- [ ] 通過  - [ ] 未通過

---

### 3.7 壓力 / 重複請求測試（短時間多次 POST）

⚠ 請避免在 production 高頻執行此段測試，以免觸發 rate limit 或被誤判為異常流量。

目的：

- 真實環境會遇到重複 alert、短時間多筆 request、retry。
- 此段用來確認系統不會因重複請求 crash 或出現明顯異常。

執行指令：

```bash
for i in 1 2 3 4 5; do
  curl -s -o /dev/null -w "req$i status=%{http_code}\n" \
    -X POST http://127.0.0.1/webhook \
    -H "Content-Type: application/json" \
    -d '{"symbol":"MNQ","signal":"long_breakout","price":20150,"breakout_level":20145,"delta_strength":0.88}' &
done
wait
```

後續檢查指令：

```bash
journalctl -u yui-quant-lab.service -n 50 --no-pager
cd ~/yui-quant-lab
tail -n 20 output/signal_log.jsonl
```

檢查重點：

- 服務是否仍 `active`：- [ ] 是  - [ ] 否
- Nginx / Gunicorn 是否無明顯 crash：- [ ] 是  - [ ] 否
- `signal_log.jsonl` 是否持續可寫入：- [ ] 是  - [ ] 否
- 是否觀察到 duplicate / dedupe（回應或 log）：- [ ] 是  - [ ] 否

輸出摘要（貼重點）：

```text
# 在此貼上
```

結果：- [ ] 通過  - [ ] 未通過

---

### 3.8 `journalctl` 檢查 Gunicorn 實際 request log

執行指令：

```bash
journalctl -u yui-quant-lab.service -n 200 --no-pager
journalctl -u yui-quant-lab.service -f
```

檢查重點：

- 是否可看到 Gunicorn access log（如 `POST /webhook`、`POST /tv-webhook`）：- [ ] 是  - [ ] 否
- 是否可看到對應時間的 request 與 status code：- [ ] 是  - [ ] 否
- 是否有異常 traceback / error：- [ ] 無  - [ ] 有（請記錄）

輸出摘要（貼重點）：

```text
# 在此貼上
```

結果：- [ ] 通過  - [ ] 未通過

---

### 3.9 Nginx -> Gunicorn 連通性測試（localhost 與 public IP）

執行指令：

```bash
# localhost（經 Nginx）
curl -i http://127.0.0.1/health

# public IP（經 Nginx 對外）
curl -i http://<VPS_PUBLIC_IP>/health
```

檢查重點：

- localhost 路徑是否 HTTP 200：- [ ] 是  - [ ] 否
- public IP 路徑是否 HTTP 200：- [ ] 是  - [ ] 否
- 回應內容是否可辨識為健康檢查成功：- [ ] 是  - [ ] 否

輸出摘要（貼重點）：

```text
# 在此貼上
```

結果：- [ ] 通過  - [ ] 未通過

---

### 3.10 Webhook 回應時間檢查（避免 TradingView timeout）

執行指令：

```bash
curl -sS -o /dev/null -w "webhook_status=%{http_code} total_time=%{time_total}s\n" \
  -X POST http://127.0.0.1/webhook \
  -H "Content-Type: application/json" \
  -d '{"symbol":"MNQ","signal":"long_breakout","price":20150,"breakout_level":20145,"delta_strength":0.88}'

cd ~/yui-quant-lab
source .venv/bin/activate
TVS=$(python -c "from dotenv import dotenv_values; print(dotenv_values('.env').get('TV_WEBHOOK_SECRET',''))")
curl -sS -o /dev/null -w "tv_webhook_status=%{http_code} total_time=%{time_total}s\n" \
  -X POST http://127.0.0.1/tv-webhook \
  -H "Content-Type: application/json" \
  -d "{\"secret\":\"$TVS\",\"symbol\":\"MNQ\",\"signal\":\"long_breakout\",\"price\":20150,\"breakout_level\":20145,\"delta_strength\":0.88}"
```

檢查重點：

- `/webhook` 是否回 `200` 且時間在可接受範圍（建議 < 3 秒，以避免 TradingView webhook timeout）：- [ ] 是  - [ ] 否
- `/tv-webhook` 是否回 `200` 且時間在可接受範圍（建議 < 3 秒，以避免 TradingView webhook timeout）：- [ ] 是  - [ ] 否
- 是否觀察到明顯 timeout 風險（例如長時間無回應）：- [ ] 否  - [ ] 是

輸出摘要（貼重點）：

```text
# 在此貼上
```

結果：- [ ] 通過  - [ ] 未通過

---

### 3.11 `output/` 資料夾權限檢查

執行指令：

```bash
cd ~/yui-quant-lab
ls -ld output
ls -l output | head -n 20
```

檢查重點：

- `yui-quant-lab.service` 的 `User` 對 `output/` 有讀寫權限：- [ ] 是  - [ ] 否
- webhook 後 `output/signal_log.jsonl` 可正常新增內容：- [ ] 是  - [ ] 否
- 是否有 Permission denied / 寫檔失敗跡象：- [ ] 無  - [ ] 有

輸出摘要（貼重點）：

```text
# 在此貼上
```

結果：- [ ] 通過  - [ ] 未通過

---

### 3.12 Nginx `error.log` 檢查

執行指令：

```bash
sudo tail -n 100 /var/log/nginx/error.log
sudo tail -f /var/log/nginx/error.log
```

檢查重點：

- 是否出現 upstream connect / timeout / 502 / 504 錯誤：- [ ] 無  - [ ] 有
- 是否出現 `unknown limit_req_zone` 或 config 相關錯誤：- [ ] 無  - [ ] 有
- 若有錯誤，是否已修正並重測通過：- [ ] 是  - [ ] 否

輸出摘要（貼重點）：

```text
# 在此貼上
```

結果：- [ ] 通過  - [ ] 未通過

---

### 3.13 VPS Provider Security Group / 防火牆規則檢查

檢查項目（於 VPS 供應商主控台）：

- Inbound 規則是否允許 `TCP 80`（來源依需求設定）：- [ ] 是  - [ ] 否
- Inbound 規則是否允許 `TCP 22`（管理用 SSH）：- [ ] 是  - [ ] 否
- 若未開 443，是否已確認目前只走 HTTP 驗收：- [ ] 是  - [ ] 否

佐證（貼主控台截圖重點或文字紀錄）：

```text
# 在此貼上
```

結果：- [ ] 通過  - [ ] 未通過

---

### 3.14 安全部署順序（5 步，避免 CHDIR / 啟動失敗）

目的：避免 `yui-quant-lab.service` 在檔案尚未到位時啟動，造成 `status=200/CHDIR` 或 `status=1/FAILURE`。

#### Step 1) 先停服務（避免半套檔案被讀到）

```bash
sudo systemctl stop yui-quant-lab.service
sudo systemctl status yui-quant-lab.service --no-pager
```

檢查重點：

- service 已停止（`inactive`）再進行檔案更新：- [ ] 是  - [ ] 否

#### Step 2) 佈署檔案後，先確認三個必要條件

```bash
ls -ld /home/yui/yui-bot
ls -l /home/yui/yui-bot/.env
ls -l /home/yui/yui-bot/.venv/bin/gunicorn
```

檢查重點：

- `WorkingDirectory` 目錄存在：- [ ] 是  - [ ] 否
- `.env` 存在：- [ ] 是  - [ ] 否
- `gunicorn` 可執行檔存在：- [ ] 是  - [ ] 否

#### Step 3) 權限與 service 設定一致性檢查

```bash
sudo chown -R yui:yui /home/yui/yui-bot
systemctl cat yui-quant-lab.service --no-pager
```

檢查重點：

- 專案路徑 owner 為 `yui:yui`：- [ ] 是  - [ ] 否
- `WorkingDirectory` 與實際部署路徑一致：- [ ] 是  - [ ] 否
- `ExecStart` 指向正確 venv gunicorn：- [ ] 是  - [ ] 否

#### Step 4) 啟動前預檢（改過 service 才需要 reload）

```bash
sudo systemctl daemon-reload
sudo systemctl start yui-quant-lab.service
```

檢查重點：

- 無 `Start request repeated too quickly`：- [ ] 是  - [ ] 否
- 無 `Changing to the requested working directory failed`：- [ ] 是  - [ ] 否

#### Step 5) 啟動後驗收（健康檢查 + log）

```bash
sudo systemctl status yui-quant-lab.service --no-pager
curl -i http://127.0.0.1:8000/health
journalctl -u yui-quant-lab.service --since "10 minutes ago" --no-pager
```

檢查重點：

- service 狀態為 `active (running)`：- [ ] 是  - [ ] 否
- `/health` 回應 `200`：- [ ] 是  - [ ] 否
- log 無關鍵錯誤（CHDIR / FAILURE / timeout）：- [ ] 是  - [ ] 否

輸出摘要（貼重點）：

```text
# 在此貼上
```

結果：- [ ] 通過  - [ ] 未通過

---

### 3.15 本機改了交易引擎後，怎麼同步到 VPS（白話版）

目的：你在本機改完 `decision_engine.py` 之後，安全地把同一版更新到 VPS，避免「本機是新版本、VPS 還在跑舊版本」。

#### 情境 A) 這次只改 `decision_engine.py`（最快）

1. 在本機上傳單一檔案到 VPS

```powershell
scp .\decision_engine.py root@<VPS_PUBLIC_IP>:/home/yui/yui-bot/decision_engine.py
```

2. 在 VPS 修正檔案擁有者（避免權限問題）

```bash
chown yui:yui /home/yui/yui-bot/decision_engine.py
```

3. 重新啟動服務讓新檔案生效

```bash
sudo systemctl restart yui-quant-lab.service
sudo systemctl status yui-quant-lab.service --no-pager
curl -sS http://127.0.0.1:8000/health
```

#### 情境 B) 這次改了多個檔案（建議用打包更新）

1. 本機先打包（把你要上線的檔案包成一包）

```powershell
tar -czf yui-quant-lab-upload.tgz app.py decision_engine.py command_writer.py execution_tracker.py state_manager.py telegram_bot.py webhook_dedupe.py time_utils.py requirements.txt scripts deploy docs
```

2. 上傳壓縮包到 VPS

```powershell
scp .\yui-quant-lab-upload.tgz root@<VPS_PUBLIC_IP>:/home/yui/
```

3. 在 VPS 解壓覆蓋到專案目錄

```bash
sudo -u yui bash -lc 'cd /home/yui && tar -xzf yui-quant-lab-upload.tgz -C yui-bot'
```

4. 若 `requirements.txt` 有變，再補安裝套件

```bash
sudo -u yui /home/yui/yui-bot/.venv/bin/pip install -r /home/yui/yui-bot/requirements.txt
```

5. 重啟服務並驗收

```bash
sudo systemctl restart yui-quant-lab.service
sudo systemctl status yui-quant-lab.service --no-pager
curl -sS http://127.0.0.1:8000/health
```

#### 最少要做的驗證（每次更新後）

- 服務是 `active (running)`。
- `/health` 回 `200`。
- 用 hash 確認本機與 VPS 版本一致（尤其是 `decision_engine.py`）：

```powershell
# 本機
python -c "import hashlib;print(hashlib.sha256(open('decision_engine.py','rb').read()).hexdigest())"
```

```bash
# VPS
sha256sum /home/yui/yui-bot/decision_engine.py
```

判斷方式：兩邊雜湊值完全一樣，就代表兩邊檔案內容完全一致。

#### 建議的安全做法（可選但推薦）

- 更新前先備份 `/home/yui/yui-bot`（至少保留最近 1 份）。
- 一次只做一件事：先同步檔案，再重啟，再驗收，不要混在一起。
- 驗收沒過就先回滾備份，再查 log（`journalctl -u yui-quant-lab.service --since "10 minutes ago" --no-pager`）。

---

## 4) 異常快速定位（Symptom -> Check）

- TradingView 顯示 fail  
  - 檢查 `nginx` 狀態與配置：`sudo systemctl status nginx --no-pager`、`sudo nginx -t`
  - 檢查防火牆與安全群組：`sudo ufw status`、VPS provider Security Group inbound `TCP 80`
  - 檢查 public IP 是否可達：`curl -i http://<VPS_PUBLIC_IP>/health`

- `/webhook` 有回應但沒有 `decision_result`  
  - 檢查 `output/signal_log.jsonl` 最新事件：`tail -n 50 ~/yui-quant-lab/output/signal_log.jsonl`
  - 檢查 service log 是否有 gate/例外：`journalctl -u yui-quant-lab.service -n 200 --no-pager`
  - 檢查 payload 是否符合策略條件與 state gate

- 有 request 但 `signal_log.jsonl` 沒更新  
  - 檢查 `output/` 權限：`ls -ld ~/yui-quant-lab/output`
  - 檢查 service 使用者：`systemctl cat yui-quant-lab.service`
  - 檢查是否有寫檔錯誤：`journalctl -u yui-quant-lab.service -n 200 --no-pager`

- latency > 3 秒  
  - 量測回應時間：第 3.10 節 `curl -w total_time`
  - 檢查 decision 流程是否阻塞：`journalctl -u yui-quant-lab.service -f`
  - 檢查 Nginx upstream 異常：`sudo tail -n 100 /var/log/nginx/error.log`

---

## 5) 最終結論

- 服務是否正常常駐（systemd + nginx）：- [ ] 是  - [ ] 否
- 外部 webhook 是否可達（公網 `:80`）：- [ ] 是  - [ ] 否
- TradingView 實際打入是否成功：- [ ] 是  - [ ] 否
- 是否可進 production：- [ ] 可以  - [ ] 不可
- 若不可，阻塞項目：
- 下一步建議（例如補 TLS/443、監控）：

最低上線條件：
- systemd active
- nginx active
- `/health` 可由 public IP 存取
- `/tv-webhook` 成功回應
- `signal_log.jsonl` 有新增 request_id

以上條件全部成立才可判定為 production-ready。
