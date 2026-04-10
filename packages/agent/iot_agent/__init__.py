from .printer_service import PrinterService

__all__ = ["app", "create_app", "PrinterService"]


def __getattr__(name: str):
    if name in {"app", "create_app"}:
        from .main import app, create_app

        return {"app": app, "create_app": create_app}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
