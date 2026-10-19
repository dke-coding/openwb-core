"""Contract tests for the go_e chargepoint module against core 2.2.3
(openWB/core 3ced7f9ce), the commit installed with this update.

API contracts are pinned against the installed commit, never against a
branch HEAD. chargepoint_module.py itself is not changed by these tests.
"""

import ast
import pathlib
import types

import pytest

from go_e import chargepoint_module as cpm
from go_e.chargepoint_module import parse_status

COMPONENT_STATE = pathlib.Path(__file__).resolve().parents[3] / "common" / "component_state.py"


# ------------------------------------------------------------ fixtures

def snapshot_cp5_594_idle():
    """Real go-e API v2 /api/status, firmware 59.4, cp5 idle, read
    2026-10-19 01:01. Only sse is invented (990001)."""
    return {
        "car": 1,
        "amp": 16,
        "acu": None,
        "psm": 2,
        "frc": 1,
        "nrg": [230.0200043, 228.4700012, 229.7100067, 3.720000029,
                0, 0, 0,
                0, 0, 0, 0,
                0,
                0, 0, 0, 0],
        "eto": 7552045,
        "wh": 52395.60148,
        "cbl": 32,
        "err": 0,
        "fwv": "59.4",
        "pha": [False, False, False, True, True, True],
        "ama": 32,
        "sse": "990001",
    }


def snapshot_cp6_594_idle():
    """Real go-e API v2 /api/status, firmware 59.4, cp6 idle, read
    2026-10-19 01:01. Only sse is invented (990002)."""
    return {
        "car": 1,
        "amp": 6,
        "acu": None,
        "psm": 2,
        "frc": 1,
        "nrg": [230.3300018, 229.3999939, 230.3300018, 2.480000019,
                0, 0, 0,
                0, 0, 0, 0,
                0,
                0, 0, 0, 0],
        "eto": 1010871,
        "wh": 0,
        "cbl": None,
        "err": 0,
        "fwv": "59.4",
        "pha": [False, False, False, True, True, True],
        "ama": 32,
        "sse": "990002",
    }


def snapshot_charging_acu_differs_from_amp():
    """INVENTED: the charging snapshot of test_go_e_values.py (fw 60.5,
    2026-08-03) with acu 10 instead of 16, so that amp (16) and acu (10)
    differ. No such state was recorded on the installation."""
    return {
        "car": 2,
        "amp": 16,
        "acu": 10,
        "psm": 0,
        "frc": 0,
        "nrg": [230.9, 230.4, 230.9, 1.2,
                15.43, 15.50, 15.54,
                3568.95, 3571.30, 3585.41, 0.0,
                10725.66,
                99.0, 99.0, 99.0, 0.0],
        "eto": 7145947,
        "wh": 44326.0,
        "cbl": 32,
        "err": 0,
        "fwv": "60.5",
        "pha": [False, False, False, True, True, True],
        "ama": 32,
        "sse": "990003",
    }


# ------------------------------------------------- T1 real 59.4 idle

@pytest.mark.parametrize("snapshot", [snapshot_cp5_594_idle, snapshot_cp6_594_idle], ids=["cp5", "cp6"])
def test_594_idle_snapshot(snapshot):
    """Firmware 59.4 is installed; the older fixtures carry 60.5. In both
    59.4 idle snapshots amp is set (16 and 6) and acu is None, so an
    evse_current taken from amp would show up here."""
    resp = snapshot()
    parsed = parse_status(resp, old_phases_in_use=3)
    assert parsed["evse_current"] == 0.0
    assert parsed["plug_state"] is False
    assert parsed["max_evse_current"] == 32
    assert parsed["power"] == 0.0
    assert parsed["imported"] == float(resp["eto"])


# ---------------------------------------- T2 evse_current while charging

