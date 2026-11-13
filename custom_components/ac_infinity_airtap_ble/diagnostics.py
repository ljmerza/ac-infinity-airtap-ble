"""Download diagnostics: what the vent last advertised and who can reach it."""

from __future__ import annotations

import dataclasses
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_EMAIL, CONF_PASSWORD, CONF_SERVICE_DATA
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .models import ACInfinityCloudData, ACInfinityData

# The cloud login, and anything in a cloud reply that could identify the account.
TO_REDACT = {CONF_EMAIL, CONF_PASSWORD, "appEmail", "appId", "userId", "token", "email"}


def _as_dict(value: Any) -> Any:
    return dataclasses.asdict(value) if dataclasses.is_dataclass(value) else value


def _advertisement(
    info: bluetooth.BluetoothServiceInfoBleak | None,
) -> dict[str, Any] | None:
    if info is None:
        return None
    return {
        "name": info.name,
        "source": info.source,
        "rssi": info.rssi,
        "tx_power": info.tx_power,
        "connectable": info.connectable,
        "seconds_ago": round(bluetooth.MONOTONIC_TIME() - info.time, 1),
        "service_uuids": info.service_uuids,
        "manufacturer_data": {str(k): v.hex() for k, v in info.manufacturer_data.items()},
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    address = entry.data[CONF_ADDRESS].upper()
    data: ACInfinityData | ACInfinityCloudData | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    return {
        "entry_data": async_redact_data(
            {**entry.data, CONF_SERVICE_DATA: _as_dict(entry.data.get(CONF_SERVICE_DATA))},
            TO_REDACT,
        ),
        "state": _as_dict(data.device.state) if isinstance(data, ACInfinityData) else None,
        "cloud_device": async_redact_data(data.coordinator.data or {}, TO_REDACT)
        if isinstance(data, ACInfinityCloudData)
        else None,
        "scanners": {
            "connectable": bluetooth.async_scanner_count(hass, connectable=True),
            "total": bluetooth.async_scanner_count(hass, connectable=False),
        },
        "seen_by_connectable_scanner": bluetooth.async_ble_device_from_address(
            hass, address, True
        )
        is not None,
        "last_advertisement_connectable": _advertisement(
            bluetooth.async_last_service_info(hass, address, True)
        ),
        "last_advertisement_any": _advertisement(
            bluetooth.async_last_service_info(hass, address, False)
        ),
    }
