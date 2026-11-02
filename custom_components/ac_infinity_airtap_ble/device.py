from __future__ import annotations

import asyncio
import dataclasses
import logging
from dataclasses import dataclass
from typing import Optional

from ac_infinity_ble import ACInfinityController, DeviceInfo
from ac_infinity_ble.const import CallbackType, MANUFACTURER_ID
from ac_infinity_ble.protocol import parse_manufacturer_data
from ac_infinity_ble.util import get_short
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from homeassistant.exceptions import HomeAssistantError

from .const import BLEAK_EXCEPTIONS

WORK_TYPE_OFF = 1
WORK_TYPE_ON = 2

# Display settings live in one cmd-3 TLV write on key 0x21, value = 2 bytes:
#   byte[0] = brightness, byte[1] = backlightSwitch (1 on / 0 off).
# Confirmed from the app (ul1.setSettingData: {33, 2, brightness, backlightSwitch})
# and live captures (findings/CAPTURE_2026-06-23.md). Because both live in the same
# key, every write must carry both bytes -- so we track them together and rewrite the
# whole key whenever either changes (otherwise toggling the display would reset
# brightness, and vice-versa).
#
# The 5 UI brightness levels map to these brightness bytes (SettingControlFragment /
# SettingActivity radio handlers: rb_low/medium/height/a2/a3):
BRIGHTNESS_LEVEL_TO_BYTE = {1: 0x01, 2: 0x02, 3: 0x03, 4: 0xA2, 5: 0xA3}
BRIGHTNESS_BYTE_TO_LEVEL = {v: k for k, v in BRIGHTNESS_LEVEL_TO_BYTE.items()}
# Used until the device reports a brightness we can read (the poll doesn't return 0x21).
DEFAULT_BRIGHTNESS_BYTE = 0xA3  # level 5

_LOGGER = logging.getLogger(ACInfinityController.__module__)


@dataclass
class DeviceInfoEx(DeviceInfo):
    @staticmethod
    def create(device_info: DeviceInfo) -> DeviceInfoEx:
        return DeviceInfoEx(**device_info.__dict__)

    # Retained only so existing config entries (whose stored service_data may
    # include this key) still load; the AirTap booster fan has no auto config.
    auto_mode: Optional[dict] = None
    # Commanded display state (key 0x21). None until first set -- the device's status
    # frames aren't decoded for type 48, so these stay optimistic.
    display_on: Optional[bool] = None        # byte[1]: backlightSwitch
    display_brightness: Optional[int] = None  # byte[0]: raw brightness byte


