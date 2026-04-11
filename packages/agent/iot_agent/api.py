from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from .dependencies import get_printer_service
from .models import (
    OperationResponse,
    PrintJobRequest,
    PrinterCommandKind,
    PrinterCommandRequest,
    PrinterDirectoryResponse,
    PrinterDirectorySummaryResponse,
    PrinterResourceResponse,
    PrinterResponse,
    ServiceDescriptorResponse,
    SystemStatusResponse,
)
from .print_jobs import PrintContentKind
from .printer_service import PrinterService
from .printers import PrintJobResult, PrinterTransport

SERVICE_NAME = "IoT Agent"
API_VERSION = "1.6.0a2"

router = APIRouter()
system_router = APIRouter(prefix="/system", tags=["system"])
devices_router = APIRouter(prefix="/devices", tags=["devices"])
printing_router = APIRouter(tags=["printing"])

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


router.include_router(system_router)
router.include_router(devices_router)
router.include_router(printing_router)


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
