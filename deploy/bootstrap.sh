#!/usr/bin/env bash
# One-time setup of a small VM (~1 GB RAM) for the lite stack. Safe to re-run.
#
#   bash deploy/bootstrap.sh
#
# - 2 GB swapfile (survives reboot) + low swappiness, so a memory spike swaps
#   instead of getting a container OOM-killed
# - Docker network `web`, shared by the reverse proxy and the API
# - App directory the deploy workflow copies compose files into
#
# Does not restart dockerd or touch running containers.
set -euo pipefail

SWAPFILE=${SWAPFILE:-/swapfile}
SWAP_SIZE=${SWAP_SIZE:-2G}
SWAPPINESS=${SWAPPINESS:-10}
NETWORK=${NETWORK:-web}
APP_DIR=${APP_DIR:-$HOME/docker/stock-streaming}

log() { printf '==> %s\n' "$*"; }

if swapon --show=NAME --noheadings | grep -qx "$SWAPFILE"; then
  log "Swap $SWAPFILE already active"
else
  if [[ ! -f $SWAPFILE ]]; then
    log "Creating $SWAP_SIZE swapfile at $SWAPFILE"
    sudo fallocate -l "$SWAP_SIZE" "$SWAPFILE"
    sudo chmod 600 "$SWAPFILE"
    sudo mkswap "$SWAPFILE" >/dev/null
  fi
  log "Enabling swap $SWAPFILE"
  sudo swapon "$SWAPFILE"
fi

if grep -qE "^${SWAPFILE}[[:space:]]" /etc/fstab; then
  log "Swap already in /etc/fstab"
else
  log "Adding swap to /etc/fstab"
  echo "$SWAPFILE none swap sw 0 0" | sudo tee -a /etc/fstab >/dev/null
fi

log "Setting vm.swappiness=$SWAPPINESS"
echo "vm.swappiness=$SWAPPINESS" | sudo tee /etc/sysctl.d/99-swappiness.conf >/dev/null
sudo sysctl -q -p /etc/sysctl.d/99-swappiness.conf

if docker network inspect "$NETWORK" >/dev/null 2>&1; then
  log "Docker network $NETWORK already exists"
else
  log "Creating Docker network $NETWORK"
  docker network create "$NETWORK" >/dev/null
fi

mkdir -p "$APP_DIR"
log "App directory: $APP_DIR"

free -h
