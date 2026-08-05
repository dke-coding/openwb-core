"""
openWB software2 Chargepoint Module: go-eCharger (HOMEfix/Gemini, API v2)

Configuration class for the go-e charger chargepoint module.
"""

from typing import Optional
from modules.common.abstract_chargepoint import SetupChargepoint


class GoEConfiguration:
    def __init__(self, ip_address: Optional[str] = None, timeout: int = 3) -> None:
        self.ip_address = ip_address
        self.timeout = timeout


class GoE(SetupChargepoint[GoEConfiguration]):
    def __init__(self,
                 name: str = "go-eCharger (HOMEfix/Gemini, API v2)",
                 type: str = "go_e",
                 id: int = 0,
                 configuration: GoEConfiguration = None) -> None:
        super().__init__(name, type, id, configuration or GoEConfiguration())
