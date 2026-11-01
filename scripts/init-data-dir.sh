#!/usr/bin/env bash
set -euo pipefail

# Vytvorí bind-mount adresáre pre compose.vps.yaml s vlastníkmi podľa UID v kontajneroch
# (frappe=1000, mariadb=999, redis=999). Spúšťa sa raz pred prvým `up`.
DATA_DIR="${1:?usage: init-data-dir.sh <FRAPPE_DATA_DIR>}"

mkdir -p "$DATA_DIR"/{sites,logs,db,redis-queue}

# chown cez docker, aby to nevyžadovalo sudo na hoste
docker run --rm -v "$DATA_DIR":/data alpine:3.20 sh -c '
  chown 1000:0 /data/sites /data/logs && chmod 775 /data/sites /data/logs
  chown 999:999 /data/db
  chown 999:1000 /data/redis-queue
'
ls -ln "$DATA_DIR"
