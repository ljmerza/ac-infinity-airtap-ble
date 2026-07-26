from __future__ import annotations

import math
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify
from homeassistant.util.percentage import (int_states_in_range,
                                           percentage_to_ranged_value,
                                           ranged_value_to_percentage)

from .const import DEVICE_MODEL, DOMAIN, MANUFACTURER
from .coordinator import (ACInfinityDataUpdateCoordinator,
                          ActiveBluetoothCoordinatorEntity)
from .device import ACInfinityDevice
from .models import ACInfinityData

SPEED_RANGE = (1, 10)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data: ACInfinityData = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ACInfinityFan(data.coordinator, data.device, "Fan")])


class ACInfinityFan(
    ActiveBluetoothCoordinatorEntity[ACInfinityDataUpdateCoordinator], FanEntity
):
    _attr_has_entity_name = True
    _attr_speed_count = int_states_in_range(SPEED_RANGE)
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.TURN_OFF
        | FanEntityFeature.TURN_ON
    )

    def __init__(
        self,
        coordinator: ACInfinityDataUpdateCoordinator,
        device: ACInfinityDevice,
        name: str,
    ) -> None:
        super().__init__(coordinator)
        self._device = device
        # Primary entity: name=None inherits the device name, so entity_id is
        # fan.<device> with no "_fan" suffix. unique_id still derives from "Fan".
        self._attr_name = None
        self._attr_unique_id = f"{self._device.address}_{slugify(name)}"
        self._attr_device_info = DeviceInfo(
            name=device.name,
            model=DEVICE_MODEL[device.state.type],
            manufacturer=MANUFACTURER,
            sw_version=str(device.state.version),
            connections={(dr.CONNECTION_BLUETOOTH, device.address)},
        )

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed of the fan, as a percentage."""
        speed = 0
        if percentage > 0:
            speed = math.ceil(percentage_to_ranged_value(SPEED_RANGE, percentage))

        await self._device.set_speed(speed)
        self._write_state_from_device()

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        speed = None
        if percentage is not None:
            speed = math.ceil(percentage_to_ranged_value(SPEED_RANGE, percentage))
        await self._device.turn_on(speed)
        self._write_state_from_device()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._device.turn_off()
        self._write_state_from_device()

    @callback
    def _write_state_from_device(self) -> None:
        """Reflect commanded device state in HA immediately.

        This device's status advertisements don't reliably reach the
        coordinator, so the entity would otherwise never update after a
        command. Push the optimistic commanded state right away.
        """
        self._update_attrs()
        self.async_write_ha_state()

    @callback
    def _update_attrs(self) -> None:
        """Handle updating _attr values."""
        self._attr_is_on = self._device.is_on
        self._attr_percentage = ranged_value_to_percentage(
            SPEED_RANGE, self._device.state.fan or 0
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_attrs()
        super()._handle_coordinator_update()
