"""Shared base for entities of a vent controlled through the AC Infinity cloud (type 48)."""

from __future__ import annotations

from collections.abc import Awaitable

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .cloud import AirtapCloudCoordinator, CloudError
from .const import DEVICE_MODEL, MANUFACTURER, TYPE_AIRTAP_AI
from .models import ACInfinityCloudData


def cloud_device_info(data: ACInfinityCloudData) -> DeviceInfo:
    """Device info for a cloud entry, shared by its entities."""
    device = data.coordinator.data or {}
    return DeviceInfo(
        name=data.title,
        model=DEVICE_MODEL[TYPE_AIRTAP_AI],
        manufacturer=MANUFACTURER,
        sw_version=device.get("firmwareVersion"),
        hw_version=device.get("hardwareVersion"),
        connections={(dr.CONNECTION_BLUETOOTH, data.address)},
    )


class CloudEntity(CoordinatorEntity[AirtapCloudCoordinator]):
    """An entity of a type 48 vent, read from and written to the AC Infinity cloud."""

    _attr_has_entity_name = True

    def __init__(self, data: ACInfinityCloudData, name: str) -> None:
        super().__init__(data.coordinator)
        self._attr_unique_id = f"{data.address}_{slugify(name)}"
        self._attr_device_info = cloud_device_info(data)

    async def _send(self, write: Awaitable[None]) -> None:
        """Run a cloud write, turning a refusal into an error Home Assistant shows."""
        try:
            await write
        except CloudError as err:
            raise HomeAssistantError(f"{self.coordinator.name}: {err}") from err
