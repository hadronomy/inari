from importlib import import_module

_EXPORTS = {
    "app": ".local_api.app",
    "create_app": ".local_api.app",
    "PrinterService": ".printing.service",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    try:
        module_name = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    module = import_module(module_name, __package__)
    value = getattr(module, name)
    globals()[name] = value
    return value
