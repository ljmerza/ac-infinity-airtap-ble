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
from homeassistant.const import CONF_ADDRESS, CONF_EMAIL, CONF_PASSWORD, CONF_SERVICE_DATA
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .cloud import (
    AcInfinityCloud,
    CloudAuthError,
    CloudError,
    ble_address_from_cloud_mac,
    mac_candidates,
    normalize_mac,
)
from .const import BLEAK_EXCEPTIONS, CONF_CLOUD, CONF_DEV_ID, DOMAIN, TYPE_AIRTAP_AI
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
        # The type 48 vent being set up through the cloud, and whether a saved login
        # from another vent has been tried yet.
        self._cloud_target: BluetoothServiceInfoBleak | None = None
        self._tried_saved_login = False
        # Adding from the account without Bluetooth: the login, and the AirTaps to pick from.
        self._cloud_login: dict[str, str] = {}
        self._cloud_choices: dict[str, dict[str, Any]] = {}

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

            # Type 48 doesn't take commands over Bluetooth, only through the cloud.
            info = parse_manufacturer_data(
                discovery_info.advertisement.manufacturer_data[MANUFACTURER_ID]
            )
            if info.type == TYPE_AIRTAP_AI:
                self._cloud_target = discovery_info
                return await self.async_step_cloud()

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

        # No AC Infinity (manufacturer 2306) devices advertising. Offer the two ways that
        # don't need one: a Bluetooth MAC address, or the AC Infinity account, which is
        # all a Wi-Fi AirTap needs.
        if not self._discovered_devices:
            _LOGGER.warning(
                "No unconfigured AC Infinity advertisements (manufacturer %s) seen, "
                "offering a MAC address or the AC Infinity account instead",
                MANUFACTURER_ID,
            )
            return self.async_show_menu(
                step_id="no_devices", menu_options=["cloud_account", "manual"]
            )

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

    def _saved_login(self) -> dict[str, str] | None:
        """The cloud login of a vent already set up, so the next one doesn't ask again."""
        for entry in self._async_current_entries():
            if cloud := entry.data.get(CONF_CLOUD):
                return {CONF_EMAIL: cloud[CONF_EMAIL], CONF_PASSWORD: cloud[CONF_PASSWORD]}
        return None

    async def _find_in_account(
        self, email: str, password: str
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Log in and find the vent being set up. Returns (device, error key)."""
        target = self._cloud_target
        try:
            devices = await AcInfinityCloud(self.hass, email, password).devices()
        except CloudAuthError as err:
            _LOGGER.warning("AC Infinity login refused: %s", err)
            return None, "invalid_auth"
        except CloudError as err:
            _LOGGER.warning("Couldn't reach the AC Infinity cloud: %s", err)
            return None, "cloud_unreachable"
        advert = target.advertisement.manufacturer_data.get(MANUFACTURER_ID)
        wanted = mac_candidates(target.address, advert)
        for device in devices:
            if normalize_mac(device.get("devMacAddr", "")) in wanted:
                return device, None
        _LOGGER.warning(
            "No device on the AC Infinity account matches %s (tried %s). The account has: %s",
            target.address,
            sorted(wanted),
            [(d.get("devName"), d.get("devType"), d.get("devMacAddr")) for d in devices],
        )
        return None, "not_in_account"

    async def async_step_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Set up a type 48 vent through the AC Infinity cloud."""
        errors: dict[str, str] = {}
        login = user_input
        if login is None and not self._tried_saved_login:
            self._tried_saved_login = True
            login = self._saved_login()

        if login is not None:
            device, error = await self._find_in_account(login[CONF_EMAIL], login[CONF_PASSWORD])
            if device is not None:
                target = self._cloud_target
                info = parse_manufacturer_data(
                    target.advertisement.manufacturer_data[MANUFACTURER_ID]
                )
                return self.async_create_entry(
                    title=device.get("devName") or info.name,
                    data={
                        CONF_ADDRESS: target.address,
                        CONF_SERVICE_DATA: dataclasses.asdict(info),
                        CONF_CLOUD: {
                            CONF_EMAIL: login[CONF_EMAIL],
                            CONF_PASSWORD: login[CONF_PASSWORD],
                            CONF_DEV_ID: str(device["devId"]),
                        },
                    },
                )
            # A saved login that doesn't find this vent just falls through to the form.
            if user_input is not None:
                errors["base"] = error

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_EMAIL, default=(login or {}).get(CONF_EMAIL, "")
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL)),
                vol.Required(CONF_PASSWORD): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(step_id="cloud", data_schema=data_schema, errors=errors)

    async def async_step_cloud_account(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add a Wi-Fi AirTap from the AC Infinity account, without Bluetooth.

        For when Home Assistant doesn't hear the vent: the cloud is all it needs.
        """
        errors: dict[str, str] = {}
        login = user_input
        if login is None and not self._tried_saved_login:
            self._tried_saved_login = True
            login = self._saved_login()

        if login is not None:
            airtaps, error = await self._account_airtaps(login[CONF_EMAIL], login[CONF_PASSWORD])
            if airtaps:
                self._cloud_login = login
                self._cloud_choices = airtaps
                if len(airtaps) == 1:
                    return await self._create_account_entry(next(iter(airtaps.values())))
                return await self.async_step_cloud_pick()
            # A saved login that finds nothing just falls through to the form.
            if user_input is not None:
                errors["base"] = error

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_EMAIL, default=(login or {}).get(CONF_EMAIL, "")
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL)),
                vol.Required(CONF_PASSWORD): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(
            step_id="cloud_account", data_schema=data_schema, errors=errors
        )

    async def async_step_cloud_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Pick which Wi-Fi AirTap on the account to add."""
        if user_input is not None:
            return await self._create_account_entry(self._cloud_choices[user_input[CONF_DEV_ID]])
        options = {
            dev_id: f"{device.get('devName') or device.get('devCode')} ({device.get('devMacAddr')})"
            for dev_id, device in self._cloud_choices.items()
        }
        return self.async_show_form(
            step_id="cloud_pick",
            data_schema=vol.Schema({vol.Required(CONF_DEV_ID): vol.In(options)}),
        )

    async def _account_airtaps(
        self, email: str, password: str
    ) -> tuple[dict[str, dict[str, Any]], str | None]:
        """Log in and list the account's Wi-Fi AirTaps that aren't set up yet.

        Returns ({devId: device}, error key).
        """
        try:
            devices = await AcInfinityCloud(self.hass, email, password).devices()
        except CloudAuthError as err:
            _LOGGER.warning("AC Infinity login refused: %s", err)
            return {}, "invalid_auth"
        except CloudError as err:
            _LOGGER.warning("Couldn't reach the AC Infinity cloud: %s", err)
            return {}, "cloud_unreachable"
        configured = self._async_current_ids()
        airtaps = {
            str(device["devId"]): device
            for device in devices
            if (device.get("airTap") or device.get("devType") == TYPE_AIRTAP_AI)
            and device.get("devMacAddr")
            and ble_address_from_cloud_mac(device["devMacAddr"]) not in configured
        }
        if not airtaps:
            _LOGGER.warning(
                "No Wi-Fi AirTaps left to add on the AC Infinity account. The account has: %s",
                [(d.get("devName"), d.get("devType"), d.get("devMacAddr")) for d in devices],
            )
            return {}, "no_airtaps"
        return airtaps, None

    async def _create_account_entry(self, device: dict[str, Any]) -> FlowResult:
        """Create the entry for a Wi-Fi AirTap picked from the account.

        Keyed by its Bluetooth address (the account's MAC + 2), like an entry set up from
        Bluetooth discovery, so a later discovery of the same vent is already configured.
        """
        address = ble_address_from_cloud_mac(device["devMacAddr"])
        await self.async_set_unique_id(address, raise_on_progress=False)
        self._abort_if_unique_id_configured()
        info = DeviceInfoEx(
            type=TYPE_AIRTAP_AI,
            name=f"A-{device.get('devCode') or address}",
            version=device.get("devVersion") or 0,
        )
        login = self._cloud_login
        return self.async_create_entry(
            title=device.get("devName") or info.name,
            data={
                CONF_ADDRESS: address,
                CONF_SERVICE_DATA: dataclasses.asdict(info),
                CONF_CLOUD: {
                    CONF_EMAIL: login[CONF_EMAIL],
                    CONF_PASSWORD: login[CONF_PASSWORD],
                    CONF_DEV_ID: str(device["devId"]),
                },
            },
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
                _LOGGER.warning(
                    "%s: no Bluetooth adapter or proxy that can connect sees this address "
                    "right now (seen by a passive scanner: %s)",
                    address,
                    async_ble_device_from_address(self.hass, address, False) is not None,
                )
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
