from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import timedelta

import async_timeout
from ac_infinity_ble.const import MANUFACTURER_ID
from bleak.backends.device import BLEDevice
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.active_update_coordinator import \
    ActiveBluetoothDataUpdateCoordinator
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import (
    BaseCoordinatorEntity
)
from homeassistant.core import CoreState, HomeAssistant, callback

from .device import ACInfinityDevice

DEVICE_STARTUP_TIMEOUT = 30
# This AirTap emits byte-identical advertisements, which HA's bluetooth manager
# dedups -- so the advert-driven poll never re-fires. Poll on a fixed timer
# instead, connecting via whatever connectable device the manager currently has.
POLL_INTERVAL = timedelta(seconds=60)


class ACInfinityDataUpdateCoordinator(ActiveBluetoothDataUpdateCoordinator[None]):

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
            needs_poll_method=self._needs_poll,
            poll_method=self._async_update,
            mode=bluetooth.BluetoothScanningMode.ACTIVE,
            connectable=True,
        )
        self.ble_device = ble_device
        self.controller = controller
        self._device_ready = asyncio.Event()

    @callback
    def async_start(self):
        """Start advert tracking plus a fixed-interval telemetry poll.

        The advert-driven poll alone stalls on this device (its identical
        advertisements get deduped by HA), so add a timer that connects and
        reads temperature only.
        """
        cancel_bt = super().async_start()
        cancel_timer = async_track_time_interval(
            self.hass, self._async_poll_telemetry, POLL_INTERVAL
        )
        self.hass.async_create_task(self._async_poll_telemetry())

        @callback
        def _cancel() -> None:
            cancel_timer()
            cancel_bt()

        return _cancel

    async def _async_poll_telemetry(self, now=None) -> None:
        """Connect briefly to capture a 0x1EFF temperature push.

        TELEMETRY ONLY: this deliberately does NOT read model-data / work_type,
        so the commanded on/off/speed state is never disturbed. The device emits
        a 0x1EFF status frame shortly after connect, which the controller's
        notification handler parses into temperature (and only temperature).
        """
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            self.logger.debug("%s: telemetry poll skipped (no device)", self.address)
            return
        self.ble_device = ble_device
        self.controller._ble_device = ble_device
        try:
            await self.controller._ensure_connected()
            await asyncio.sleep(5)
        except Exception as err:  # noqa: BLE001
            self.logger.debug("%s: telemetry poll error: %s", self.address, err)
        finally:
            with contextlib.suppress(Exception):
                await self.controller._execute_disconnect()
        self.async_update_listeners()

    @callback
    def _needs_poll(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        seconds_since_last_poll: float | None,
    ) -> bool:
        return (
            self.hass.state == CoreState.running
            and self.controller.update_needed(seconds_since_last_poll)
            and bool(
                bluetooth.async_ble_device_from_address(
                    self.hass, service_info.device.address, connectable=True
                )
            )
        )

    async def _async_update(
        self, service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> None:
        """Poll the device."""
        await self.controller.update()
        self.logger.debug("%s (%s) state after poll: %s",
                          self.ble_device.name,
                          self.ble_device.address,
                          self.controller.state)

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
        # Always keep the freshest BLEDevice and let the base coordinator
        # schedule polls. AC Infinity manufacturer data (2306) is only present
        # for devices that broadcast it; when it is, refresh state from the
        # advertisement, otherwise rely on the GATT poll for state.
        self.ble_device = service_info.device
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
    _ActiveBluetoothDataUpdateCoordinatorT: ActiveBluetoothDataUpdateCoordinator = ActiveBluetoothDataUpdateCoordinator
](
    BaseCoordinatorEntity[_ActiveBluetoothDataUpdateCoordinatorT]
):
    """A class for entities using an ActiveBluetoothDataUpdateCoordinator and whose availability should include
    whether the last Bluetooth poll was successful."""

    async def async_update(self) -> None:
        """Only allow updates via the coordinator, not on demand."""

    @property
    def available(self) -> bool:
        """Always available.

        This device is command-driven and connected on demand (and on a timer),
        not advert-driven -- its identical advertisements get deduped by HA, so
        the base coordinator's advert-based availability flaps to False and would
        block control. Reachability surfaces via command success/failure instead.
        """
        return True
