#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: scripts/provision-windows-signing.sh OUTPUT_DIRECTORY

Creates the Inari code-signing root, publisher certificate, and publisher PFX.
Run this only on the offline signing workstation. OUTPUT_DIRECTORY must be new.
EOF
  exit 2
}

[[ $# -eq 1 ]] || usage

workspace_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_directory="$1"
root_template="${workspace_root}/deploy/windows/signing/root.tpl"
publisher_template="${workspace_root}/deploy/windows/signing/publisher.tpl"

if [[ -e "${output_directory}" ]]; then
  printf 'Refusing to use an existing output directory: %s\n' "${output_directory}" >&2
  exit 1
fi

umask 077
mkdir -p "${output_directory}"

step certificate create \
  "Inari Code Signing Root" \
  "${output_directory}/root.crt" \
  "${output_directory}/root.key" \
  --template "${root_template}" \
  --kty RSA \
  --size 4096 \
  --not-after 87600h

step certificate create \
  "Inari Device Operations" \
  "${output_directory}/publisher.crt" \
  "${output_directory}/publisher.key" \
  --template "${publisher_template}" \
  --ca "${output_directory}/root.crt" \
  --ca-key "${output_directory}/root.key" \
  --kty RSA \
  --size 3072 \
  --not-after 8760h

step certificate verify \
  --roots "${output_directory}/root.crt" \
  "${output_directory}/publisher.crt"

openssl pkcs12 -export \
  -out "${output_directory}/publisher.pfx" \
  -inkey "${output_directory}/publisher.key" \
  -in "${output_directory}/publisher.crt" \
  -certfile "${output_directory}/root.crt" \
  -name "Inari Device Operations"

cat <<EOF

Signing hierarchy created in ${output_directory}

Move root.key to offline storage now. The release environment needs only:
  publisher.pfx
  root.crt

Do not place this directory in the repository or a synchronized folder.
EOF
