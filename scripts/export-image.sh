#!/usr/bin/env bash
# Optional transfer of an already-built image; no application volumes are included.
set -euo pipefail
if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo 'Usage: scripts/export-image.sh OUTPUT.tar.gz [IMAGE=codex-web:local]' >&2
  exit 1
fi
umask 077
output="$1"
image="${2:-codex-web:local}"
if [[ -e "$output" ]]; then
  echo 'Output already exists; choose another path.' >&2
  exit 1
fi
temp="${output}.partial"
trap 'rm -f -- "$temp"' EXIT
docker image inspect "$image" >/dev/null
docker image save "$image" | gzip -1 > "$temp"
mv -- "$temp" "$output"
sha256sum -- "$output" > "${output}.sha256"
echo "Image exported: $output. Load with: docker load -i IMAGE.tar.gz"
