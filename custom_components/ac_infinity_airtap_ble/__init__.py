from __future__ import annotations

import logging

from ac_infinity_ble import DeviceInfo
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ADDRESS,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_SERVICE_DATA,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .cloud import AcInfinityCloud, AirtapCloudCoordinator
from .const import CONF_CLOUD, CONF_DEV_ID, DOMAIN
from .coordinator import ACInfinityDataUpdateCoordinator
from .device import ACInfinityDevice, DeviceInfoEx
from .models import ACInfinityCloudData, ACInfinityData

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.FAN,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

# Cloud entries get what the cloud can do for an AIRTAP. Each platform only creates the
# entities whose fields the cloud reports for the vent.
CLOUD_PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.FAN,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if cloud := entry.data.get(CONF_CLOUD):
        return await _async_setup_cloud_entry(hass, entry, cloud)

    address: str = entry.data[CONF_ADDRESS]
    ble_device = bluetooth.async_ble_device_from_address(hass, address.upper(), True)
    if not ble_device:
        raise ConfigEntryNotReady(
            f"Could not find AC Infinity device with address {address}"
        )

    service_data = entry.data[CONF_SERVICE_DATA]
    if type(service_data) is dict:
        device_info = DeviceInfoEx(**service_data)
    elif type(service_data) is DeviceInfoEx:
        device_info = service_data
    elif type(service_data) is DeviceInfo:
        device_info = DeviceInfoEx.create(service_data)
    else:
        raise ValueError(
            f"Unexpected config entry service data type: {type(service_data)}"
        )

    _LOGGER.debug(
        "Setting up %s [%s] (type %s, version %s)",
        entry.title,
        address,
        device_info.type,
        device_info.version,
    )
    device = ACInfinityDevice(ble_device, device_info)
    coordinator = ACInfinityDataUpdateCoordinator(hass, _LOGGER, ble_device, device)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = ACInfinityData(
        entry.title, device, coordinator
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(coordinator.async_start())

    return True


async def _async_setup_cloud_entry(hass: HomeAssistant, entry: ConfigEntry, cloud: dict) -> bool:
    """A type 48 vent: poll and control it through the AC Infinity cloud."""
    client = AcInfinityCloud(hass, cloud[CONF_EMAIL], cloud[CONF_PASSWORD])
    coordinator = AirtapCloudCoordinator(hass, entry, client, cloud[CONF_DEV_ID])
    # Raises ConfigEntryNotReady while the cloud can't be reached.
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = ACInfinityCloudData(
        entry.title, entry.data[CONF_ADDRESS], coordinator
    )
    await hass.config_entries.async_forward_entry_setups(entry, CLOUD_PLATFORMS)
    return True
