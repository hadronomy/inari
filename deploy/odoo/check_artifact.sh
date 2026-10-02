#!/usr/bin/env bash
set -euo pipefail

artifact_root="$(mktemp -d)"
trap 'rm -rf "$artifact_root"' EXIT

python deploy/odoo/build_artifact.py "$artifact_root/artifact"
test -f "$artifact_root/artifact/addons/inari_devices/__manifest__.py"
PYTHONPATH="$artifact_root/artifact/python" python - "$artifact_root/artifact" <<'PY'
import ast
import importlib.metadata as metadata
import json
from pathlib import Path
import sys

import inari_print_contracts
import rfc8785

root = Path(sys.argv[1])
compatibility = json.loads((root / "compatibility.json").read_text())
addon = ast.literal_eval((root / "addons/inari_devices/__manifest__.py").read_text())
assert compatibility["schema_version"] == 1
assert compatibility["addon_version"] == addon["version"]
assert compatibility["print_contracts_version"] == metadata.version("inari-print-contracts")
assert compatibility["odoo_version_range"] == ">=19.0,<20.0"
assert compatibility["local_agent_contract_major"] == 1
assert compatibility["managed_workload_contract_major"] == 1
assert compatibility["device_authority_contract"] == "inari.device-authority.v1"
assert compatibility["gateway_protocol_version"] == "2026-09-06"
assert compatibility["agent_version_range"].startswith("=")
assert compatibility["controller_version_range"].startswith("=")
assert metadata.version("rfc8785")
PY
