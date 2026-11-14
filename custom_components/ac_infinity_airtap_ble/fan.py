from __future__ import annotations

import math
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import slugify
from homeassistant.util.percentage import (int_states_in_range,
                                           percentage_to_ranged_value,
                                           ranged_value_to_percentage)

from .cloud import ID_POWER, ID_SPEED, MODE_NAMES
from .cloud_entity import CloudEntity
from .const import DEVICE_MODEL, DOMAIN, MANUFACTURER
from .coordinator import (ACInfinityDataUpdateCoordinator,
                          ActiveBluetoothCoordinatorEntity)
from .device import ACInfinityDevice
from .models import ACInfinityCloudData, ACInfinityData

SPEED_RANGE = (1, 10)
PRESET_AUTO = "Auto"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data: ACInfinityData | ACInfinityCloudData = hass.data[DOMAIN][entry.entry_id]
    if isinstance(data, ACInfinityCloudData):
        async_add_entities([AirtapCloudFan(data, "Fan")])
        return
    async_add_entities([ACInfinityFan(data.coordinator, data.device, "Fan")])


class ACInfinityFan(
    ActiveBluetoothCoordinatorEntity[ACInfinityDataUpdateCoordinator], FanEntity, RestoreEntity
):
    _attr_has_entity_name = True
    _attr_speed_count = int_states_in_range(SPEED_RANGE)
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.PRESET_MODE
        | FanEntityFeature.TURN_OFF
        | FanEntityFeature.TURN_ON
    )
    _attr_preset_modes = [PRESET_AUTO]

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

    async def async_added_to_hass(self) -> None:
        """Restore the commanded state; nothing reads it back from the vent."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is None or last.state not in (STATE_ON, STATE_OFF):
            return
        percentage = last.attributes.get("percentage")
        speed = math.ceil(percentage_to_ranged_value(SPEED_RANGE, percentage)) if percentage else None
        auto = last.attributes.get("preset_mode") == PRESET_AUTO
        self._device.restore_fan_state(last.state == STATE_ON, speed, auto)
        self._update_attrs()

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

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Hand control to the vent's own Auto mode."""
        await self._device.set_auto_mode()
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
        self._attr_preset_mode = PRESET_AUTO if self._device.is_auto else None
        self._attr_percentage = ranged_value_to_percentage(
            SPEED_RANGE, self._device.state.fan or 0
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_attrs()
        super()._handle_coordinator_update()


class AirtapCloudFan(CloudEntity, FanEntity):
    """A type 48 vent through the AC Infinity cloud.

    On and off are the vent's power (powerState). Speed is its fan level (onSpead, 1-10),
    which the vent uses in every mode; the mode is its own select.
    """

    _attr_name = None
    _attr_speed_count = int_states_in_range(SPEED_RANGE)
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED | FanEntityFeature.TURN_OFF | FanEntityFeature.TURN_ON
    )

    @property
    def is_on(self) -> bool:
        power = self.coordinator.info.get("powerState")
        if power is not None:
            return power == 1
        return bool(self.coordinator.info.get("speak"))

    @property
    def percentage(self) -> int | None:
        """The fan level set on the vent, or what it runs at if that isn't known."""
        level = self.coordinator.mode_value("onSpead")
        if level is None:
            level = self.coordinator.info.get("speak")
        return ranged_value_to_percentage(SPEED_RANGE, int(level)) if level else 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        mode = self.coordinator.info.get("curMode")
        return {"mode": MODE_NAMES.get(mode, mode)}

    async def async_set_percentage(self, percentage: int) -> None:
        if not percentage:
            await self.async_turn_off()
            return
        speed = math.ceil(percentage_to_ranged_value(SPEED_RANGE, percentage))
        await self._send(self.coordinator.set_control(ID_SPEED, onSpead=speed))
        if not self.is_on:
            await self._send(self.coordinator.set_control(ID_POWER, powerState=1))

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        if percentage:
            await self.async_set_percentage(percentage)
            return
        await self._send(self.coordinator.set_control(ID_POWER, powerState=1))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send(self.coordinator.set_control(ID_POWER, powerState=0))
