from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.number import (NumberEntityDescription, NumberMode,
                                             RestoreNumber)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify

from .const import DEVICE_MODEL, DOMAIN, MANUFACTURER
from .coordinator import (ACInfinityDataUpdateCoordinator,
                          ActiveBluetoothCoordinatorEntity)
from .device import (AUTO_TRIGGER_RANGE_C, AUTO_TRIGGER_RANGE_F,
                     CALIBRATION_RANGE_C, CALIBRATION_RANGE_F, OFF_SPEED_RANGE,
                     TRANSITION_RANGE_C, TRANSITION_RANGE_F, ACInfinityDevice,
                     c_delta_to_f, c_to_f, f_delta_to_c, f_to_c)
from .models import ACInfinityData


@dataclass(frozen=True, kw_only=True)
class TemperatureNumberDescription(NumberEntityDescription):
    """A vent setting stored in both units; shown in the unit the vent displays."""

    field: str
    range_f: tuple[int, int]
    range_c: tuple[int, int]
    to_c: Callable[[int], int]
    to_f: Callable[[int], int]
    setter: Callable[[ACInfinityDevice, int], Awaitable[None]]


TEMPERATURE_NUMBERS = (
    TemperatureNumberDescription(
        key="temperature_calibration",
        name="Temperature Calibration",
        icon="mdi:thermometer-check",
        field="calibration",
        range_f=CALIBRATION_RANGE_F,
        range_c=CALIBRATION_RANGE_C,
        to_c=f_delta_to_c,
        to_f=c_delta_to_f,
        setter=ACInfinityDevice.set_calibration,
    ),
    TemperatureNumberDescription(
        key="auto_transition",
        name="Auto Transition",
        icon="mdi:stairs-up",
        field="transition",
        range_f=TRANSITION_RANGE_F,
        range_c=TRANSITION_RANGE_C,
        to_c=f_delta_to_c,
        to_f=c_delta_to_f,
        setter=ACInfinityDevice.set_transition,
    ),
    TemperatureNumberDescription(
        key="auto_high_temperature",
        name="Auto High Temperature",
        icon="mdi:thermometer-chevron-up",
        field="auto_high",
        range_f=AUTO_TRIGGER_RANGE_F,
        range_c=AUTO_TRIGGER_RANGE_C,
        to_c=f_to_c,
        to_f=c_to_f,
        setter=ACInfinityDevice.set_auto_high,
    ),
    TemperatureNumberDescription(
        key="auto_low_temperature",
        name="Auto Low Temperature",
        icon="mdi:thermometer-chevron-down",
        field="auto_low",
        range_f=AUTO_TRIGGER_RANGE_F,
        range_c=AUTO_TRIGGER_RANGE_C,
        to_c=f_to_c,
        to_f=c_to_f,
        setter=ACInfinityDevice.set_auto_low,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data: ACInfinityData = hass.data[DOMAIN][entry.entry_id]
    entities: list[ACInfinityNumber] = [ACInfinityOffSpeed(data.coordinator, data.device)]
    entities += [
        ACInfinityTemperatureNumber(data.coordinator, data.device, description)
        for description in TEMPERATURE_NUMBERS
    ]
    async_add_entities(entities)


class ACInfinityNumber(
    ActiveBluetoothCoordinatorEntity[ACInfinityDataUpdateCoordinator], RestoreNumber
):
    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1

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
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._restore(int(last.native_value), last.native_unit_of_measurement)
            self._update_attrs()

    @callback
    def _restore(self, value: int, unit: str | None) -> None:
        raise NotImplementedError

    @callback
    def _update_attrs(self) -> None:
        raise NotImplementedError

    @callback
    def _write_state_from_device(self) -> None:
        self._update_attrs()
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_attrs()
        super()._handle_coordinator_update()


class ACInfinityOffSpeed(ACInfinityNumber):
    """The speed the vent runs at in off mode (cmd-3 setting key 0x11)."""

    _attr_icon = "mdi:fan-minus"
    _attr_native_min_value = OFF_SPEED_RANGE[0]
    _attr_native_max_value = OFF_SPEED_RANGE[1]

    def __init__(
        self, coordinator: ACInfinityDataUpdateCoordinator, device: ACInfinityDevice
    ) -> None:
        super().__init__(coordinator, device, "Off Speed")

    @callback
    def _restore(self, value: int, unit: str | None) -> None:
        if self._device.state.level_off is None:
            self._device.state.level_off = value

    async def async_set_native_value(self, value: float) -> None:
        await self._device.set_off_speed(int(value))
        self._write_state_from_device()

    @callback
    def _update_attrs(self) -> None:
        self._attr_native_value = self._device.state.level_off


class ACInfinityTemperatureNumber(ACInfinityNumber):
    entity_description: TemperatureNumberDescription

    def __init__(
        self,
        coordinator: ACInfinityDataUpdateCoordinator,
        device: ACInfinityDevice,
        description: TemperatureNumberDescription,
    ) -> None:
        self.entity_description = description
        super().__init__(coordinator, device, description.name)

    @property
    def _celsius(self) -> bool:
        return bool(self._device.state.unit_celsius)

    @property
    def native_unit_of_measurement(self) -> str:
        return UnitOfTemperature.CELSIUS if self._celsius else UnitOfTemperature.FAHRENHEIT

    @property
    def _range(self) -> tuple[int, int]:
        description = self.entity_description
        return description.range_c if self._celsius else description.range_f

    @property
    def native_min_value(self) -> float:
        return self._range[0]

    @property
    def native_max_value(self) -> float:
        return self._range[1]

    @callback
    def _restore(self, value: int, unit: str | None) -> None:
        description = self.entity_description
        state = self._device.state
        if getattr(state, f"{description.field}_f") is not None:
            return
        if unit == UnitOfTemperature.CELSIUS:
            f, c = description.to_f(value), value
        else:
            f, c = value, description.to_c(value)
        setattr(state, f"{description.field}_f", f)
        setattr(state, f"{description.field}_c", c)

    async def async_set_native_value(self, value: float) -> None:
        await self.entity_description.setter(self._device, int(value))
        self._write_state_from_device()

    @callback
    def _update_attrs(self) -> None:
        unit = "c" if self._celsius else "f"
        self._attr_native_value = getattr(
            self._device.state, f"{self.entity_description.field}_{unit}"
        )
