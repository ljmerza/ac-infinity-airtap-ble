from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import slugify

from .cloud import ID_CHILD_LOCK, IDS_SETTINGS
from .cloud_entity import CloudEntity
from .const import DEVICE_MODEL, DOMAIN, MANUFACTURER
from .coordinator import (ACInfinityDataUpdateCoordinator,
                          ActiveBluetoothCoordinatorEntity)
from .device import ACInfinityDevice
from .models import ACInfinityCloudData, ACInfinityData

# Device settings a type 48 vent keeps in the cloud: (name, field, ids, icon).
# The app shows Display only when the cloud reports hasBacklightSwitch.
CLOUD_SWITCHES = (
    ("Display", "backlightSwitch", list(IDS_SETTINGS), "mdi:monitor"),
    ("Touch Sound", "keytoneSwitch", list(IDS_SETTINGS), "mdi:volume-high"),
    ("Child Lock", "devCsm1", [ID_CHILD_LOCK], "mdi:lock"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data: ACInfinityData | ACInfinityCloudData = hass.data[DOMAIN][entry.entry_id]
    if isinstance(data, ACInfinityCloudData):
        settings = data.coordinator.settings
        async_add_entities(
            CloudSwitch(data, name, field, ids, icon)
            for name, field, ids, icon in CLOUD_SWITCHES
            if settings.get(field) is not None
            and (field != "backlightSwitch" or settings.get("hasBacklightSwitch", 1))
        )
        return
    async_add_entities(
        [
            ACInfinityDisplaySwitch(data.coordinator, data.device, "Display"),
            ACInfinityAutoHighSwitch(data.coordinator, data.device, "Auto High Trigger"),
            ACInfinityAutoLowSwitch(data.coordinator, data.device, "Auto Low Trigger"),
        ]
    )


class ACInfinityDisplaySwitch(
    ActiveBluetoothCoordinatorEntity[ACInfinityDataUpdateCoordinator], SwitchEntity, RestoreEntity
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

    async def async_added_to_hass(self) -> None:
        """Restore the last known value; nothing reads it until a command connects."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in (STATE_ON, STATE_OFF):
            self._restore(last.state == STATE_ON)
            self._update_attrs()

    @callback
    def _restore(self, is_on: bool) -> None:
        if self._device.state.display_on is None:
            self._device.state.display_on = is_on

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


class ACInfinityAutoHighSwitch(ACInfinityDisplaySwitch):
    """Enable the Auto high temperature trigger (key 0x13 byte[0] bit 0x08)."""

    _attr_icon = "mdi:thermometer-chevron-up"

    @callback
    def _restore(self, is_on: bool) -> None:
        if self._device.state.auto_high_on is None:
            self._device.state.auto_high_on = is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._device.set_auto_high_enabled(True)
        self._write_state_from_device()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._device.set_auto_high_enabled(False)
        self._write_state_from_device()

    @callback
    def _update_attrs(self) -> None:
        self._attr_is_on = self._device.state.auto_high_on


class ACInfinityAutoLowSwitch(ACInfinityDisplaySwitch):
    """Enable the Auto low temperature trigger (key 0x13 byte[0] bit 0x04)."""

    _attr_icon = "mdi:thermometer-chevron-down"

    @callback
    def _restore(self, is_on: bool) -> None:
        if self._device.state.auto_low_on is None:
            self._device.state.auto_low_on = is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._device.set_auto_low_enabled(True)
        self._write_state_from_device()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._device.set_auto_low_enabled(False)
        self._write_state_from_device()

    @callback
    def _update_attrs(self) -> None:
        self._attr_is_on = self._device.state.auto_low_on


class CloudSwitch(CloudEntity, SwitchEntity):
    """An on/off device setting of a type 48 vent, through the AC Infinity cloud."""

    def __init__(
        self, data: ACInfinityCloudData, name: str, field: str, ids: list[int], icon: str
    ) -> None:
        super().__init__(data, name)
        self._attr_name = name
        self._attr_icon = icon
        self._field = field
        self._ids = ids

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.settings.get(self._field)
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._send(self.coordinator.set_setting(self._ids, **{self._field: 1}))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send(self.coordinator.set_setting(self._ids, **{self._field: 0}))
