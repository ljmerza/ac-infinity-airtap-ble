from __future__ import annotations

from homeassistant.components.bluetooth.passive_update_coordinator import \
    PassiveBluetoothCoordinatorEntity
from homeassistant.components.sensor import (SensorDeviceClass, SensorEntity,
                                             SensorStateClass)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfPressure, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .cloud import AirtapCloudCoordinator
from .const import DEVICE_MODEL, DOMAIN, FAMILY_E_MODELS, MANUFACTURER
from .coordinator import ACInfinityDataUpdateCoordinator
from .device import ACInfinityDevice
from .fan import cloud_device_info
from .models import ACInfinityCloudData, ACInfinityData


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data: ACInfinityData | ACInfinityCloudData = hass.data[DOMAIN][entry.entry_id]
    if isinstance(data, ACInfinityCloudData):
        async_add_entities(
            [
                CloudTemperatureSensor(data, "Temperature"),
                CloudCurrentSpeedSensor(data, "Current Speed"),
            ]
        )
        return
    entities = [
        TemperatureSensor(data.coordinator, data.device, "Temperature"),
        CurrentSpeedSensor(data.coordinator, data.device, "Current Speed"),
    ]

    if data.device.state.type not in [6]:  # Airtap does not have humidity
        entities.append(HumiditySensor(data.coordinator, data.device, "Humidity"))

    if data.device.state.version >= 3 and data.device.state.type in FAMILY_E_MODELS:
        entities.append(VpdSensor(data.coordinator, data.device, "VPD"))
    async_add_entities(entities)


class ACInfinitySensor(
    PassiveBluetoothCoordinatorEntity[ACInfinityDataUpdateCoordinator], SensorEntity
):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ACInfinityDataUpdateCoordinator,
        device: ACInfinityDevice,
        name: str,
    ) -> None:
        super().__init__(coordinator)
        self._device = device
        self._name = name
        self._attr_unique_id = f"{self._device.address}_{slugify(name)}"
        self._attr_device_info = DeviceInfo(
            name=device.name,
            model=DEVICE_MODEL[device.state.type],
            manufacturer=MANUFACTURER,
            sw_version=str(device.state.version),
            connections={(dr.CONNECTION_BLUETOOTH, device.address)},
        )

    @callback
    def _update_attrs(self) -> None:
        """Handle updating _attr values."""
        raise NotImplementedError("Not yet implemented.")

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_attrs()
        super()._handle_coordinator_update()


class TemperatureSensor(ACInfinitySensor):
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    @callback
    def _update_attrs(self) -> None:
        """Handle updating _attr values."""
        self._attr_native_value = self._device.temperature


class CurrentSpeedSensor(ACInfinitySensor):
    """The speed the vent reports, including in Auto and off mode."""

    _attr_name = "Current Speed"
    _attr_icon = "mdi:fan"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @callback
    def _update_attrs(self) -> None:
        """Handle updating _attr values."""
        self._attr_native_value = self._device.state.current_speed


class HumiditySensor(ACInfinitySensor):
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_state_class = SensorStateClass.MEASUREMENT

    @callback
    def _update_attrs(self) -> None:
        """Handle updating _attr values."""
        self._attr_native_value = self._device.humidity


class VpdSensor(ACInfinitySensor):
    _attr_native_unit_of_measurement = UnitOfPressure.KPA
    _attr_device_class = SensorDeviceClass.ATMOSPHERIC_PRESSURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    @callback
    def _update_attrs(self) -> None:
        """Handle updating _attr values."""
        self._attr_native_value = self._device.vpd


class CloudSensor(CoordinatorEntity[AirtapCloudCoordinator], SensorEntity):
    """A reading from a type 48 vent's entry in the AC Infinity cloud."""

    _attr_has_entity_name = True

    def __init__(self, data: ACInfinityCloudData, name: str) -> None:
        super().__init__(data.coordinator)
        self._attr_unique_id = f"{data.address}_{slugify(name)}"
        self._attr_device_info = cloud_device_info(data)


class CloudTemperatureSensor(CloudSensor):
    """deviceInfo.temperature, in hundredths of a degree C."""

    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        value = self.coordinator.info.get("temperature")
        return value / 100 if isinstance(value, (int, float)) else None


class CloudCurrentSpeedSensor(CloudSensor):
    """The speed the vent is running at (deviceInfo.speak), in any mode."""

    _attr_name = "Current Speed"
    _attr_icon = "mdi:fan"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        return self.coordinator.info.get("speak")
