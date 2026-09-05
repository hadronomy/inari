from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from inari_print_contracts.zpl import LabelContractError, ZplLayout, split_labels

from .models import DocumentAdmissionError

_PDF_MAX_BYTES = 10 * 1024 * 1024
_WORKER_OUTPUT_LIMIT = 64 * 1024


def _run_worker(
    command: list[str], *, input: bytes, timeout: float
) -> subprocess.CompletedProcess[bytes]:
    with subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={"LC_ALL": "C", "TZ": "UTC"},
    ) as process:
        if process.stdin is None or process.stdout is None:
            process.kill()
            raise OSError("The PDF worker pipes are unavailable.")
        input_stream, output_stream = process.stdin, process.stdout

        def send() -> None:
            try:
                input_stream.write(input)
                input_stream.close()
            except BrokenPipeError:
                pass

        with ThreadPoolExecutor(max_workers=2) as pool:
            sent = pool.submit(send)
            received = pool.submit(output_stream.read, _WORKER_OUTPUT_LIMIT + 1)
            try:
                output = received.result(timeout=timeout)
                if len(output) > _WORKER_OUTPUT_LIMIT:
                    raise OSError("The PDF worker exceeded its output limit.")
                sent.result(timeout=1)
                return subprocess.CompletedProcess(
                    command, process.wait(timeout=1), output, b""
                )
            except TimeoutError as error:
                process.kill()
                raise subprocess.TimeoutExpired(command, timeout) from error
            finally:
                if process.poll() is None:
                    process.kill()


class QpdfDocumentValidator:
    def __init__(
        self,
        *,
        runner: Callable[..., subprocess.CompletedProcess[bytes]] = _run_worker,
    ) -> None:
        self._runner = runner

    def validate(self, content: bytes, *, dpi: int = 300) -> None:
        _validate_bytes(content, label="Report PDF", limit=_PDF_MAX_BYTES)
        if not content.startswith(b"%PDF-"):
            raise DocumentAdmissionError(
                "payload_invalid", "Report PDF content is not a PDF document."
            )
        try:
            result = self._runner(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-m",
                    "inari.documents._pdf_worker",
                    str(dpi),
                ],
                input=content,
                timeout=30,
            )
        except FileNotFoundError as exc:
            raise DocumentAdmissionError(
                "service_unavailable",
                "The isolated PDF validator is not installed.",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise DocumentAdmissionError(
                "service_unavailable",
                "The isolated PDF validator did not finish in time.",
            ) from exc
        except OSError as exc:
            raise DocumentAdmissionError(
                "service_unavailable",
                "The isolated PDF validator could not start.",
            ) from exc
        if result.returncode == 3:
            raise DocumentAdmissionError(
                "service_unavailable", "The PDF worker isolation is unavailable."
            )
        if result.returncode != 0:
            raise DocumentAdmissionError(
                "document_policy_rejected",
                "Report PDF content failed structural validation or document policy.",
            )


def validate_label_document(content: bytes, *, layout: ZplLayout) -> None:
    try:
        split_labels(content, layout, max_labels=1)
    except LabelContractError as error:
        raise DocumentAdmissionError("label_data_invalid", str(error)) from error


def _validate_bytes(content: bytes, *, label: str, limit: int) -> None:
    if not isinstance(content, bytes) or not content:
        raise DocumentAdmissionError(
            "payload_invalid", f"{label} content must be non-empty bytes."
        )
    if len(content) > limit:
        raise DocumentAdmissionError(
            "payload_invalid", f"{label} exceeds its {limit}-byte limit."
        )


__all__ = ["QpdfDocumentValidator", "validate_label_document"]
