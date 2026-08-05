"""Stubs the openWB runtime imports so go_e.chargepoint_module can be
imported and its pure parser tested outside an openWB installation.
Only parse_status() and STATUS_FILTER are under test; the stubbed
classes are never exercised."""

import sys
import types
from unittest.mock import MagicMock


def _stub(name: str) -> types.ModuleType:
    mod = sys.modules.get(name)
    if mod is None:
        mod = types.ModuleType(name)
        sys.modules[name] = mod
    return mod


for name in [
    "helpermodules", "helpermodules.utils", "helpermodules.utils.error_handling",
    "modules", "modules.chargepoints", "modules.chargepoints.go_e",
    "modules.chargepoints.go_e.config",
    "modules.common", "modules.common.abstract_chargepoint",
    "modules.common.abstract_device", "modules.common.component_context",
    "modules.common.fault_state", "modules.common.store",
    "modules.common.component_state", "modules.common.req",
]:
    _stub(name)

sys.modules["helpermodules.utils.error_handling"].CP_ERROR = 3
sys.modules["helpermodules.utils.error_handling"].ErrorTimerContext = MagicMock
sys.modules["modules.chargepoints.go_e.config"].GoE = MagicMock
sys.modules["modules.common.abstract_chargepoint"].AbstractChargepoint = object
sys.modules["modules.common.abstract_device"].DeviceDescriptor = MagicMock
sys.modules["modules.common.component_context"].SingleComponentUpdateContext = MagicMock
sys.modules["modules.common.fault_state"].ComponentInfo = MagicMock
sys.modules["modules.common.fault_state"].FaultState = MagicMock
sys.modules["modules.common.store"].get_chargepoint_value_store = MagicMock()
sys.modules["modules.common.component_state"].ChargepointState = MagicMock
sys.modules["modules.common.req"].get_http_session = MagicMock()

# make the package importable as go_e.*
import pathlib  # noqa: E402
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
