from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from .dependencies import get_printer_service
from .models import (
    ActionResponse,
    DrawerRequest,
    HealthResponse,
    LegacyPrintJobRequest,
    OperationResponse,
    PrintHtmlRequest,
    PrintJobRequest,
    PrintReceiptRequest,
    PrinterCommandKind,
    PrinterCommandRequest,
    PrinterDirectoryResponse,
    PrinterDirectorySummaryResponse,
    PrinterInfoResponse,
    PrinterResourceResponse,
    PrinterResponse,
    PrintersResponse,
    ServiceDescriptorResponse,
    SystemStatusResponse,
    TestPrintRequest,
)
from .print_jobs import PrintContentKind
from .printer_service import PrinterService
from .printers import PrintJobResult, PrinterTransport

SERVICE_NAME = "IoT Agent"
API_VERSION = "1.6.0a1"

router = APIRouter()
system_router = APIRouter(prefix="/system", tags=["system"])
devices_router = APIRouter(prefix="/devices", tags=["devices"])
printing_router = APIRouter(tags=["printing"])
legacy_router = APIRouter(tags=["legacy"])

PrinterServiceDependency = Annotated[PrinterService, Depends(get_printer_service)]


@system_router.get("/status", response_model=SystemStatusResponse)
def system_status(printer_service: PrinterServiceDependency) -> SystemStatusResponse:
    printers = list(printer_service.list_printers())
    return SystemStatusResponse(
        service=ServiceDescriptorResponse(name=SERVICE_NAME, version=API_VERSION),
        printers=PrinterDirectorySummaryResponse.from_printers(printers),
        supported_content_kinds=tuple(PrintContentKind),
        supported_printer_commands=tuple(PrinterCommandKind),
    )


@devices_router.get("/printers", response_model=PrinterDirectoryResponse)
def list_printers(printer_service: PrinterServiceDependency) -> PrinterDirectoryResponse:
    printers = list(printer_service.list_printers())
    return PrinterDirectoryResponse(
        printers=[PrinterResponse.from_domain(printer) for printer in printers],
        summary=PrinterDirectorySummaryResponse.from_printers(printers),
    )


@devices_router.get("/printers/{printer_name}", response_model=PrinterResourceResponse)
def get_printer(printer_name: str, printer_service: PrinterServiceDependency) -> PrinterResourceResponse:
    return PrinterResourceResponse(
        printer=PrinterResponse.from_domain(printer_service.get_printer_info(printer_name))
    )


@printing_router.post("/print-jobs", response_model=OperationResponse)
def submit_print_job(
    request: PrintJobRequest,
    printer_service: PrinterServiceDependency,
) -> OperationResponse:
    return _to_operation_response(
        operation="print_job",
        result=printer_service.print_job(request.to_domain()),
        message="Print job submitted.",
    )


@printing_router.post("/printer-commands", response_model=OperationResponse)
def execute_printer_command(
    request: PrinterCommandRequest,
    printer_service: PrinterServiceDependency,
) -> OperationResponse:
    printer_name = request.target.printer_name
    command = request.command

    if command.kind == PrinterCommandKind.OPEN_CASH_DRAWER:
        result = printer_service.open_cash_drawer(printer_name=printer_name)
        return _to_operation_response(
            operation=command.kind,
            result=result,
            message="Cash drawer pulse sent.",
        )

    if command.kind == PrinterCommandKind.PRINT_TEST_PAGE:
        result = printer_service.print_test_ticket(
            printer_name=printer_name,
            transport=command.transport,
        )
        return _to_operation_response(
            operation=command.kind,
            result=result,
            message=(
                "RAW receipt test sent."
                if result.transport is PrinterTransport.RAW
                else "Windows text/document print test submitted."
            ),
        )

    if command.kind == PrinterCommandKind.FEED_LINES:
        return _to_operation_response(
            operation=command.kind,
            result=printer_service.feed_lines(command.count, printer_name=printer_name),
            message="Paper feed command sent.",
        )

    if command.kind == PrinterCommandKind.FEED_DOTS:
        return _to_operation_response(
            operation=command.kind,
            result=printer_service.feed_dots(command.count, printer_name=printer_name),
            message="Fine paper feed command sent.",
        )

    return _to_operation_response(
        operation=command.kind,
        result=printer_service.cut_paper(
            printer_name=printer_name,
            mode=command.mode,
        ),
        message="Paper cut command sent.",
    )


@legacy_router.get("/health", response_model=HealthResponse, deprecated=True)
def legacy_health(printer_service: PrinterServiceDependency) -> HealthResponse:
    printers = list(printer_service.list_printers())
    return HealthResponse(
        default_printer=next((printer.name for printer in printers if printer.is_default), None),
        printer_count=len(printers),
        drawer_supported=any(printer.supports_cash_drawer for printer in printers),
    )


@legacy_router.get("/printers", response_model=PrintersResponse, deprecated=True)
def legacy_printers(printer_service: PrinterServiceDependency) -> PrintersResponse:
    printers = list(printer_service.list_printers())
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
            for printer in printers
        ]
    )


@legacy_router.post("/print", response_model=ActionResponse, deprecated=True)
def legacy_print_job(
    request: LegacyPrintJobRequest,
    printer_service: PrinterServiceDependency,
) -> ActionResponse:
    return _to_action_response(
        printer_service.print_job(request.to_domain()),
        detail="Print job submitted.",
    )


@legacy_router.post("/print_receipt", response_model=ActionResponse, deprecated=True)
def legacy_print_receipt(
    request: PrintReceiptRequest,
    printer_service: PrinterServiceDependency,
) -> ActionResponse:
    return _to_action_response(
        printer_service.print_job(request.to_domain()),
        detail="Receipt sent successfully.",
    )


@legacy_router.post("/print_html", response_model=ActionResponse, deprecated=True)
def legacy_print_html(
    request: PrintHtmlRequest,
    printer_service: PrinterServiceDependency,
) -> ActionResponse:
    return _to_action_response(
        printer_service.print_job(request.to_domain()),
        detail="HTML print job submitted.",
    )


@legacy_router.post("/open_drawer", response_model=ActionResponse, deprecated=True)
def legacy_open_drawer(
    request: DrawerRequest,
    printer_service: PrinterServiceDependency,
) -> ActionResponse:
    return _to_action_response(
        printer_service.open_cash_drawer(printer_name=request.printer_name),
        detail="Drawer pulse sent.",
    )


@legacy_router.post("/test_print", response_model=ActionResponse, deprecated=True)
def legacy_test_print(
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


router.include_router(system_router)
router.include_router(devices_router)
router.include_router(printing_router)
router.include_router(legacy_router)


def _to_operation_response(
    *,
    operation: str,
    result: PrintJobResult,
    message: str | None = None,
) -> OperationResponse:
    return OperationResponse.from_domain(
        operation=operation,
        result=result,
        message=message,
    )


def _to_action_response(result: PrintJobResult, *, detail: str) -> ActionResponse:
    return ActionResponse(
        printer_name=result.printer_name,
        driver=result.printer.driver_key,
        mode=result.transport,
        bytes_written=result.bytes_written,
        detail=detail,
    )
