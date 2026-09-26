#!/usr/bin/env bash
set -euo pipefail

artifact_root="$(mktemp -d)"
trap 'rm -rf "$artifact_root"' EXIT

python deploy/odoo/build_artifact.py "$artifact_root/artifact"
test -f "$artifact_root/artifact/addons/inari_devices/__manifest__.py"
PYTHONPATH="$artifact_root/artifact/python" python -c 'import importlib.metadata as metadata; import inari_print_contracts, rfc8785; assert metadata.version("inari-print-contracts"); assert metadata.version("rfc8785")'
