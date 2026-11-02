#!/usr/bin/env bash
# Runs ON the VM (piped over SSH by .github/workflows/deploy.yml):
#
#   ssh vm 'bash -s -- <image_tag> <app_dir relative to $HOME>' < deploy/remote-deploy.sh
#
# Pins IMAGE_TAG in .env so a manual `docker compose up` later uses the same
# release, pulls the images and recreates only what changed.
set -euo pipefail

tag=$1
cd "$HOME/$2"

if [[ ! -f .env ]]; then
  echo "Missing $PWD/.env: copy deploy/env.vm.example there and fill it in" >&2
  exit 1
fi
{ grep -v '^IMAGE_TAG=' .env || true; echo "IMAGE_TAG=$tag"; } > .env.tmp
mv .env.tmp .env
chmod 600 .env

compose() { docker compose -f docker-compose.prod.yml -f deploy/compose.vm.yml "$@"; }
compose pull api ingestion
compose up -d --no-build --remove-orphans
docker image prune -f >/dev/null
compose ps
