from __future__ import annotations

import asyncio
import contextlib
import logging

import async_timeout
from ac_infinity_ble.const import MANUFACTURER_ID
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

    @callback
    def _async_handle_bluetooth_event(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Handle a Bluetooth event."""
        self.logger.debug("%s (%s) received: %s",
                          self.ble_device.name,
                          self.ble_device.address,
                          service_info.advertisement)
        # Always keep the freshest BLEDevice for the next command. AC Infinity
        # manufacturer data (2306) is only present in some advertisements; when
        # it is, refresh state from it.
        self.ble_device = service_info.device
        self.controller._ble_device = service_info.device
        if MANUFACTURER_ID in service_info.advertisement.manufacturer_data:
            self.controller.set_ble_device_and_advertisement_data(
                service_info.device, service_info.advertisement
            )
        if self.controller.name:
            self._device_ready.set()
        self.logger.debug("%s (%s) state after advertisement: %s",
                          self.ble_device.name,
                          self.ble_device.address,
                          self.controller.state)
        super()._async_handle_bluetooth_event(service_info, change)

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
