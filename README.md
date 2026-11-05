# 記帳本

我和家人共用的記帳網頁：收入、支出、分類，手機上用。Python（FastAPI）＋ SQLite，跑在 Oracle Cloud VM（`ubuntu@217.142.229.67`）的 Docker 裡，對外由 finflow 的 Caddy 提供 HTTPS。

- **先建帳本再記帳**：可以有多本帳本（家用、旅行…），左上角切換；每本帳本有自己的付款／收款人（預設 成、圓、寶貝基金）和分類，在「帳本設定」管理。有記錄的帳本、成員、分類不能刪，只能封存（舊記錄照常顯示）
- **一個月一本帳本**：新增帳本時名稱預設「YYYY 年 M 月」（這個月的已經有了就給下個月），付款／收款人與分類沿用目前帳本
- **儀表板**：這本帳本的總收入、總支出、結餘、筆數，每位付款／收款人的合計、各分類金額；「預算剩餘」用指針儀表顯示成、圓、寶貝基金各自還剩多少 % 預算（左端 0%、右端 100%；剩不到 20% 變橘色標「快用完」，超支指針停在 0 並顯示超支 %，沒設預算顯示「未設定預算」）。已結算的帳本顯示結算當時的數字
- **記帳**：日期、金額、誰付款／收款、分類、備註；列出整本帳本的記錄，可依付款／收款人篩選
- **全域預算**：帳本選單裡的「全域設定（預算）」，設定成、圓、寶貝基金每本帳本的預算，所有帳本共用；也在這裡用 ↑ 調整三位的排列順序，所有帳本的記帳、儀表板、結算都照這個順序（其他付款人排在後面，在帳本設定調整）
- **結算**：每本帳本的「結算」頁，花費＝記在成、圓、寶貝基金名下的支出（收款不算），剩餘＝預算 − 花費（負數表示超支）。平常是用目前預算試算；按「完成結算」會存下當時的預算與花費，並鎖定這本帳本的記錄（不能新增、修改、刪除），之後改全域預算也不影響；「取消結算」會解鎖並回到試算
- **多個帳號共用**：所有帳號看到同一批帳本，每筆記錄顯示是誰記的
- **版面**：電腦是左側欄＋表格；手機（寬度 < 900px）是上方帳本切換、底部分頁、右下「＋」
- **登入**：帳號只能用指令建立。密碼只存 pbkdf2 雜湊（30 萬次）；登入後 7 天不用再登入；同一個 IP 30 秒內輸錯 3 次封鎖 24 小時（重啟容器解除）；改密碼後，這個帳號舊的登入全部失效
- **深色／淺色模式**：預設跟著系統（自動），也可以手動選淺色或深色；電腦在左側欄下方、手機在右上角帳號選單。選擇只存在那台裝置的瀏覽器
- **手機主畫面**：Safari「分享 → 加入主畫面」／Chrome「加到主畫面」

## 本機開發

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
export ACCOUNT_DB=data/dev.db ACCOUNT_SESSION_SECRET=dev ACCOUNT_INSECURE_COOKIE=1
.venv/bin/python -m app.cli add-user nick Nick
.venv/bin/uvicorn app.main:app --reload     # http://127.0.0.1:8000
```

`ACCOUNT_INSECURE_COOKIE=1` 只給本機 http 用；正式環境 cookie 一定帶 `Secure`。

## 部署（VM）

架構：`account-web` 容器（8000，不對到主機）接在 finflow 的 Docker 網路 `deploy_default` 上，finflow 的 `caddy` 容器 import `/opt/finflow/caddy/sites/*.caddy`，把記帳本的網域轉過來。

**第一次**（需要 finflow 已經有 `import /etc/caddy/sites/*.caddy` 與對應的掛載，見 finflow README「對外入口」）：

1. DuckDNS 新增子網域，指到 `217.142.229.67`（Security List 的 80/443 已經開了）
2. 在 VM 上：
   ```bash
   sudo mkdir -p /opt/account && sudo chown ubuntu:ubuntu /opt/account
   mkdir -p /opt/account/data /opt/account/backups
   git clone https://github.com/Woonchen/account.git /opt/account/src
   cd /opt/account/src
   cp deploy/.env.example deploy/.env   # 填 ACCOUNT_SESSION_SECRET、ACCOUNT_DOMAIN
   docker compose -f deploy/docker-compose.yml up -d --build
   docker compose -f deploy/docker-compose.yml run --rm web python -m app.cli add-user nick Nick
   deploy/install-caddy.sh               # 產生站台設定、驗證、reload Caddy（finflow 不中斷）
   sudo cp deploy/systemd/account-backup.* /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now account-backup.timer
   ```

**更新程式**（在 `/opt/account/src`）：

```bash
git pull && docker compose -f deploy/docker-compose.yml up -d --build
```

**常用指令**（在 `/opt/account/src`，`C="docker compose -f deploy/docker-compose.yml run --rm web python -m app.cli"`）：

| 做什麼 | 指令 |
|---|---|
| 新增帳號 | `$C add-user 帳號 顯示名稱` |
| 改密碼 | `$C passwd 帳號` |
| 列出帳號 | `$C list-users` |
| 刪除沒記過帳的帳號 | `$C del-user 帳號` |
| 看登入紀錄 | `docker logs account-web` |
| 解除所有封鎖 | `docker restart account-web` |
| 換網域 | 改 `deploy/.env` 的 `ACCOUNT_DOMAIN`，再跑 `deploy/install-caddy.sh` |

## 備份與還原

`account-backup.timer` 每天 03:30 跑 `deploy/backup.sh`：用 SQLite backup API 取一致的快照、做 `integrity_check`，存到 `/opt/account/backups/account-YYYYMMDD.db`，保留 30 天。手動備份：直接跑 `deploy/backup.sh`。

還原：
```bash
docker stop account-web
cp /opt/account/backups/account-YYYYMMDD.db /opt/account/data/account.db
rm -f /opt/account/data/account.db-wal /opt/account/data/account.db-shm
docker start account-web
```

## 檔案

```
app/main.py      API 與頁面
app/db.py        資料表、schema 遷移（PRAGMA user_version）、新帳本的預設成員與分類
app/auth.py      密碼雜湊、session cookie、輸錯封鎖（做法同 finflow deploy/serve.py）
app/cli.py       帳號管理
app/static/      前端（純 HTML/CSS/JS）
tests/           pytest
deploy/          docker-compose.yml、Caddy 站台範本與安裝腳本、備份腳本與 systemd timer
```
