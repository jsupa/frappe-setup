#!/usr/bin/env bash
set -euo pipefail

SITE_NAME="${1:-menucka.local}"
CONTAINER="${2:-frappe-backend-1}"

echo "Enabling scheduler on site: $SITE_NAME..."
docker exec "$CONTAINER" bench --site "$SITE_NAME" enable-scheduler
echo "Scheduler enabled successfully!"
