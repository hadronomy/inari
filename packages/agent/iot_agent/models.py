from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .printers import PrinterTransport


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ErrorResponse(APIModel):
    ok: Literal[False] = False
    code: str
    message: str


class HealthResponse(APIModel):
    ok: Literal[True] = True
    status: Literal["healthy"] = "healthy"
    default_printer: str | None = None
    printer_count: int
    drawer_supported: bool


class PrinterInfoResponse(APIModel):
    name: str
    driver: str
    is_default: bool = False
    mode: PrinterTransport
    supports_raw: bool
    supports_documents: bool
    supports_cash_drawer: bool


class PrintersResponse(APIModel):
    ok: Literal[True] = True
    printers: list[PrinterInfoResponse]


class ActionResponse(APIModel):
    ok: Literal[True] = True
    printer_name: str | None = None
    driver: str | None = None
    mode: PrinterTransport | None = None
    bytes_written: int | None = None
    detail: str | None = None


class PrintReceiptRequest(APIModel):
    source: str = "odoo_export_for_printing"
    receipt: dict[str, Any]
    printer_name: str | None = None
    open_drawer: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    mode: PrinterTransport = PrinterTransport.AUTO


class PrintHtmlRequest(APIModel):
    html: str
    printer_name: str | None = None
    open_drawer: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class DrawerRequest(APIModel):
    printer_name: str | None = None


class TestPrintRequest(APIModel):
    printer_name: str | None = None
    mode: PrinterTransport = PrinterTransport.AUTO
