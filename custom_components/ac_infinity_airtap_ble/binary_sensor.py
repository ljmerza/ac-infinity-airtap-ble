"""Binary sensors. Only cloud entries (type 48) have any: whether the vent is open."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .cloud_entity import CloudEntity
from .const import DOMAIN
from .models import ACInfinityCloudData


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    if isinstance(data, ACInfinityCloudData):
        async_add_entities([CloudVentOpen(data, "Vent")])


class CloudVentOpen(CloudEntity, BinarySensorEntity):
    """Whether the vent's louvers are open (deviceInfo.isWindSteering, 0 = closed)."""

    _attr_name = "Vent"
    _attr_device_class = BinarySensorDeviceClass.OPENING

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.info.get("isWindSteering")
        return None if value is None else value != 0
