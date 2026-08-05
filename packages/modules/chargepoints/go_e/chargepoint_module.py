"""
openWB software2 Chargepoint Module: go-eCharger (HOMEfix/Gemini, API v2)

Implements the AbstractChargepoint interface for go-e Charger using the local
HTTP API v2 (firmware 050.x+). Supports HOMEfix and Gemini hardware v3+.

Prerequisites on the go-e charger:
- Settings -> Internet -> Advanced Settings -> "Local HTTP API v2" ON
- Static IP recommended (DHCP reservation)

Reference: https://github.com/goecharger/go-eCharger-API-v2
License: GPLv3 (same as openWB software2)

Changelog (dke fork):
- 2026-08-05 M1 v1.0.2:
  * FIX: `exported` added to parse_status output. Required positional
    arg of ChargepointState in core 2.2.1 (82510a223) -- the HEAD of
    Release has a default, the installed commit does not. Lesson:
    verify API contracts against the INSTALLED commit, not branch HEAD.
  * HARDEN: get_values pre-initializes chargepoint_state and guards
    store.set, so a swallowed parse error can no longer cascade into
    a NameError.
- 2026-08-04 M1/Gate 0:
  * FIX power scaling: nrg[11] is Watt in API v2 (was multiplied by 10,
    a leftover from API v1 where nrg[11] is 0.01 kW). Verified against
    go-e apikeys doc, evcc StatusResponse2 and live per-phase sums.
  * ADD evse_current from `acu` ("Mit wie vielen Ampere darf das Auto
    derzeit laden?"). Core 2.2.x requires this value in
    control/ev/ev.py:_check_phase_switch_conditions when
    auto_phase_switch is enabled; None crashes every control cycle
    with TypeError. Mapped like openwb_pro maps `offered_current`.
  * USE measured per-phase powers nrg[7..9] instead of letting the
    core derive P = U * I (measured values are consistent with
    nrg[11], derived ones are not).
"""

import time
from typing import Any, Dict

from helpermodules.utils.error_handling import CP_ERROR, ErrorTimerContext
from modules.chargepoints.go_e.config import GoE
from modules.common.abstract_chargepoint import AbstractChargepoint
from modules.common.abstract_device import DeviceDescriptor
from modules.common.component_context import SingleComponentUpdateContext
from modules.common.fault_state import ComponentInfo, FaultState
from modules.common.store import get_chargepoint_value_store
from modules.common.component_state import ChargepointState
from modules.common import req


# go-e API constants
CAR_STATE_READY = 1       # Charger ready, no vehicle connected
CAR_STATE_CHARGING = 2    # Vehicle charging
CAR_STATE_WAIT_CAR = 3    # Waiting for vehicle (plug connected, not charging)
CAR_STATE_COMPLETE = 4    # Charging complete, vehicle still connected
CAR_STATE_ERROR = 5       # Error state

PSM_1P = 1                # Phase switch mode: 1-phase
PSM_3P = 2                # Phase switch mode: 3-phase

FRC_NEUTRAL = 0           # Force state: neutral (charger decides)
FRC_OFF = 1               # Force state: do not charge
FRC_ON = 2                # Force state: force charging

# Status keys requested from the charger. M1: added "acu".
STATUS_FILTER = "car,amp,acu,psm,frc,nrg,eto,wh,cbl,err,fwv,pha,ama,sse"


