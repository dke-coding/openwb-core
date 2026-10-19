"""Loads go_e.chargepoint_module with stubbed openWB runtime imports so its
pure parser can be tested outside an openWB installation.

The stubs exist only while the module is loaded and stay reachable only
through the module's own globals: sys.modules is restored afterwards, so
no other test under packages sees a stub or a changed attribute. The
module is loaded by file path, which makes the import independent of the
working directory and of PYTHONPATH (repo root, CI, this folder)."""

import importlib.util
import pathlib
import sys
import types
from unittest.mock import MagicMock

_GO_E_DIR = pathlib.Path(__file__).resolve().parents[1]

_STUBBED = [
    "helpermodules", "helpermodules.utils", "helpermodules.utils.error_handling",
    "modules", "modules.chargepoints", "modules.chargepoints.go_e",
    "modules.chargepoints.go_e.config",
    "modules.common", "modules.common.abstract_chargepoint",
    "modules.common.abstract_device", "modules.common.component_context",
    "modules.common.fault_state", "modules.common.store",
    "modules.common.component_state", "modules.common.req",
]


def _stubs() -> dict:
    stubs = {name: types.ModuleType(name) for name in _STUBBED}
    stubs["helpermodules.utils.error_handling"].CP_ERROR = 3
    stubs["helpermodules.utils.error_handling"].ErrorTimerContext = MagicMock
    stubs["modules.chargepoints.go_e.config"].GoE = MagicMock
    stubs["modules.common.abstract_chargepoint"].AbstractChargepoint = object
    stubs["modules.common.abstract_device"].DeviceDescriptor = MagicMock
    stubs["modules.common.component_context"].SingleComponentUpdateContext = MagicMock
    stubs["modules.common.fault_state"].ComponentInfo = MagicMock
    stubs["modules.common.fault_state"].FaultState = MagicMock
    stubs["modules.common.store"].get_chargepoint_value_store = MagicMock()
    stubs["modules.common.component_state"].ChargepointState = MagicMock
    stubs["modules.common.req"].get_http_session = MagicMock()
    stubs["modules.common"].req = stubs["modules.common.req"]
    return stubs


def _load_go_e() -> types.ModuleType:
    saved = {name: sys.modules.get(name) for name in _STUBBED}
    sys.modules.update(_stubs())
    try:
        spec = importlib.util.spec_from_file_location(
            "go_e.chargepoint_module", _GO_E_DIR / "chargepoint_module.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for name, original in saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
    return module


# make the module importable as go_e.chargepoint_module
if "go_e.chargepoint_module" not in sys.modules:
    _package = types.ModuleType("go_e")
    _package.__path__ = [str(_GO_E_DIR)]
    _package.chargepoint_module = _load_go_e()
    sys.modules["go_e"] = _package
    sys.modules["go_e.chargepoint_module"] = _package.chargepoint_module
