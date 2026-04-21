from __future__ import annotations

import argparse
import os
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True, slots=True)
class TimingSample:
    name: str
    samples_ms: tuple[float, ...]

    @property
    def minimum_ms(self) -> float:
        return min(self.samples_ms)

    @property
    def mean_ms(self) -> float:
        return statistics.mean(self.samples_ms)

    @property
    def median_ms(self) -> float:
        return statistics.median(self.samples_ms)


@dataclass(frozen=True, slots=True)
class ImportTiming:
    cumulative_us: int
    self_us: int
    module: str


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark Inari CLI import and startup latency."
    )
    parser.add_argument(
        "--module",
        default="inari.cli",
        help="Python module to import with -X importtime.",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=7,
        help="Wall-clock samples per command.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=30,
        help="Number of importtime rows to print.",
    )
    parser.add_argument(
        "--include-uv",
        action="store_true",
        help="Also benchmark `uv run --no-sync inari --help` when uv is available.",
    )
    args = parser.parse_args(argv)

    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if args.top < 1:
        parser.error("--top must be at least 1")

    print(f"Python: {sys.executable}")
    print(f"Module: {args.module}")
    print()
    _print_wall_clock_benchmarks(args.runs, include_uv=args.include_uv)
    print()
    _print_importtime_summary(args.module, top=args.top)
    return 0


def _print_wall_clock_benchmarks(runs: int, *, include_uv: bool) -> None:
    print("Wall-clock startup")
    print("------------------")
    commands = _benchmark_commands(include_uv=include_uv)
    name_width = max(len(name) for name, _ in commands)
    for name, command in commands:
        sample = _time_command(name, command, runs=runs)
        samples = ", ".join(f"{value:.1f}" for value in sample.samples_ms)
        print(
            f"{sample.name:<{name_width}} "
            f"min={sample.minimum_ms:7.1f}ms "
            f"median={sample.median_ms:7.1f}ms "
            f"mean={sample.mean_ms:7.1f}ms "
            f"samples=[{samples}]"
        )


def _benchmark_commands(*, include_uv: bool) -> list[tuple[str, tuple[str, ...]]]:
    python = sys.executable
    commands: list[tuple[str, tuple[str, ...]]] = [
        ("python -c pass", (python, "-c", "pass")),
        ("python import typer", (python, "-c", "import typer")),
        ("python import inari.cli", (python, "-c", "import inari.cli")),
        ("python -m inari --help", (python, "-m", "inari", "--help")),
    ]
    console_script = _console_script_path("inari")
    if console_script is not None:
        commands.append(("console inari --help", (str(console_script), "--help")))
    if include_uv and shutil.which("uv") is not None:
        commands.append(
            ("uv run inari --help", ("uv", "run", "--no-sync", "inari", "--help"))
        )
    return commands


def _console_script_path(name: str) -> Path | None:
    executable = f"{name}.exe" if os.name == "nt" else name
    candidate = Path(sys.executable).with_name(executable)
    if candidate.exists():
        return candidate
    resolved = shutil.which(name)
    return Path(resolved) if resolved is not None else None


def _time_command(
    name: str,
    command: Sequence[str],
    *,
    runs: int,
) -> TimingSample:
    samples = []
    for _ in range(runs):
        started = time.perf_counter()
        subprocess.run(
            list(command),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        samples.append((time.perf_counter() - started) * 1000)
    return TimingSample(name=name, samples_ms=tuple(samples))


def _print_importtime_summary(module: str, *, top: int) -> None:
    print("Import-time profile")
    print("-------------------")
    completed = subprocess.run(
        [sys.executable, "-X", "importtime", "-c", f"import {module}"],
        capture_output=True,
        check=True,
        text=True,
    )
    rows = _parse_importtime(completed.stderr)
    if not rows:
        print("No importtime rows were captured.")
        return
    for row in sorted(rows, key=lambda item: item.cumulative_us, reverse=True)[:top]:
        print(
            f"{row.cumulative_us / 1000:9.1f}ms cum "
            f"{row.self_us / 1000:9.1f}ms self  {row.module}"
        )


def _parse_importtime(value: str) -> list[ImportTiming]:
    rows = []
    for line in value.splitlines():
        if not line.startswith("import time:") or "self [us]" in line:
            continue
        parts = line.removeprefix("import time:").split("|")
        if len(parts) < 3:
            continue
        try:
            self_us = int(parts[0].strip())
            cumulative_us = int(parts[1].strip())
        except ValueError:
            continue
        rows.append(
            ImportTiming(
                cumulative_us=cumulative_us,
                self_us=self_us,
                module=parts[2].strip(),
            )
        )
    return rows


if __name__ == "__main__":
    raise SystemExit(main())
