#!/usr/bin/env bash
# Emulator-free sync smoke test (just watch-smoke).
# POSTs a representative watchface payload (activity + heart rate + sleep) to the
# running GUAI server using the token from server/.env. Requires the server up
# (`just run`) and reachable at http://127.0.0.1:8000.
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root

# shellcheck disable=SC1091
set -a
. server/.env
set +a

NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
TODAY=$(date -u +%Y-%m-%d)

echo "POST /api/sync (device=smoke, token from server/.env) ..."
curl -s -w "\nHTTP %{http_code}\n" \
  -X POST "http://127.0.0.1:8000/api/sync" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${GUAI_SYNC_TOKEN}" \
  --data "{\"collected_at\":\"${NOW}\",\"device\":\"smoke\",\"activity\":{\"steps\":8124,\"distance_meters\":5310.5,\"active_seconds\":2210,\"resting_kcal\":1480.2,\"active_kcal\":320.4},\"heart_rates\":[{\"at\":\"${NOW}\",\"bpm\":62}],\"sleep\":[{\"night_date\":\"${TODAY}\",\"sleep_seconds\":25200,\"restful_seconds\":18000}]}"
