from __future__ import annotations

import dataclasses
import logging
from typing import Any

from ac_infinity_ble.const import MANUFACTURER_ID
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
    async_discovered_service_info,
)
from homeassistant.const import CONF_ADDRESS, CONF_SERVICE_DATA
from homeassistant.data_entry_flow import FlowResult

from .const import BLEAK_EXCEPTIONS, DOMAIN
from .device import ACInfinityDevice, DeviceInfoEx
from .legacy import async_adopt_legacy_entry, async_find_legacy_entry

_LOGGER = logging.getLogger(__name__)


def parse_manufacturer_data(data: bytes) -> DeviceInfoEx:
    from ac_infinity_ble.protocol import parse_manufacturer_data as parse
    return DeviceInfoEx.create(parse(data))


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):

    VERSION = 1

    def __init__(self) -> None:
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> FlowResult:
        """Handle the bluetooth discovery step."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        # Upgrading from 1.x: adopt the orphaned ac_infinity entry for this
        # device silently, so the domain rename costs the user nothing. No
        # BLE round-trip here — the vent may not be reachable at startup, and
        # the stored data is already known-good.
        if legacy := async_find_legacy_entry(self.hass, discovery_info.address):
            data = await async_adopt_legacy_entry(self.hass, legacy)
            return self.async_create_entry(title=legacy.title, data=data)

        self._discovery_info = discovery_info
        device: DeviceInfoEx = parse_manufacturer_data(
            discovery_info.advertisement.manufacturer_data[MANUFACTURER_ID]
        )
        self.context["title_placeholders"] = {"name": device.name}
        return await self.async_step_user()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the user step to pick discovered device."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            discovery_info = self._discovered_devices[address]
            await self.async_set_unique_id(
                discovery_info.address, raise_on_progress=False
            )
            self._abort_if_unique_id_configured()

            # Reached when the user adds the integration by hand rather than
            # via discovery; adopt here as well so a 1.x entry is never left
            # orphaned alongside a duplicate new one.
            if legacy := async_find_legacy_entry(self.hass, address):
                data = await async_adopt_legacy_entry(self.hass, legacy)
                return self.async_create_entry(title=legacy.title, data=data)

            controller = ACInfinityDevice(
                discovery_info.device, advertisement_data=discovery_info.advertisement
            )
            try:
                await controller.update()
            except BLEAK_EXCEPTIONS:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected error")
                errors["base"] = "unknown"
            else:
                await controller.stop()
                return self.async_create_entry(
                    title=controller.name,
                    data={
                        CONF_ADDRESS: discovery_info.address,
                        CONF_SERVICE_DATA: parse_manufacturer_data(
                            discovery_info.advertisement.manufacturer_data[
                                MANUFACTURER_ID
                            ]
                        ),
                    },
                )

        if discovery := self._discovery_info:
            self._discovered_devices[discovery.address] = discovery
        else:
            current_addresses = self._async_current_ids()
            for discovery in async_discovered_service_info(self.hass):
                if (
                    discovery.address in current_addresses
                    or discovery.address in self._discovered_devices
                ):
                    continue
                if MANUFACTURER_ID not in discovery.advertisement.manufacturer_data:
                    continue
                self._discovered_devices[discovery.address] = discovery

        # No AC Infinity (manufacturer 2306) devices advertising — fall back to
        # entering a BLE MAC address manually and connecting to it directly.
        if not self._discovered_devices:
            return await self.async_step_manual()

        _LOGGER.debug("Discovered devices: %s", self._discovered_devices)

        devices = {}
        for service_info in self._discovered_devices.values():
            device = parse_manufacturer_data(
                service_info.advertisement.manufacturer_data[MANUFACTURER_ID]
            )
            devices[service_info.address] = f"{device.name} ({service_info.address})"

        data_schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS): vol.In(devices),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add a device by BLE MAC address without relying on a 2306 advert."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper()
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()

            # The AirTap does not reliably advertise its manufacturer data, so
            # users upgrading from 1.x may never hit the discovery path. Adopt
            # here too rather than making them delete and re-add.
            if legacy := async_find_legacy_entry(self.hass, address):
                data = await async_adopt_legacy_entry(self.hass, legacy)
                return self.async_create_entry(title=legacy.title, data=data)

            ble_device = async_ble_device_from_address(self.hass, address, True)
            if ble_device is None:
                errors["base"] = "cannot_connect"
            else:
                # Minimal seed state; real state is read over GATT in update().
                # type 6 == "Airtap Series" (see const.DEVICE_MODEL).
                device_info = DeviceInfoEx(
                    type=6, name=ble_device.name or address, version=0
                )
                controller = ACInfinityDevice(ble_device, state=device_info)
                try:
                    await controller.update()
                except BLEAK_EXCEPTIONS:
                    errors["base"] = "cannot_connect"
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Unexpected error")
                    errors["base"] = "unknown"
                else:
                    await controller.stop()
                    return self.async_create_entry(
                        title=controller.name or address,
                        data={
                            CONF_ADDRESS: address,
                            CONF_SERVICE_DATA: dataclasses.asdict(device_info),
                        },
                    )

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_ADDRESS, default="A4:C1:38:41:4C:80"
                ): str,
            }
        )
        return self.async_show_form(
            step_id="manual",
            data_schema=data_schema,
            errors=errors,
        )
