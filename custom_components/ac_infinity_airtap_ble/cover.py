"""The vent's louvers as a cover. Only cloud entries (type 48) have one.

The AC Infinity app has no command to close the vent. Its Vent Behavior is Open (louvers
always open) or Auto (louvers close while the vent's own fan is idle). So closing sets Auto
and turns the vent's fan off; the louvers then stay shut whatever the house HVAC is doing.
Opening sets Open, which keeps them open with the fan off too.
"""

from __future__ import annotations

import time
from typing import Any

from homeassistant.components.cover import CoverDeviceClass, CoverEntity, CoverEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .cloud import ID_POWER, ID_VENT_BEHAVIOR
from .cloud_entity import CloudEntity
from .const import DOMAIN
from .models import ACInfinityCloudData

# How long to show "closing"/"opening" before trusting the reported louver state again.
TRANSITION_SECONDS = 90
VENT_AUTO = 0
VENT_OPEN = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    if isinstance(data, ACInfinityCloudData) and data.coordinator.mode_value("co2Accuracy") is not None:
        async_add_entities([CloudVentCover(data, "Vent Louvers")])


class CloudVentCover(CloudEntity, CoverEntity):
    """Open or close a type 48 vent's louvers through the AC Infinity cloud."""

    _attr_name = "Vent Louvers"
    _attr_device_class = CoverDeviceClass.DAMPER
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE

    def __init__(self, data: ACInfinityCloudData, name: str) -> None:
        super().__init__(data, name)
        # (target closed?, until) while the cloud hasn't reported the new state yet.
        self._target: tuple[bool, float] | None = None

    @property
    def _reported_closed(self) -> bool | None:
        value = self.coordinator.info.get("isWindSteering")
        return None if value is None else value == 0

    @property
    def _moving_to(self) -> bool | None:
        """The state a recent command is heading to, until the vent reports it."""
        if self._target is None:
            return None
        closed, until = self._target
        if time.monotonic() > until or self._reported_closed == closed:
            self._target = None
            return None
        return closed

    @property
    def is_closed(self) -> bool | None:
        return self._reported_closed

    @property
    def is_closing(self) -> bool:
        return self._moving_to is True

    @property
    def is_opening(self) -> bool:
        return self._moving_to is False

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._send(self.coordinator.set_control(ID_VENT_BEHAVIOR, co2Accuracy=VENT_AUTO))
        await self._send(self.coordinator.set_control(ID_POWER, powerState=0))
        self._target = (True, time.monotonic() + TRANSITION_SECONDS)
        self.async_write_ha_state()

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._send(self.coordinator.set_control(ID_VENT_BEHAVIOR, co2Accuracy=VENT_OPEN))
        self._target = (False, time.monotonic() + TRANSITION_SECONDS)
        self.async_write_ha_state()
