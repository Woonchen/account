#!/bin/sh
# 每天備份 SQLite（systemd account-backup.timer 03:30）：用 SQLite 的 backup API 拿一致的快照，保留 30 天
set -eu
HOME_DIR=${ACCOUNT_HOME:-/opt/account}
DEST=$HOME_DIR/backups
mkdir -p "$DEST"
OUT=$DEST/account-$(date +%Y%m%d).db
docker exec account-web python -c "
import sqlite3, sys
src = sqlite3.connect('/data/account.db'); dst = sqlite3.connect('/data/.backup.tmp')
src.backup(dst); dst.close()
ok = sqlite3.connect('/data/.backup.tmp').execute('PRAGMA integrity_check').fetchone()[0]
sys.exit(0 if ok == 'ok' else 1)"
mv "$HOME_DIR/data/.backup.tmp" "$OUT"
find "$DEST" -name 'account-*.db' -mtime +30 -delete
echo "$(date '+%F %T') 備份完成 $OUT ($(du -h "$OUT" | cut -f1))"
