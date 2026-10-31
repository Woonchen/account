#!/bin/sh
# 把記帳本的站台加進 finflow 的 Caddy：產生站台設定 → caddy validate → caddy reload（不重啟容器，finflow 不會中斷）。
# 驗證失敗就還原，不會讓 Caddy 停掉。
set -eu
cd "$(dirname "$0")"
. ./.env
: "${ACCOUNT_DOMAIN:?請在 deploy/.env 設定 ACCOUNT_DOMAIN}"
SITES=${FINFLOW_HOME:-/opt/finflow}/caddy/sites
CADDY=${CADDY_CONTAINER:-deploy-caddy-1}
mkdir -p "$SITES"
[ -f "$SITES/account.caddy" ] && cp "$SITES/account.caddy" "$SITES/account.caddy.bak"
sed "s/__ACCOUNT_DOMAIN__/$ACCOUNT_DOMAIN/" account.caddy > "$SITES/account.caddy"
if docker exec "$CADDY" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
  docker exec -w /etc/caddy "$CADDY" caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
  rm -f "$SITES/account.caddy.bak"
  echo "已啟用 https://$ACCOUNT_DOMAIN（第一次會花幾十秒申請憑證，docker logs $CADDY 看進度）"
else
  docker exec "$CADDY" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile || true
  if [ -f "$SITES/account.caddy.bak" ]; then mv "$SITES/account.caddy.bak" "$SITES/account.caddy"; else rm -f "$SITES/account.caddy"; fi
  echo "Caddy 設定驗證失敗，已還原" >&2
  exit 1
fi
