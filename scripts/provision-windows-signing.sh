#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: scripts/provision-windows-signing.sh OUTPUT_DIRECTORY

Creates the publisher-owned code-signing root, Inari issuing CA, publisher
certificate, and publisher PFX.
Run this only on the offline signing workstation. OUTPUT_DIRECTORY must be new.
EOF
  exit 2
}

[[ $# -eq 1 ]] || usage

workspace_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_directory="$1"
root_template="${workspace_root}/deploy/windows/signing/root.tpl"
issuer_template="${workspace_root}/deploy/windows/signing/issuer.tpl"
publisher_template="${workspace_root}/deploy/windows/signing/publisher.tpl"
root_name="Pablo Hernández Jiménez Code Signing Root CA"
issuer_name="Inari Code Signing Issuing CA"
publisher_name="Pablo Hernández Jiménez"

if [[ -e "${output_directory}" ]]; then
  printf 'Refusing to use an existing output directory: %s\n' "${output_directory}" >&2
  exit 1
fi

umask 077
mkdir -p "${output_directory}"

# step derives an implicit SAN from the positional subject. The reserved ASCII
# names keep CSR construction valid while the templates intentionally omit SANs
# from the resulting code-signing certificates.
step certificate create \
  "${root_name}" \
  "${output_directory}/root.crt" \
  "${output_directory}/root.key" \
  --template "${root_template}" \
  --san hadronomy-code-signing-root.invalid \
  --kty RSA \
  --size 4096 \
  --not-after 87600h

step certificate create \
  "${issuer_name}" \
  "${output_directory}/issuer.crt" \
  "${output_directory}/issuer.key" \
  --template "${issuer_template}" \
  --san inari-code-signing-issuer.invalid \
  --ca "${output_directory}/root.crt" \
  --ca-key "${output_directory}/root.key" \
  --kty RSA \
  --size 4096 \
  --not-after 43800h

step certificate create \
  "${publisher_name}" \
  "${output_directory}/publisher.crt" \
  "${output_directory}/publisher.key" \
  --template "${publisher_template}" \
  --san inari-publisher.invalid \
  --ca "${output_directory}/issuer.crt" \
  --ca-key "${output_directory}/issuer.key" \
  --kty RSA \
  --size 3072 \
  --not-after 8760h

step certificate verify \
  --roots "${output_directory}/root.crt" \
  "${output_directory}/issuer.crt"

step certificate verify \
  --roots "${output_directory}/issuer.crt" \
  "${output_directory}/publisher.crt"

openssl verify \
  -CAfile "${output_directory}/root.crt" \
  -untrusted "${output_directory}/issuer.crt" \
  "${output_directory}/publisher.crt"

openssl pkcs12 -export \
  -out "${output_directory}/publisher.pfx" \
  -inkey "${output_directory}/publisher.key" \
  -in "${output_directory}/publisher.crt" \
  -certfile "${output_directory}/issuer.crt" \
  -name "${publisher_name} — Inari" \
  -certpbe AES-256-CBC \
  -keypbe AES-256-CBC \
  -macalg sha256

cat <<EOF

Signing hierarchy created in ${output_directory}

Move root.key and issuer.key to separate offline storage now. The release
environment needs only:
  publisher.pfx
  root.crt

Do not place this directory in the repository or a synchronized folder.
EOF
