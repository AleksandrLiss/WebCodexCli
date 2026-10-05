#!/usr/bin/env bash
# Run explicitly as root on the NEW server. Official Docker apt repositories.
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
  echo 'Run with sudo or as root.' >&2
  exit 1
fi
if command -v docker >/dev/null 2>&1; then
  docker info >/dev/null
  echo 'Docker already exists; packages and configuration were not modified.'
  exit 0
fi
. /etc/os-release
case "$ID" in
  ubuntu|debian) ;;
  *) echo 'Supported installer OS: Ubuntu or Debian.' >&2; exit 1 ;;
esac
for package in docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc; do
  status="$(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null || true)"
  if [[ "$status" == 'installed' ]]; then
    echo "Conflicting package $package exists. Resolve the conflict manually first." >&2
    exit 1
  fi
done
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl python3 git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
os_codename="${UBUNTU_CODENAME:-$VERSION_CODENAME}"
os_arch="$(dpkg --print-architecture)"
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/$ID
Suites: $os_codename
Components: stable
Architectures: $os_arch
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y --no-install-recommends docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
docker info >/dev/null
echo 'Docker installed and enabled at boot. Next: python3 scripts/manage.py init-env'
