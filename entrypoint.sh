#!/bin/sh
set -e

if [ "$USE_SQLITE" != "True" ]; then
  echo "Waiting for database at ${POSTGRES_HOST:-localhost}:${POSTGRES_PORT:-5432}..."
  python - <<'PYEOF'
import os
import socket
import time

host = os.environ.get("POSTGRES_HOST", "localhost")
port = int(os.environ.get("POSTGRES_PORT", "5432"))

for _ in range(30):
    try:
        with socket.create_connection((host, port), timeout=2):
            break
    except OSError:
        time.sleep(1)
else:
    raise SystemExit(f"Database at {host}:{port} not reachable after 30s")
PYEOF
  echo "Database is available."
fi

python manage.py migrate --noinput

if [ "$#" -gt 0 ]; then
  exec "$@"
elif [ "$DEBUG" = "True" ]; then
  exec python manage.py runserver 0.0.0.0:8000
else
  exec gunicorn sprout_warehouse.wsgi:application --bind 0.0.0.0:8000
fi
