from __future__ import annotations

from fastapi import APIRouter, Depends

from .config import AgentSettings, get_settings
from .dependencies import get_printer_service
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
from .printer_service import PrinterService, PrinterTransport

router = APIRouter(tags=["printing"])


@router.get("/health", response_model=HealthResponse)
def health(printer_service: PrinterService = Depends(get_printer_service)) -> HealthResponse:
    printers = printer_service.list_printers()
    default_printer = next((printer.name for printer in printers if printer.is_default), None)
    return HealthResponse(
        default_printer=default_printer,
        printer_count=len(printers),
        drawer_supported=any(printer.supports_raw for printer in printers),
    )


@router.get("/printers", response_model=PrintersResponse)
def printers(printer_service: PrinterService = Depends(get_printer_service)) -> PrintersResponse:
    printer_list = printer_service.list_printers()
    return PrintersResponse(
        printers=[
            PrinterInfoResponse(
                name=printer.name,
                is_default=printer.is_default,
                mode=printer.preferred_transport,
                supports_raw=printer.supports_raw,
                supports_documents=printer.supports_documents,
            )
            for printer in printer_list
        ]
    )


@router.post("/print_receipt", response_model=ActionResponse)
def print_receipt(
    request: PrintReceiptRequest,
    printer_service: PrinterService = Depends(get_printer_service),
) -> ActionResponse:
    printer = printer_service.print_odoo_receipt(
        request.receipt,
        printer_name=request.printer_name,
        transport=request.mode,
        document_name="Odoo Receipt",
    )

    if request.open_drawer:
        printer_service.open_cash_drawer(printer_name=request.printer_name)

    return ActionResponse(
        printer_name=printer.name,
        mode=printer.preferred_transport,
        detail="Receipt sent successfully.",
    )


@router.post("/print_html", response_model=ActionResponse)
def print_html(
    request: PrintHtmlRequest,
    settings: AgentSettings = Depends(get_settings),
    printer_service: PrinterService = Depends(get_printer_service),
) -> ActionResponse:
    if not settings.html_print_enabled:
        raise PrinterServiceError(
            "HTML_MODE_DISABLED",
            "HTML printing is disabled.",
        )

    printer = printer_service.print_html_document(
        request.html,
        printer_name=request.printer_name,
        title="Odoo HTML Document",
    )

    if request.open_drawer:
        printer_service.open_cash_drawer(printer_name=request.printer_name)

    return ActionResponse(
        printer_name=printer.name,
        mode=printer.preferred_transport,
        detail="HTML print job submitted.",
    )


@router.post("/open_drawer", response_model=ActionResponse)
def open_drawer(
    request: DrawerRequest,
    printer_service: PrinterService = Depends(get_printer_service),
) -> ActionResponse:
    printer = printer_service.open_cash_drawer(printer_name=request.printer_name)
    return ActionResponse(
        printer_name=printer.name,
        mode=printer.preferred_transport,
        detail="Drawer pulse sent.",
    )


@router.post("/test_print", response_model=ActionResponse)
def test_print(
    request: TestPrintRequest,
    printer_service: PrinterService = Depends(get_printer_service),
) -> ActionResponse:
    printer = printer_service.print_test_ticket(
        printer_name=request.printer_name,
        transport=request.mode,
    )

    return ActionResponse(
        printer_name=printer.name,
        mode=printer.preferred_transport,
        detail=(
            "RAW receipt test sent."
            if printer.preferred_transport is PrinterTransport.RAW
            else "Windows text/document print test submitted."
        ),
    )