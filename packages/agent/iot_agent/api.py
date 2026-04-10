from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from .config import AgentSettings
from .dependencies import get_printer_service, get_settings
from .exceptions import PrinterServiceError
from .models import (
    ActionResponse,
    DrawerRequest,
    HealthResponse,
    PrintHtmlRequest,
    PrintReceiptRequest,
    PrinterInfoResponse,
    PrintersResponse,
    TestPrintRequest,
)
from .printer_service import PrinterService
from .printers import PrinterTransport

router = APIRouter(tags=["printing"])
PrinterServiceDependency = Annotated[PrinterService, Depends(get_printer_service)]
SettingsDependency = Annotated[AgentSettings, Depends(get_settings)]


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


@router.post("/print_receipt", response_model=ActionResponse)
def print_receipt(
    request: PrintReceiptRequest,
    printer_service: PrinterServiceDependency,
) -> ActionResponse:
    result = printer_service.print_odoo_receipt(
        request.receipt,
        printer_name=request.printer_name,
        transport=request.mode,
        document_name="Odoo Receipt",
    )

    if request.open_drawer:
        printer_service.open_cash_drawer(printer_name=request.printer_name)

    return ActionResponse(
        printer_name=result.printer_name,
        driver=result.printer.driver_key,
        mode=result.transport,
        bytes_written=result.bytes_written,
        detail="Receipt sent successfully.",
    )


@router.post("/print_html", response_model=ActionResponse)
def print_html(
    request: PrintHtmlRequest,
    settings: SettingsDependency,
    printer_service: PrinterServiceDependency,
) -> ActionResponse:
    if not settings.html_print_enabled:
        raise PrinterServiceError(
            "HTML_MODE_DISABLED",
            "HTML printing is disabled.",
        )

    result = printer_service.print_html_document(
        request.html,
        printer_name=request.printer_name,
        title="Odoo HTML Document",
    )

    if request.open_drawer:
        printer_service.open_cash_drawer(printer_name=request.printer_name)

    return ActionResponse(
        printer_name=result.printer_name,
        driver=result.printer.driver_key,
        mode=result.transport,
        bytes_written=result.bytes_written,
        detail="HTML print job submitted.",
    )


@router.post("/open_drawer", response_model=ActionResponse)
def open_drawer(
    request: DrawerRequest,
    printer_service: PrinterServiceDependency,
) -> ActionResponse:
    result = printer_service.open_cash_drawer(printer_name=request.printer_name)
    return ActionResponse(
        printer_name=result.printer_name,
        driver=result.printer.driver_key,
        mode=result.transport,
        bytes_written=result.bytes_written,
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

    return ActionResponse(
        printer_name=result.printer_name,
        driver=result.printer.driver_key,
        mode=result.transport,
        bytes_written=result.bytes_written,
        detail=(
            "RAW receipt test sent."
            if result.transport is PrinterTransport.RAW
            else "Windows text/document print test submitted."
        ),
    )