class ACInfinityDevice(ACInfinityController):
    def __init__(
        self,
        ble_device: BLEDevice,
        state: DeviceInfoEx | None = None,
        advertisement_data: AdvertisementData | None = None,
    ):
        super().__init__(
            ble_device=ble_device,
            state=state,
            advertisement_data=advertisement_data,
        )

        if self._state is DeviceInfo:
            self._state = DeviceInfoEx(**self._state.__dict__)

        self._command_lock = asyncio.Lock()
        self._commands_in_line = 0
        self._newest_command: dict[int, object] = {}

    def set_ble_device_and_advertisement_data(
        self, ble_device: BLEDevice, advertisement_data: AdvertisementData
    ) -> None:
        self._ble_device = ble_device
        self._advertisement_data = advertisement_data
        info = parse_manufacturer_data(
            advertisement_data.manufacturer_data[MANUFACTURER_ID]
        )
        # The advertisement's fan byte is always 0 on this device, and is_on needs
        # a non-zero fan, so taking it would flip a running vent to off.
        self._state = dataclasses.replace(
            self._state,
            **{k: v for k, v in dataclasses.asdict(info).items() if v is not None and k != "fan"},
        )
        self._fire_callbacks(CallbackType.ADVERTISEMENT)

    def _notification_handler(self, _sender: int, data: bytearray) -> None:
        """Handle BLE notifications.

        Command replies still resolve their awaiting future (so update/turn
        on/off work). Unsolicited status pushes are ignored: this AirTap-class
        unit (device type 48) uses a status-frame layout the upstream library
        does not decode correctly -- it was reading work_type from a byte that
        flipped the HA entity OFF while the fan was still running. Until the
        type-48 layout is mapped from a live capture, commanded on/off/speed
        state stays authoritative rather than being clobbered by these frames.
        """
        _LOGGER.debug("%s: Notification received: %s", self.name, data.hex())
        if self._notify_future and not self._notify_future.done():
            self._notify_future.set_result(data)
            return
        # Unsolicited status push: update environment readings only (never
        # work_type/fan, which this frame does not reliably carry here).
        if self._parse_status_frame(data):
            self._fire_callbacks(CallbackType.NOTIFICATION)

    def _parse_status_frame(self, data: bytes) -> bool:
        """Parse the AirTap 0x1EFF status frame for environment readings.

        Confirmed from a BTSnoop capture (findings/CAPTURE_2026-06-07.md): the
        device reports state as `1e ff 0902 03 0c 00 00 <temp16> 0000 0000 2710
        00 a2`. Offset 8 = temperature x100 (degC); humidity/vpd fields read 0
        on the T4 (no humidity sensor). The 0x2710 constant at offset 14
        confirms alignment. Does NOT touch work_type/fan, so commanded on/off
        state stays authoritative. Returns True if a status frame was seen.
        """
        if len(data) >= 16 and data[0] == 0x1E and data[1] == 0xFF:
            self.state.tmp = get_short(data, 8) / 100
            self.state.hum = get_short(data, 10) / 100
            self.state.vpd = get_short(data, 12) / 100
            _LOGGER.debug(
                "%s: Status frame tmp=%s hum=%s", self.name, self.state.tmp,
                self.state.hum,
            )
            return True
        return False

    async def _send_setting(self, payload: list[int]) -> bool:
        """Send one cmd-3 setting write (TLV key,len,value...). Matches the app.

        See findings/CAPTURE_2026-06-07.md: cmd 3 = WRITE, key 0x10 = power/mode
        (01=off, 02=on, 03=auto), key 0x12 = fan speed (0-10).

        Commands take turns. Connecting can take a while, and without this the first
        command's disconnect cuts off anything waiting on the same connection. While
        waiting, a command is replaced by a newer one of the same kind (its first key),
        so a slider drag doesn't send every step. The connection stays open for the next
        command in line, since every extra connection is another chance to lose the vent.

        Returns False if the command was replaced and never sent.
        """
        kind = payload[0]
        ticket = object()
        self._newest_command[kind] = ticket
        self._commands_in_line += 1
        try:
            async with self._command_lock:
                if self._newest_command[kind] is not ticket:
                    _LOGGER.debug("%s: Skipping replaced command %s", self.name, payload)
                    return False
                await self._send_setting_now(payload)
                return True
        finally:
            self._commands_in_line -= 1
            if not self._commands_in_line:
                await self._execute_disconnect()
                # Otherwise the idle timer can fire during the next command's connect.
                if self._disconnect_timer:
                    self._disconnect_timer.cancel()
                    self._disconnect_timer = None

    async def _send_setting_now(self, payload: list[int]) -> None:
        """Write one setting. Call with the command lock held."""
        try:
            await self._ensure_connected()
            command = self._protocol._add_head(payload, 3, self.sequence)
            await self._send_command(command)
        except (*BLEAK_EXCEPTIONS, AssertionError) as err:
            await self._execute_disconnect()
            # The device connects per-command; if it's out of range or busy the
            # upstream lib raises a raw TimeoutError/AssertionError. Translate to a
            # HomeAssistantError so HA shows a clean failure instead of logging an
            # "Unexpected exception" traceback.
            raise HomeAssistantError(
                f"{self.name}: failed to send BLE command (device unreachable?): {err!r}"
            ) from err

    async def set_speed(self, speed: int) -> None:
        """Set fan speed 0-10 (key 0x12). Speed 0 = off."""
        if speed not in range(0, 11):
            raise ValueError("speed must be between 0 and 10")
        if speed == 0:
            await self.turn_off()
            return
        _LOGGER.debug("%s: Set speed to %s", self.name, speed)
        if not await self._send_setting([0x10, 1, 2, 0x12, 1, speed]):  # on + speed
            return
        self._state.work_type = 2
        self._state.level_on = speed
        self._state.fan = speed

    async def turn_on(self, speed: Optional[int] = None) -> None:
        """Turn on (key 0x10 = 2), optionally at a given speed (key 0x12)."""
        if speed is not None:
            await self.set_speed(speed)
            return
        _LOGGER.debug("%s: Turn on", self.name)
        if not await self._send_setting([0x10, 1, 2]):
            return
        self._state.work_type = 2
        if not self._state.fan:
            self._state.fan = self._state.level_on or 10

    async def turn_off(self) -> None:
        """Turn off (key 0x10 = 1) -- exactly what the app sends."""
        _LOGGER.debug("%s: Turn off", self.name)
        if not await self._send_setting([0x10, 1, 1]):
            return
        self._state.work_type = 1
        self._state.fan = 0

    async def _write_display(self) -> None:
        """Write key 0x21 = [brightness, backlightSwitch] from tracked state.

        Both display attributes share this one key, so we always send both bytes,
        defaulting brightness to DEFAULT_BRIGHTNESS_BYTE until it's been set.
        """
        brightness = self._state.display_brightness
        if brightness is None:
            brightness = DEFAULT_BRIGHTNESS_BYTE
        on = 1 if self._state.display_on else 0
        await self._send_setting([0x21, 2, brightness & 0xFF, on])

    async def set_display(self, enabled: bool) -> None:
        """Turn the display on/off (key 0x21 byte[1]); preserves the brightness byte."""
        _LOGGER.debug("%s: Set display %s", self.name, "on" if enabled else "off")
        self._state.display_on = enabled
        await self._write_display()

    async def set_display_brightness_level(self, level: int) -> None:
        """Set display brightness to UI level 1-5 (key 0x21 byte[0]); turns display on."""
        if level not in BRIGHTNESS_LEVEL_TO_BYTE:
            raise ValueError("brightness level must be 1-5")
        _LOGGER.debug("%s: Set display brightness level %s", self.name, level)
        self._state.display_brightness = BRIGHTNESS_LEVEL_TO_BYTE[level]
        self._state.display_on = True  # the app sends backlightSwitch=1 with brightness
        await self._write_display()

    @property
    def speed(self) -> Optional[int]:
        """Get the speed of the device."""
        return self._state.fan

    @property
    def temperature(self) -> Optional[float]:
        """Get the temperature of the device."""
        return self._state.tmp

    @property
    def humidity(self) -> Optional[float]:
        """Get the humidity of the device."""
        return self._state.hum

    @property
    def vpd(self) -> Optional[float]:
        """Get the vpd of the device."""
        return self._state.vpd

    @property
    def display_on(self) -> Optional[bool]:
        """Commanded display on/off state (None until first set)."""
        return self._state.display_on

    @property
    def display_brightness_level(self) -> Optional[int]:
        """Commanded display brightness as UI level 1-5 (None until first set)."""
        b = self._state.display_brightness
        return BRIGHTNESS_BYTE_TO_LEVEL.get(b) if b is not None else None

    @property
    def state(self) -> DeviceInfoEx:
        return self._state

    def restore_fan_state(self, is_on: bool, speed: Optional[int]) -> None:
        """Seed the commanded state after a restart, since nothing polls the vent for it."""
        self._state.work_type = WORK_TYPE_ON if is_on else WORK_TYPE_OFF
        if speed:
            self._state.level_on = speed
        self._state.fan = (speed or self._state.level_on or 10) if is_on else 0

    async def update(self) -> None:
        """Read the device once over GATT. Only the config flow uses this."""
        await self._ensure_connected()
        try:
            _LOGGER.debug("%s: Updating model data", self.name)
            command = self._protocol.get_model_data(self.state.type, 0, self.sequence)
            if data := await self._send_command(command):
                if len(data) >= 28:
                    self.state.work_type = data[12]
                    self.state.level_off = data[15]
                    self.state.level_on = data[18]
                    self._fire_callbacks(CallbackType.UPDATE_RESPONSE)
                elif self._parse_status_frame(data):
                    self._fire_callbacks(CallbackType.UPDATE_RESPONSE)
                else:
                    _LOGGER.debug(
                        "%s: Unrecognized update response (%s): %s",
                        self.name,
                        len(data),
                        data.hex(),
                    )
            # The settings read above does not carry temperature; the device
            # emits that in a periodic 0x1EFF status push. Stay connected a few
            # seconds so _notification_handler can catch one.
            await asyncio.sleep(4)
        finally:
            await self._execute_disconnect()
