from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify

from .const import DEVICE_MODEL, DOMAIN, MANUFACTURER
from .coordinator import (ACInfinityDataUpdateCoordinator,
                          ActiveBluetoothCoordinatorEntity)
from .device import ACInfinityDevice
from .models import ACInfinityData


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data: ACInfinityData = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ACInfinityDisplaySwitch(data.coordinator, data.device, "Display")])


class ACInfinityDisplaySwitch(
    ActiveBluetoothCoordinatorEntity[ACInfinityDataUpdateCoordinator], SwitchEntity
):
    """Toggle the AirTap's on-unit display (cmd-3 setting key 0x21)."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:monitor"

    def __init__(
        self,
        coordinator: ACInfinityDataUpdateCoordinator,
        device: ACInfinityDevice,
        name: str,
    ) -> None:
        super().__init__(coordinator)
        self._device = device
        self._attr_name = name
        self._attr_unique_id = f"{self._device.address}_{slugify(name)}"
        self._attr_device_info = DeviceInfo(
            name=device.name,
            model=DEVICE_MODEL[device.state.type],
            manufacturer=MANUFACTURER,
            sw_version=str(device.state.version),
            connections={(dr.CONNECTION_BLUETOOTH, device.address)},
        )
        self._update_attrs()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._device.set_display(True)
        self._write_state_from_device()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._device.set_display(False)
        self._write_state_from_device()

    @callback
    def _write_state_from_device(self) -> None:
        """Reflect commanded display state in HA immediately.

        Like the fan entity, this device's status pushes aren't decoded for
        type 48, so we surface the optimistic commanded state right away.
        """
        self._update_attrs()
        self.async_write_ha_state()

    @callback
    def _update_attrs(self) -> None:
        """Handle updating _attr values."""
        # None until first toggled -> HA shows the switch as unknown.
        self._attr_is_on = self._device.display_on

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_attrs()
        super()._handle_coordinator_update()