def test_evse_current_is_acu_while_charging():
    """Core 3ced7f9ce, SurplusControlled._fix_deviating_evse_current
    (control/algorithm/surplus_controlled.py) now evaluates evse_current
    0.0 ("is not None"; 82510a223 had "if evse_current"): offset =
    evse_current - medium charging current. 0.0 while charging would
    lower the set current by the whole charging current. Contract: car 2
    with acu set gives float(acu), never 0.0 and never amp."""
    parsed = parse_status(snapshot_charging_acu_differs_from_amp(), old_phases_in_use=3)
    assert parsed["charge_state"] is True
    assert parsed["evse_current"] == 10.0
    assert parsed["evse_current"] != 0.0


# ------------------------------------------ T3 imported None, error path

class _PassThroughContext:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return None

    def __exit__(self, exception_type, exception, exception_traceback):
        return False


class _ExceededErrorContext:
    """ErrorTimerContext (helpermodules/utils/error_handling.py @3ced7f9ce)
    between 60 s and 70 s after the first error: error_counter_exceeded()
    is True (timeout 60), __exit__ still hides the exception (timeout + 10)."""

    def __enter__(self):
        return None

    def __exit__(self, exception_type, exception, exception_traceback):
        return True

    def error_counter_exceeded(self):
        return True

    def reset_error_counter(self):
        raise AssertionError("reset_error_counter must not run on the error path")


class _RecordingState:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def test_error_path_passes_imported_and_exported_none(monkeypatch):
    """get_values hands imported=None and exported=None to the store once
    the error counter is exceeded. This path runs only in the cycles 60 s
    to 70 s after the first error (error_handling.py, ErrorTimerContext:
    error_counter_exceeded uses timeout 60, __exit__ hides the exception
    up to timeout + 10; later the exception leaves the context and
    store.set is skipped). ChargepointValueStoreBroker.update
    (modules/common/store/_chargepoint.py @3ced7f9ce) publishes imported
    and exported only when they are not None, so get.imported keeps the
    last meter reading. The error state stays in the store and is
    published again on every following cycle until a read succeeds."""
    monkeypatch.setattr(cpm, "SingleComponentUpdateContext", _PassThroughContext)
    monkeypatch.setattr(cpm, "ChargepointState", _RecordingState)
    module = cpm.ChargepointModule.__new__(cpm.ChargepointModule)
    module.fault_state = None
    module.client_error_context = _ExceededErrorContext()
    module.old_phases_in_use = 3
    stored = []
    module.store = types.SimpleNamespace(set=stored.append)

    def charger_unreachable(path):
        raise ConnectionError("charger unreachable")
    module._api_get = charger_unreachable

    module.get_values()

    assert len(stored) == 1
    assert stored[0].kwargs["imported"] is None
    assert stored[0].kwargs["exported"] is None


# ----------------------------- T4 contract against the checked-out core

def _chargepoint_state_init_args():
    tree = ast.parse(COMPONENT_STATE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "ChargepointState":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                    args = [a.arg for a in item.args.args][1:]
                    required = args[:len(args) - len(item.args.defaults)]
                    return set(required), set(args)
    raise AssertionError("ChargepointState.__init__ not found in " + str(COMPONENT_STATE))


def test_parse_status_has_every_required_chargepoint_state_arg():
    """(a) Every parameter of ChargepointState.__init__ without a default
    is in parse_status. Read from the checked-out core, not a fixed set:
    the 2026-08-05 failure (exported) came from a fixed set."""
    required, _ = _chargepoint_state_init_args()
    assert required
    parsed = parse_status(snapshot_cp5_594_idle(), old_phases_in_use=3)
    assert required - set(parsed) == set()


def test_parse_status_has_no_unknown_chargepoint_state_arg():
    """(b) parse_status returns no key that ChargepointState.__init__
    does not accept."""
    _, accepted = _chargepoint_state_init_args()
    parsed = parse_status(snapshot_cp5_594_idle(), old_phases_in_use=3)
    assert set(parsed) - accepted == set()
