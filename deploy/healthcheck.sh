#!/usr/bin/env bash
# Wait until the API answers with the expected version.
#
#   deploy/healthcheck.sh https://api.example.com/health sha-1a2b3c4 [timeout_sec]
#
# Matching the version (not just HTTP 200) proves the new containers are the
# ones serving traffic, not the previous release that was still up.
set -euo pipefail

url=$1
version=$2
timeout=${3:-90}

deadline=$((SECONDS + timeout))
body=""
while ((SECONDS < deadline)); do
  body=$(curl -fsS --max-time 5 "$url" 2>/dev/null || true)
  if [[ $body == *"\"version\":\"$version\""* ]]; then
    echo "Healthy: $body"
    exit 0
  fi
  sleep 3
done
echo "Not healthy after ${timeout}s (last response: ${body:-none})" >&2
exit 1
