#!/usr/bin/env bash
set -euo pipefail

# Run from WSL against the same checkout and env file as Windows Django.
mode=${1:?Usage: bash scripts/celery-wsl.sh catalog|webhooks|beat ENV_FILE}
export OPM_ENV_FILE=${2:?Specify .env.demo or .env explicitly}
case "$mode" in catalog|webhooks|beat) ;; *) echo 'Unknown mode' >&2; exit 2 ;; esac
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root/backend"
python=${OPM_PYTHON:-"$HOME/.venvs/opm/bin/python"}
# WSL NAT gateway is the Windows host. Override for mirrored/remote networking.
export DATABASE_HOST=${DATABASE_HOST:-$(ip route show default | awk '{print $3; exit}')}
"$python" manage.py check_services
if [[ "$mode" == beat ]]; then
    exec "$python" -m celery -A config.celery beat --loglevel=INFO \
        --schedule "/tmp/opm-${OPM_ENV_FILE//[^a-zA-Z0-9]/_}-beat"
else
    exec "$python" -m celery -A config.celery worker -Q "$mode" \
        --concurrency=1 --loglevel=INFO --hostname="${mode}@%h"
fi