def parse_status(resp: Dict[str, Any], old_phases_in_use: int) -> Dict[str, Any]:
    """Pure parser for a go-e API v2 status response.

    Extracted from get_values() so the unit conversions can be pinned
    by unit tests without an openWB runtime. Returns the kwargs for
    ChargepointState plus the phases_in_use bookkeeping value.

    API v2 units (verified 2026-08-04 against
    go-eCharger-API-v2/API_KEYS_FIRMWARE/apikeys-de.md, evcc
    charger/go-e/types2.go and live per-phase measurements):
      nrg[0..2]  U L1..L3 in V
      nrg[4..6]  I L1..L3 in A
      nrg[7..9]  P L1..L3 in W
      nrg[11]    P total  in W          <-- NOT 0.01 kW (that is v1)
      eto        total energy in Wh
      acu        allowed charging current in A (None if unknown)
    """
    car = int(resp.get("car") or 0)
    plug_state = car != CAR_STATE_READY
    charge_state = car == CAR_STATE_CHARGING

    nrg = resp.get("nrg") or [0] * 16
    if len(nrg) < 16:
        nrg = list(nrg) + [0] * (16 - len(nrg))

    voltages = [float(nrg[0]), float(nrg[1]), float(nrg[2])]
    currents = [float(nrg[4]), float(nrg[5]), float(nrg[6])]
    # M1 fix: nrg[11] is already Watt in API v2. The former
    # "* 10.0" produced 107 kW instead of 10.7 kW and poisoned
    # load management (raw_power_left).
    power = float(nrg[11])
    # M1 fix: use measured per-phase powers; they are consistent
    # with nrg[11], while U*I derived values are not.
    powers = [float(nrg[7]), float(nrg[8]), float(nrg[9])]

    imported = float(resp.get("eto") or 0)
    # Required positional arg in core 2.2.1 (82510a223); a wallbox
    # never feeds back, so exported is constant 0.
    exported = 0.0

    # M1 fix: evse_current from `acu` (allowed charging current).
    # Core 2.2.x compares this against min/max current ranges in
    # auto phase switching; it must never be None while a vehicle
    # is charging. acu is None when the charger cannot tell (e.g.
    # no vehicle) -> map to 0.0, matching the internal chargepoint
    # which reports evse_current=0 when idle.
    acu = resp.get("acu")
    evse_current = float(acu) if acu is not None else 0.0

    pha = resp.get("pha", 0)
    if isinstance(pha, list) and len(pha) >= 3:
        phases_in_use = sum(1 for p in pha[:3] if p)
    else:
        pha_after = int(pha) & 0b00000111
        phases_in_use = bin(pha_after).count("1")

    if phases_in_use == 0:
        phases_in_use = old_phases_in_use

    ama = resp.get("ama")
    cbl = resp.get("cbl")
    max_evse_current = ama if ama else (cbl if cbl else 32)

    serial = resp.get("sse") or resp.get("fwv", "unknown")

    return {
        "power": power,
        "powers": powers,
        "currents": currents,
        "voltages": voltages,
        "imported": imported,
        "exported": exported,
        "plug_state": plug_state,
        "charge_state": charge_state,
        "phases_in_use": phases_in_use,
        "evse_current": evse_current,
        "max_evse_current": int(max_evse_current),
        "serial_number": serial,
    }


class ChargepointModule(AbstractChargepoint):
    def __init__(self, config: GoE) -> None:
        self.config = config
        self.store = get_chargepoint_value_store(self.config.id)
        self.fault_state = FaultState(ComponentInfo(
            self.config.id, "Ladepunkt", "chargepoint"))
        self.client_error_context = ErrorTimerContext(
            f"openWB/set/chargepoint/{self.config.id}/get/error_timestamp",
            CP_ERROR, hide_exception=True)
        self.session = req.get_http_session()
        self.old_phases_in_use = 3

    # -------- HTTP helpers --------

    def _api_url(self, path: str) -> str:
        ip = self.config.configuration.ip_address
        return f"http://{ip}/api/{path}"

    def _api_get(self, path: str) -> dict:
        timeout = self.config.configuration.timeout
        return self.session.get(self._api_url(path), timeout=timeout).json()

    # -------- AbstractChargepoint interface --------

    def set_current(self, current: float) -> None:
        """Set target charging current in A (typ. 0 or 6..32)."""
        if self.client_error_context.error_counter_exceeded():
            current = 0
        with SingleComponentUpdateContext(self.fault_state, update_always=False):
            with self.client_error_context:
                amp = max(0, min(32, int(current)))
                if amp == 0:
                    self._api_get(f"set?frc={FRC_OFF}")
                else:
                    self._api_get(f"set?amp={amp}")
                    self._api_get(f"set?frc={FRC_NEUTRAL}")

    def get_values(self) -> None:
        chargepoint_state = None
        with SingleComponentUpdateContext(self.fault_state):
            with self.client_error_context:
                resp = self._api_get(f"status?filter={STATUS_FILTER}")

                parsed = parse_status(resp, self.old_phases_in_use)
                self.old_phases_in_use = parsed["phases_in_use"]
                chargepoint_state = ChargepointState(**parsed)

                self.client_error_context.reset_error_counter()
            if self.client_error_context.error_counter_exceeded():
                chargepoint_state = ChargepointState(
                    plug_state=None, charge_state=False,
                    imported=None, exported=None,
                    currents=[0]*3, phases_in_use=self.old_phases_in_use,
                    power=0, evse_current=0.0)
            if chargepoint_state is not None:
                self.store.set(chargepoint_state)

    def switch_phases(self, phases_to_use: int) -> None:
        with SingleComponentUpdateContext(self.fault_state, update_always=False):
            with self.client_error_context:
                psm = PSM_1P if phases_to_use == 1 else PSM_3P
                self._api_get(f"set?frc={FRC_OFF}")
                time.sleep(1)
                self._api_get(f"set?psm={psm}")
                time.sleep(2)
                self._api_get(f"set?frc={FRC_NEUTRAL}")

    def interrupt_cp(self, duration: int) -> None:
        with SingleComponentUpdateContext(self.fault_state, update_always=False):
            with self.client_error_context:
                self._api_get(f"set?frc={FRC_OFF}")
                time.sleep(duration)
                self._api_get(f"set?frc={FRC_NEUTRAL}")

    def clear_rfid(self) -> None:
        """No-op: go-e manages RFID tags internally via its own UI."""
        pass


chargepoint_descriptor = DeviceDescriptor(configuration_factory=GoE)
