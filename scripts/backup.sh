#!/usr/bin/env bash
# SmileFlow backup: database + uploaded schedule files.
#
# PHI NOTE (docs/05): backups contain patient data. BACKUP_DIR must be an
# encrypted volume, retention-managed, and covered by the same access
# controls as the live system. Schedule this daily (cron/systemd timer) and
# rehearse a restore — an untested backup is not a backup.
#
# Usage: DATABASE_URL=... UPLOADS_DIR=var/uploads BACKUP_DIR=/backups ./scripts/backup.sh
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:?set BACKUP_DIR to an encrypted destination}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$BACKUP_DIR/smileflow-$STAMP"
mkdir -p "$DEST"

DATABASE_URL="${DATABASE_URL:-sqlite:///smileflow.db}"
case "$DATABASE_URL" in
  postgres*|postgresql*)
    pg_dump --no-owner --format=custom --dbname="$DATABASE_URL" \
      --file="$DEST/db.pgdump"
    ;;
  sqlite:///*)
    DB_FILE="${DATABASE_URL#sqlite:///}"
    sqlite3 "$DB_FILE" ".backup '$DEST/db.sqlite3'"
    ;;
  *)
    echo "unsupported DATABASE_URL scheme" >&2; exit 1
    ;;
esac

UPLOADS_DIR="${UPLOADS_DIR:-var/uploads}"
if [ -d "$UPLOADS_DIR" ]; then
  tar -czf "$DEST/uploads.tar.gz" -C "$(dirname "$UPLOADS_DIR")" "$(basename "$UPLOADS_DIR")"
fi

echo "backup written to $DEST"

# Retention: keep the newest 30 backups.
ls -1dt "$BACKUP_DIR"/smileflow-* 2>/dev/null | tail -n +31 | xargs -r rm -rf
