# 記帳本

我和家人共用的記帳網頁：收入、支出、分類，手機上用。Python（FastAPI）＋ SQLite，跑在 Oracle Cloud VM（`ubuntu@217.142.229.67`）的 Docker 裡，對外由 finflow 的 Caddy 提供 HTTPS。

- **一本帳、多個帳號**：每個人用自己的帳號登入，看到同一份資料，每筆記錄顯示是誰記的
- **金額**：整數新台幣；分類分收入、支出兩組。有記錄的分類不能刪，只能封存（舊記錄照常顯示，新增時不會出現）
- **登入**：帳號只能用指令建立。密碼只存 pbkdf2 雜湊（30 萬次）；登入後 7 天不用再登入；同一個 IP 30 秒內輸錯 3 次封鎖 24 小時（重啟容器解除）；改密碼後，這個帳號舊的登入全部失效
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
app/db.py        資料表、預設分類
app/auth.py      密碼雜湊、session cookie、輸錯封鎖（做法同 finflow deploy/serve.py）
app/cli.py       帳號管理
app/static/      前端（純 HTML/CSS/JS）
tests/           pytest
deploy/          docker-compose.yml、Caddy 站台範本與安裝腳本、備份腳本與 systemd timer
```
