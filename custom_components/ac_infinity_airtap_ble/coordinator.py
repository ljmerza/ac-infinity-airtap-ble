from __future__ import annotations

import asyncio
import contextlib
import logging

import async_timeout
from ac_infinity_ble import DeviceInfo
from ac_infinity_ble.const import CallbackType, MANUFACTURER_ID
from bleak.backends.device import BLEDevice
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.passive_update_coordinator import \
    PassiveBluetoothDataUpdateCoordinator
from homeassistant.helpers.update_coordinator import (
    BaseCoordinatorEntity
)
from homeassistant.core import HomeAssistant, callback

from .device import ACInfinityDevice

DEVICE_STARTUP_TIMEOUT = 30


class ACInfinityDataUpdateCoordinator(PassiveBluetoothDataUpdateCoordinator):
    """Follow the vent's advertisements; never connect just to read it.

    The vent can stop advertising for good after a failed connection attempt,
    until it is power cycled. Polling meant about 70 connections an hour, so
    GATT connections are now only made to carry a user command. Temperature comes
    from the advertisement's manufacturer data. Active scanning is still requested
    because that data arrives in the scan response.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        ble_device: BLEDevice,
        controller: ACInfinityDevice,
    ) -> None:
        super().__init__(
            hass=hass,
            logger=logger,
            address=ble_device.address,
            mode=bluetooth.BluetoothScanningMode.ACTIVE,
            connectable=True,
        )
        self.ble_device = ble_device
        self.controller = controller
        self._device_ready = asyncio.Event()
        self._last_adverts: dict[str, bytes] = {}
        controller.register_callback(self._handle_device_callback)

    @callback
    def _async_handle_bluetooth_event(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Handle a Bluetooth event."""
        self.logger.debug("%s (%s) received: %s source=%s",
                          self.ble_device.name,
                          self.ble_device.address,
                          service_info.advertisement,
                          service_info.source)
        # Always keep the freshest BLEDevice for the next command. AC Infinity
        # manufacturer data (2306) is only present in some advertisements; when
        # it is, refresh state from it.
        self.ble_device = service_info.device
        self.controller._ble_device = service_info.device
        data = service_info.advertisement.manufacturer_data.get(MANUFACTURER_ID)
        if data:
            self._handle_manufacturer_data(service_info, data)
        if self.controller.name:
            self._device_ready.set()
        self.logger.debug("%s (%s) state after advertisement: %s",
                          self.ble_device.name,
                          self.ble_device.address,
                          self.controller.state)
        super()._async_handle_bluetooth_event(service_info, change)

    def _handle_manufacturer_data(
        self, service_info: bluetooth.BluetoothServiceInfoBleak, data: bytes
    ) -> None:
        """Take only fresh data from an advertisement.

        A proxy can keep passing on an old copy of this data long after the vent changed
        it, even across a restart: one replayed speed 3 for minutes while the vent ran
        at 4. So a repeat from the same proxy, and a proxy's first copy, aren't trusted.
        The very first copy overall still gives a temperature to start from.
        """
        source = service_info.source
        previous = self._last_adverts.get(source)
        first_overall = not self._last_adverts
        self._last_adverts[source] = data
        if previous == data or (previous is None and not first_overall):
            self.logger.debug(
                "%s: ignoring %s advertisement from %s: %s",
                self.ble_device.address,
                "repeated" if previous == data else "proxy's first",
                source,
                data.hex(),
            )
            return
        self.controller.set_ble_device_and_advertisement_data(
            service_info.device, service_info.advertisement, use_speed=previous is not None
        )

    def _handle_device_callback(self, _state: DeviceInfo, callback_type: CallbackType) -> None:
        """Show values read over a connection, which arrive outside any advertisement."""
        if callback_type == CallbackType.UPDATE_RESPONSE:
            self.async_update_listeners()

    async def async_wait_ready(self) -> bool:
        """Wait for the device to be ready."""
        with contextlib.suppress(asyncio.TimeoutError):
            async with async_timeout.timeout(DEVICE_STARTUP_TIMEOUT):
                await self._device_ready.wait()
                return True
        return False


class ActiveBluetoothCoordinatorEntity[
    _ActiveBluetoothDataUpdateCoordinatorT: PassiveBluetoothDataUpdateCoordinator = PassiveBluetoothDataUpdateCoordinator
](
    BaseCoordinatorEntity[_ActiveBluetoothDataUpdateCoordinatorT]
):
    """A class for entities that send commands, so must stay usable between advertisements."""

    async def async_update(self) -> None:
        """Only allow updates via the coordinator, not on demand."""

    @property
    def available(self) -> bool:
        """Always available.

        This device is command-driven and connected on demand. Keeping control
        available lets a command still be attempted if advertisements lapse;
        reachability surfaces via command success/failure instead.
        """
        return True
