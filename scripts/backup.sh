#!/bin/sh
# Nightly backup: database dump + original files. Schedule with cron, e.g.
#   0 2 * * *  cd /path/to/repo && ./scripts/backup.sh /mnt/backup/knowledge-bank
# Keeps the last 14 dumps. Copy BACKUP_DIR off this machine as well; one disk is not a backup.
set -eu
BACKUP_DIR="${1:?usage: backup.sh BACKUP_DIR}"
STAMP=$(date +%Y%m%d-%H%M%S)
mkdir -p "$BACKUP_DIR/db" "$BACKUP_DIR/files"

docker compose exec -T db pg_dump -U kb_admin -d knowledge_bank -Fc > "$BACKUP_DIR/db/knowledge_bank-$STAMP.dump"
# Files are content-addressed and never modified, so an additive copy is enough.
rsync -a ./data/files/ "$BACKUP_DIR/files/"

ls -1t "$BACKUP_DIR"/db/knowledge_bank-*.dump | tail -n +15 | xargs -r rm --
echo "Backup written to $BACKUP_DIR (db/knowledge_bank-$STAMP.dump)"
