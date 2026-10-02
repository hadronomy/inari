import importlib.util
from pathlib import Path
import sys


services = Path(__file__).parents[1] / "inari_devices" / "services"
spec = importlib.util.spec_from_file_location(
    "inari_odoo_services",
    services / "__init__.py",
    submodule_search_locations=[str(services)],
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
