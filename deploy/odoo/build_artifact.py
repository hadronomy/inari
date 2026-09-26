"""Stage the Inari Odoo addon and its locked Python dependency for OCI."""

from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    stage(args.output)


if __name__ == "__main__":
    main()
