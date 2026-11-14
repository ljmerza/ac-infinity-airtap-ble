from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import slugify

from .cloud import (
    ID_MODE,
    ID_VENT_BEHAVIOR,
    IDS_SETTINGS,
    MODE_NAMES,
)
from .cloud_entity import CloudEntity
from .const import DEVICE_MODEL, DOMAIN, MANUFACTURER
from .coordinator import (ACInfinityDataUpdateCoordinator,
                          ActiveBluetoothCoordinatorEntity)
from .device import BRIGHTNESS_BYTE_TO_LEVEL, BRIGHTNESS_LEVEL_TO_BYTE, ACInfinityDevice
from .models import ACInfinityCloudData, ACInfinityData

BRIGHTNESS_OPTIONS = [str(lvl) for lvl in sorted(BRIGHTNESS_LEVEL_TO_BYTE)]  # "1".."5"
VENT_BEHAVIOR = {0: "Auto", 1: "Open"}  # co2Accuracy: Auto closes the vent while idle
UNITS = {0: UnitOfTemperature.FAHRENHEIT, 1: UnitOfTemperature.CELSIUS}  # devCompany


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data: ACInfinityData | ACInfinityCloudData = hass.data[DOMAIN][entry.entry_id]
    if isinstance(data, ACInfinityCloudData):
        coordinator = data.coordinator
        entities = [
            CloudSelect(
                data, "Mode", list(MODE_NAMES.values()),
                lambda c: MODE_NAMES.get(c.info.get("curMode")),
                lambda c, o: c.set_control(ID_MODE, atType=_key(MODE_NAMES, o)),
            )
        ]
        if coordinator.mode_value("co2Accuracy") is not None:
            entities.append(
                CloudSelect(
                    data, "Vent Behavior", list(VENT_BEHAVIOR.values()),
                    lambda c: VENT_BEHAVIOR.get(c.mode_value("co2Accuracy")),
                    lambda c, o: c.set_control(ID_VENT_BEHAVIOR, co2Accuracy=_key(VENT_BEHAVIOR, o)),
                )
            )
        if coordinator.settings.get("devLight") is not None:
            entities.append(
                CloudSelect(
                    data, "Display Brightness", BRIGHTNESS_OPTIONS,
                    lambda c: _str(BRIGHTNESS_BYTE_TO_LEVEL.get(c.settings.get("devLight"))),
                    lambda c, o: c.set_setting(
                        list(IDS_SETTINGS), devLight=BRIGHTNESS_LEVEL_TO_BYTE[int(o)]
                    ),
                )
            )
        if coordinator.settings.get("devCompany") is not None:
            entities.append(
                CloudSelect(
                    data, "Temperature Unit", list(UNITS.values()),
                    lambda c: UNITS.get(c.settings.get("devCompany")),
                    lambda c, o: c.set_setting(list(IDS_SETTINGS), devCompany=_key(UNITS, o)),
                )
            )
        async_add_entities(entities)
        return
    async_add_entities(
        [
            ACInfinityDisplayBrightness(data.coordinator, data.device, "Display Brightness"),
            ACInfinityTemperatureUnit(data.coordinator, data.device, "Temperature Unit"),
        ]
    )


class ACInfinityDisplayBrightness(
    ActiveBluetoothCoordinatorEntity[ACInfinityDataUpdateCoordinator], SelectEntity, RestoreEntity
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
            sw_version=str(device.state.version),
            connections={(dr.CONNECTION_BLUETOOTH, device.address)},
        )
        self._update_attrs()

    async def async_added_to_hass(self) -> None:
        """Restore the last known value; nothing reads it until a command connects."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in self.options:
            self._restore(last.state)
            self._update_attrs()

    @callback
    def _restore(self, option: str) -> None:
        if self._device.state.display_brightness is None:
            self._device.state.display_brightness = BRIGHTNESS_LEVEL_TO_BYTE[int(option)]

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


class ACInfinityTemperatureUnit(ACInfinityDisplayBrightness):
    """The unit the vent displays (cmd-3 setting key 0x20)."""

    _attr_icon = "mdi:temperature-fahrenheit"
    _attr_options = [UnitOfTemperature.FAHRENHEIT, UnitOfTemperature.CELSIUS]

    @callback
    def _restore(self, option: str) -> None:
        if self._device.state.unit_celsius is None:
            self._device.state.unit_celsius = option == UnitOfTemperature.CELSIUS

    async def async_select_option(self, option: str) -> None:
        await self._device.set_temperature_unit(option == UnitOfTemperature.CELSIUS)
        self._write_state_from_device()

    @callback
    def _update_attrs(self) -> None:
        unit = self._device.state.unit_celsius
        if unit is None:
            self._attr_current_option = None
        else:
            self._attr_current_option = (
                UnitOfTemperature.CELSIUS if unit else UnitOfTemperature.FAHRENHEIT
            )


def _key(mapping: dict, value) -> int:
    return next(k for k, v in mapping.items() if v == value)


def _str(value) -> str | None:
    return None if value is None else str(value)


class CloudSelect(CloudEntity, SelectEntity):
    """A choice on a type 48 vent, read and written through the AC Infinity cloud."""

    def __init__(self, data: ACInfinityCloudData, name: str, options, read, write) -> None:
        super().__init__(data, name)
        self._attr_name = name
        self._attr_options = options
        self._read = read
        self._write = write

    @property
    def current_option(self) -> str | None:
        return self._read(self.coordinator)

    async def async_select_option(self, option: str) -> None:
        await self._send(self._write(self.coordinator, option))
