from importlib import import_module

_EXPORTS = {
    "DEFAULT_SERVICE_IDENTITY": ".models",
    "DEFAULT_SERVICE_SCOPE": ".models",
    "ServiceDefinition": ".models",
    "ServiceIdentity": ".models",
    "ServiceManager": ".manager",
    "ServiceScope": ".models",
    "ServiceState": ".models",
    "ServiceStatus": ".models",
    "build_service_manager": ".manager",
    "default_service_name": ".models",
    "resolve_service_config_path": ".manager",
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
