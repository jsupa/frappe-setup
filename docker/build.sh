#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

IMAGE_NAME="${1:-frappe-crm-helpdesk:v16}"

echo "Building custom Frappe v16 image with CRM, Helpdesk, and Telephony..."
echo "Target tag: $IMAGE_NAME"

docker build \
  --build-arg=FRAPPE_PATH=https://github.com/frappe/frappe \
  --build-arg=FRAPPE_BRANCH=version-16 \
  --secret=id=apps_json,src=apps.json \
  --tag="$IMAGE_NAME" \
  --file=Containerfile \
  .

echo "Build complete: $IMAGE_NAME"
