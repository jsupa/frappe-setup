#!/usr/bin/env bash
set -euo pipefail

# Konzistentná záloha (DB dump + súbory) pred restic behom.
# Výstup ide do sites/<SITE>/private/backups, teda do FRAPPE_DATA_DIR/sites, ktorý restic zálohuje.
SITE_NAME="${1:-menucka.local}"
KEEP_DAYS="${2:-3}"
STACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose --project-directory "$STACK_DIR" -f "$STACK_DIR/compose.yaml" -f "$STACK_DIR/compose.vps.yaml")

echo "Frappe backup: $SITE_NAME"
"${COMPOSE[@]}" exec -T backend bench --site "$SITE_NAME" backup --with-files

# Lokálne držíme len posledné dni, históriu drží restic
"${COMPOSE[@]}" exec -T backend find "sites/$SITE_NAME/private/backups" -type f -mtime +"$KEEP_DAYS" -delete
echo "Frappe backup OK"
