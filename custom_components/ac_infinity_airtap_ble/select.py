from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify

from .const import DEVICE_MODEL, DOMAIN, MANUFACTURER
from .coordinator import (ACInfinityDataUpdateCoordinator,
                          ActiveBluetoothCoordinatorEntity)
from .device import BRIGHTNESS_LEVEL_TO_BYTE, ACInfinityDevice
from .models import ACInfinityData

BRIGHTNESS_OPTIONS = [str(lvl) for lvl in sorted(BRIGHTNESS_LEVEL_TO_BYTE)]  # "1".."5"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data: ACInfinityData = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [ACInfinityDisplayBrightness(data.coordinator, data.device, "Display Brightness")]
    )


class ACInfinityDisplayBrightness(
    ActiveBluetoothCoordinatorEntity[ACInfinityDataUpdateCoordinator], SelectEntity
):
    """Display brightness, 5 discrete levels (cmd-3 setting key 0x21 byte[0])."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:brightness-6"
    _attr_options = BRIGHTNESS_OPTIONS

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
            sw_version=device.state.version,
            connections={(dr.CONNECTION_BLUETOOTH, device.address)},
        )
        self._update_attrs()

    async def async_select_option(self, option: str) -> None:
        await self._device.set_display_brightness_level(int(option))
        self._write_state_from_device()

    @callback
    def _write_state_from_device(self) -> None:
        """Reflect commanded brightness in HA immediately (optimistic, like the switch)."""
        self._update_attrs()
        self.async_write_ha_state()

    @callback
    def _update_attrs(self) -> None:
        """Handle updating _attr values."""
        level = self._device.display_brightness_level
        # None until first set -> HA shows no selection.
        self._attr_current_option = str(level) if level is not None else None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_attrs()
        super()._handle_coordinator_update()
