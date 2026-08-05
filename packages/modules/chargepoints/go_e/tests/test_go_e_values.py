"""Unit tests for the go_e chargepoint parser (M1 / Gate 0).

Pins the go-e API v2 unit assumptions with real snapshots so a
regression to v1 semantics (the *10 power bug) can never come back
silently. Sources for the fixture values:

- snapshot_charging: live debug log 2026-08-03 (cp5, ID.7 charging,
  3-phase ~10.7 kW) -- the exact cycle where the old code reported
  107 kW to load management.
- snapshot_idle: live API dump 2026-05-15 (cp6, no vehicle,
  cbl=null) documented in Notion "Code & Live-Installation".
"""

import pytest

from go_e.chargepoint_module import parse_status, STATUS_FILTER


def snapshot_charging():
    # nrg: U(L1,L2,L3,N), I(L1,L2,L3), P(L1,L2,L3,N,Total), pf(...)
    return {
        "car": 2,
        "amp": 16,
        "acu": 16,
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
        "sse": "219465",
    }


def snapshot_idle():
    return {
        "car": 1,
        "amp": 6,
        "acu": None,
        "psm": 0,
        "frc": 1,
        "nrg": [231.0, 230.8, 231.1, 0.9,
                0.0, 0.0, 0.0,
                0.0, 0.0, 0.0, 0.0,
                0.0,
                0.0, 0.0, 0.0, 0.0],
        "eto": 976812,
        "wh": 0.0,
        "cbl": None,
        "err": 0,
        "fwv": "60.5",
        "pha": [False, False, False, True, True, True],
        "ama": 32,
        "sse": "219485",
    }


# ---------------------------------------------------------------- power

def test_power_is_watt_not_v1_deciwatt():
    """The M1 bug: v1 semantics (nrg[11] in 0.01 kW) multiplied by 10
    turned 10.7 kW into 107 kW. v2 delivers Watt directly."""
    parsed = parse_status(snapshot_charging(), old_phases_in_use=3)
    assert parsed["power"] == pytest.approx(10725.66)
    # regression guard: the old code produced exactly this wrong value
    assert parsed["power"] != pytest.approx(107256.6)


def test_power_matches_phase_sum():
    """nrg[11] must be consistent with the measured per-phase powers.
    This is the invariant that exposes any future unit drift."""
    parsed = parse_status(snapshot_charging(), old_phases_in_use=3)
    assert parsed["power"] == pytest.approx(sum(parsed["powers"]), rel=0.01)


def test_powers_are_measured_not_derived():
    parsed = parse_status(snapshot_charging(), old_phases_in_use=3)
    assert parsed["powers"] == pytest.approx([3568.95, 3571.30, 3585.41])
    # U*I for L1 would be ~3582.8 W; measured must win
    assert parsed["powers"][0] != pytest.approx(230.9 * 15.43, rel=1e-4)


# --------------------------------------------------------- evse_current

def test_evse_current_from_acu_while_charging():
    parsed = parse_status(snapshot_charging(), old_phases_in_use=3)
    assert parsed["evse_current"] == 16.0
    assert isinstance(parsed["evse_current"], float)


def test_evse_current_never_none():
    """Core 2.2.x crashes with TypeError on `None < int` in
    _check_phase_switch_conditions. acu=None (idle) must map to 0."""
    parsed = parse_status(snapshot_idle(), old_phases_in_use=3)
    assert parsed["evse_current"] == 0.0


def test_acu_in_status_filter():
    """acu must actually be requested, otherwise the charger omits it."""
    assert "acu" in STATUS_FILTER.split(",")


# ------------------------------------------------------------- energy

def test_eto_is_wh():
    parsed = parse_status(snapshot_charging(), old_phases_in_use=3)
    assert parsed["imported"] == pytest.approx(7145947.0)  # Wh, not 0.1 kWh


# ---------------------------------------------------------- misc state

def test_charging_state_flags():
    parsed = parse_status(snapshot_charging(), old_phases_in_use=3)
    assert parsed["plug_state"] is True
    assert parsed["charge_state"] is True


def test_idle_state_flags_and_cbl_null():
    parsed = parse_status(snapshot_idle(), old_phases_in_use=3)
    assert parsed["plug_state"] is False
    assert parsed["charge_state"] is False
    assert parsed["max_evse_current"] == 32  # ama wins, cbl=null tolerated


def test_currents_and_voltages_passthrough():
    parsed = parse_status(snapshot_charging(), old_phases_in_use=3)
    assert parsed["currents"] == pytest.approx([15.43, 15.50, 15.54])
    assert parsed["voltages"] == pytest.approx([230.9, 230.4, 230.9])


def test_short_or_missing_nrg_is_padded():
    resp = snapshot_idle()
    resp["nrg"] = [230.0]
    parsed = parse_status(resp, old_phases_in_use=3)
    assert parsed["power"] == 0.0
    resp["nrg"] = None
    parsed = parse_status(resp, old_phases_in_use=3)
    assert parsed["power"] == 0.0


def test_phases_fallback_keeps_last_value():
    """pha[0:3] is F,F,F on both chargers (pre-contactor), so the
    parser falls back to the last known value. Documented limitation,
    proper fix (derive from currents) is scheduled for Gate 3."""
    parsed = parse_status(snapshot_charging(), old_phases_in_use=3)
    assert parsed["phases_in_use"] == 3
    parsed = parse_status(snapshot_idle(), old_phases_in_use=1)
    assert parsed["phases_in_use"] == 1


# ------------------------------------------------- core 2.2.1 contract

def test_exported_present_and_zero():
    """`exported` is a REQUIRED positional arg of ChargepointState in
    core 2.2.1 (82510a223). Missing it crashes get_values on every
    cycle (found in production 2026-08-05). A wallbox never exports."""
    parsed = parse_status(snapshot_charging(), old_phases_in_use=3)
    assert parsed["exported"] == 0.0


def test_all_required_221_args_present():
    """Pin the full required-arg set of ChargepointState @ 82510a223.
    Guards against verifying signatures on the wrong commit again."""
    required = {"phases_in_use", "imported", "exported", "power",
                "currents", "charge_state", "plug_state"}
    parsed = parse_status(snapshot_idle(), old_phases_in_use=3)
    assert required.issubset(parsed.keys())
