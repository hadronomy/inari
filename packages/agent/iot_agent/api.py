from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from .dependencies import get_printer_service
from .models import (
    ActionResponse,
    DrawerRequest,
    HealthResponse,
    PrintHtmlRequest,
    PrintJobRequest,
    PrintReceiptRequest,
    PrinterInfoResponse,
    PrintersResponse,
    TestPrintRequest,
)
from .printer_service import PrinterService
from .printers import PrintJobResult
from .printers import PrinterTransport

router = APIRouter(tags=["printing"])
PrinterServiceDependency = Annotated[PrinterService, Depends(get_printer_service)]


@router.get("/health", response_model=HealthResponse)
def health(printer_service: PrinterServiceDependency) -> HealthResponse:
    printers = printer_service.list_printers()
    default_printer = next((printer.name for printer in printers if printer.is_default), None)
    return HealthResponse(
        default_printer=default_printer,
        printer_count=len(printers),
        drawer_supported=any(printer.supports_cash_drawer for printer in printers),
    )


@router.get("/printers", response_model=PrintersResponse)
def printers(printer_service: PrinterServiceDependency) -> PrintersResponse:
    printer_list = printer_service.list_printers()
    return PrintersResponse(
        printers=[
            PrinterInfoResponse(
                name=printer.name,
                driver=printer.driver_key,
                is_default=printer.is_default,
                mode=printer.preferred_transport,
                supports_raw=printer.supports_raw,
                supports_documents=printer.supports_documents,
                supports_cash_drawer=printer.supports_cash_drawer,
            )
            for printer in printer_list
        ]
    )


@router.post("/print", response_model=ActionResponse)
def print_job(
    request: PrintJobRequest,
    printer_service: PrinterServiceDependency,
) -> ActionResponse:
    return _to_action_response(
        printer_service.print_job(request.to_domain()),
        detail="Print job submitted.",
    )


@router.post("/print_receipt", response_model=ActionResponse)
def print_receipt(
    request: PrintReceiptRequest,
    printer_service: PrinterServiceDependency,
) -> ActionResponse:
    return _to_action_response(
        printer_service.print_job(request.to_domain()),
        detail="Receipt sent successfully.",
    )


@router.post("/print_html", response_model=ActionResponse)
def print_html(
    request: PrintHtmlRequest,
    printer_service: PrinterServiceDependency,
) -> ActionResponse:
    return _to_action_response(
        printer_service.print_job(request.to_domain()),
        detail="HTML print job submitted.",
    )


@router.post("/open_drawer", response_model=ActionResponse)
def open_drawer(
    request: DrawerRequest,
    printer_service: PrinterServiceDependency,
) -> ActionResponse:
    return _to_action_response(
        printer_service.open_cash_drawer(printer_name=request.printer_name),
        detail="Drawer pulse sent.",
    )


@router.post("/test_print", response_model=ActionResponse)
def test_print(
    request: TestPrintRequest,
    printer_service: PrinterServiceDependency,
) -> ActionResponse:
    result = printer_service.print_test_ticket(
        printer_name=request.printer_name,
        transport=request.mode,
    )

    return _to_action_response(
        result,
        detail=(
            "RAW receipt test sent."
            if result.transport is PrinterTransport.RAW
            else "Windows text/document print test submitted."
        ),
    )


def _to_action_response(result: PrintJobResult, *, detail: str) -> ActionResponse:
    return ActionResponse(
        printer_name=result.printer_name,
        driver=result.printer.driver_key,
        mode=result.transport,
        bytes_written=result.bytes_written,
        detail=detail,
    )
