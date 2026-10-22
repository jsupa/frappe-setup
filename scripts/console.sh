#!/usr/bin/env bash
CONTAINER="${1:-frappe-backend-1}"
echo "Opening bash console in $CONTAINER..."
docker exec -it "$CONTAINER" bash
