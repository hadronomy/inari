"""Stage the Inari Odoo addon and its locked Python dependency for OCI."""

from __future__ import annotations

import argparse
import ast
import csv
import json
from importlib.metadata import Distribution
import runpy
import shutil
import subprocess
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def stage(output: Path) -> None:
    output = output.resolve()
    if output.exists():
        raise ValueError(f"Artifact output already exists: {output}")

    addons = output / "addons"
    python = output / "python"
    addons.mkdir(parents=True)
    python.mkdir()

    shutil.copytree(
        ROOT / "packages/odoo-inari/inari_devices",
        addons / "inari_devices",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
    )
    requirements = subprocess.run(
        [
            "uv",
            "export",
            "--frozen",
            "--no-dev",
            "--package",
            "inari-print-contracts",
            "--no-emit-workspace",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    requirements_path = output / "requirements.txt"
    requirements_path.write_text(requirements)
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--target",
            str(python),
            "--python-version",
            "3.13",
            "--python-platform",
            "x86_64-unknown-linux-gnu",
            "--only-binary",
            ":all:",
            "--require-hashes",
            "--no-deps",
            "--requirements",
            str(requirements_path),
        ],
        cwd=ROOT,
        check=True,
    )

    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--target",
            str(python),
            "--no-deps",
            str(ROOT / "packages/print-contracts"),
        ],
        cwd=ROOT,
        check=True,
    )

    dist_info = next(python.glob("inari_print_contracts-*.dist-info"))
    # These installer files expose the checkout path and build time.
    generated = {"direct_url.json", "uv_cache.json"}
    for name in generated:
        (dist_info / name).unlink(missing_ok=True)
    record = dist_info / "RECORD"
    with record.open(newline="") as source:
        rows = list(csv.reader(source))
    with record.open("w", newline="") as destination:
        writer = csv.writer(destination)
        writer.writerows(row for row in rows if Path(row[0]).name not in generated)

    addon = ast.literal_eval((addons / "inari_devices/__manifest__.py").read_text())
    agent = tomllib.loads((ROOT / "packages/agent/pyproject.toml").read_text())[
        "project"
    ]
    controller = tomllib.loads((ROOT / "Cargo.toml").read_text())["workspace"][
        "package"
    ]
    compatibility = {
        "schema_version": 1,
        "addon_version": addon["version"],
        "odoo_version_range": ">=19.0,<20.0",
        "agent_version_range": f"={agent['version']}",
        "controller_version_range": f"={controller['version']}",
        "print_contracts_version": Distribution.at(dist_info).version,
        "local_agent_contract_major": 1,
        "managed_workload_contract_major": 1,
        "device_authority_contract": "inari.device-authority.v1",
        "gateway_protocol_version": runpy.run_path(
            str(ROOT / "packages/agent/inari/core/version.py")
        )["GATEWAY_PROTOCOL_VERSION"],
    }
    (output / "compatibility.json").write_text(
        json.dumps(compatibility, indent=2, sort_keys=True) + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    stage(args.output)


if __name__ == "__main__":
    main()
