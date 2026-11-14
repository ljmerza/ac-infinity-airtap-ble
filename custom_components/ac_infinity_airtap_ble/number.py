from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.number import (NumberDeviceClass, NumberEntity,
                                             NumberEntityDescription, NumberMode,
                                             RestoreNumber)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify

from .cloud import (ID_AI_THRESHOLD, ID_CALIBRATION, ID_COOL_TRIGGER, ID_HEAT_TRIGGER,
                    AirtapCloudCoordinator)
from .cloud_entity import CloudEntity
from .const import DEVICE_MODEL, DOMAIN, MANUFACTURER
from .coordinator import (ACInfinityDataUpdateCoordinator,
                          ActiveBluetoothCoordinatorEntity)
from .device import (AUTO_TRIGGER_RANGE_C, AUTO_TRIGGER_RANGE_F,
                     CALIBRATION_RANGE_C, CALIBRATION_RANGE_F, OFF_SPEED_RANGE,
                     TRANSITION_RANGE_C, TRANSITION_RANGE_F, ACInfinityDevice,
                     c_delta_to_f, c_to_f, f_delta_to_c, f_to_c)
from .models import ACInfinityCloudData, ACInfinityData


@dataclass(frozen=True, kw_only=True)
class CloudNumberDescription(NumberEntityDescription):
    """A setting of a type 48 vent in the AC Infinity cloud, shown in °F.

    read returns the °F value; write sends a °F value (with its °C twin) back.
    """

    read: Callable[[AirtapCloudCoordinator], float | None]
    write: Callable[[AirtapCloudCoordinator, int], Awaitable[None]]


def _cool_heat_c(value_f: int) -> int:
    """The °C twin the app sends with a Cool or Heat trigger (16-31 °C)."""
    return min(31, max(16, round(f_to_c(value_f))))


# Ranges from the app (ControlAirTapModel, SensorVModel): triggers 60-88 °F / 16-31 °C,
# Temp AI threshold 2-6 °F / 1-3 °C, calibration ±20 °F stored as °C with °F = 2 x °C.
CLOUD_NUMBERS = (
    CloudNumberDescription(
        key="targetTempF", name="Cool Trigger", device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        native_min_value=60, native_max_value=88, native_step=1, mode=NumberMode.BOX,
        read=lambda c: c.mode_value("targetTempF"),
        write=lambda c, f: c.set_control(ID_COOL_TRIGGER, targetTempF=f, targetTemp=_cool_heat_c(f)),
    ),
    CloudNumberDescription(
        key="waterTempHighValueF", name="Heat Trigger", device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        native_min_value=60, native_max_value=88, native_step=1, mode=NumberMode.BOX,
        read=lambda c: c.mode_value("waterTempHighValueF"),
        write=lambda c, f: c.set_control(
            ID_HEAT_TRIGGER, waterTempHighValueF=f, waterTempHighValue=_cool_heat_c(f)
        ),
    ),
    CloudNumberDescription(
        key="devLtf", name="Temp AI Threshold", icon="mdi:thermometer-lines",
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        native_min_value=2, native_max_value=6, native_step=1, mode=NumberMode.SLIDER,
        read=lambda c: c.mode_value("devLtf"),
        write=lambda c, f: c.set_control(ID_AI_THRESHOLD, devLtf=f, devLt=max(1, min(3, round(f / 2)))),
    ),
    CloudNumberDescription(
        key="devCth", name="Temperature Calibration", icon="mdi:thermometer-plus",
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        native_min_value=-20, native_max_value=20, native_step=2, mode=NumberMode.BOX,
        read=lambda c: c.settings.get("devCth"),
        write=lambda c, f: c.set_setting([ID_CALIBRATION], devCth=f, devCt=f // 2),
    ),
)


class CloudNumber(CloudEntity, NumberEntity):
    """A number on a type 48 vent, read and written through the AC Infinity cloud."""

    entity_description: CloudNumberDescription

    def __init__(self, data: ACInfinityCloudData, description: CloudNumberDescription) -> None:
        super().__init__(data, description.name)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        return self.entity_description.read(self.coordinator)

    async def async_set_native_value(self, value: float) -> None:
        await self._send(self.entity_description.write(self.coordinator, int(value)))


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
    data: ACInfinityData | ACInfinityCloudData = hass.data[DOMAIN][entry.entry_id]
    if isinstance(data, ACInfinityCloudData):
        # Only what the cloud reports for this vent.
        async_add_entities(
            CloudNumber(data, description)
            for description in CLOUD_NUMBERS
            if description.read(data.coordinator) is not None
        )
        return
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
