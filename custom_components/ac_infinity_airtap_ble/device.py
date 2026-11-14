from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
from collections.abc import AsyncIterator
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
WORK_TYPE_AUTO = 3

# App value ranges (AddSubView.setModeType, and the vent's own Auto defaults).
OFF_SPEED_RANGE = (0, 10)
CALIBRATION_RANGE_F = (-20, 20)
CALIBRATION_RANGE_C = (-10, 10)
TRANSITION_RANGE_F = (0, 20)
TRANSITION_RANGE_C = (0, 10)
AUTO_TRIGGER_RANGE_F = (32, 194)
AUTO_TRIGGER_RANGE_C = (0, 90)

# Auto trigger enable bits in key 0x13 byte[0] (a.getTmpHumSwitch).
AUTO_HIGH_TMP_BIT = 0x08
AUTO_LOW_TMP_BIT = 0x04

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

# Keys read while connected. 0x02 = sensor, 0x03 = fan, the rest are settings. The vent
# answers an empty 0x23 (buffer) and doesn't report humidity, so neither is used.
READ_BACK_KEYS = (
    [0x10, 0x02, 0x03],
    [0x20, 0x21, 0x22, 0x24, 0x11, 0x12, 0x13],
)

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
    # Vent settings. Temperatures are kept in both units, as the vent stores them.
    current_speed: Optional[int] = None  # what the vent reports it's running at
    unit_celsius: Optional[bool] = None  # key 0x20: 1 = C, 0 = F
    calibration_f: Optional[int] = None  # key 0x24
    calibration_c: Optional[int] = None
    calibration_hum: int = 0
    transition_f: Optional[int] = None  # key 0x22
    transition_c: Optional[int] = None
    transition_hum: int = 0
    auto_high_on: Optional[bool] = None  # key 0x13
    auto_low_on: Optional[bool] = None
    auto_high_f: Optional[int] = None
    auto_high_c: Optional[int] = None
    auto_low_f: Optional[int] = None
    auto_low_c: Optional[int] = None
    auto_high_hum: int = 0
    auto_low_hum: int = 0


def _signed(value: int) -> int:
    """Read a byte as signed; calibration offsets can be negative."""
    return value - 256 if value > 127 else value


def f_delta_to_c(value: int) -> int:
    return round(value * 5 / 9)


def c_delta_to_f(value: int) -> int:
    return round(value * 9 / 5)


def f_to_c(value: int) -> int:
    return round((value - 32) * 5 / 9)


def c_to_f(value: int) -> int:
    return round(value * 9 / 5 + 32)


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
        self,
        ble_device: BLEDevice,
        advertisement_data: AdvertisementData,
        use_speed: bool = True,
    ) -> None:
        self._ble_device = ble_device
        self._advertisement_data = advertisement_data
        info = parse_manufacturer_data(
            advertisement_data.manufacturer_data[MANUFACTURER_ID]
        )
        # The speed is handled below: a 0 here must not flip a running vent to off.
        self._state = dataclasses.replace(
            self._state,
            **{k: v for k, v in dataclasses.asdict(info).items() if v is not None and k != "fan"},
        )
        if not use_speed:
            self._fire_callbacks(CallbackType.ADVERTISEMENT)
            return
        self._state.current_speed = info.fan
        if info.fan and self._state.work_type in (WORK_TYPE_ON, WORK_TYPE_AUTO):
            self._state.fan = info.fan
            if self._state.work_type == WORK_TYPE_ON:
                self._state.level_on = info.fan
        self._fire_callbacks(CallbackType.ADVERTISEMENT)

    def _notification_handler(self, sender, data: bytearray) -> None:
        """Handle BLE notifications.

        Command replies still resolve their awaiting future (so update/turn
        on/off work). Unsolicited status pushes are ignored: this AirTap-class
        unit (device type 48) uses a status-frame layout the upstream library
        does not decode correctly -- it was reading work_type from a byte that
        flipped the HA entity OFF while the fan was still running. Until the
        type-48 layout is mapped from a live capture, commanded on/off/speed
        state stays authoritative rather than being clobbered by these frames.
        """
        handle = getattr(sender, "handle", sender)
        _LOGGER.debug("%s: Notification received on handle %s: %s", self.name, handle, data.hex())
        # Status pushes arrive about once a second, so only a command reply (A5) may
        # resolve the future, or a read could get a status frame back instead.
        if data[:1] == b"\xa5" and self._notify_future and not self._notify_future.done():
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

    @contextlib.asynccontextmanager
    async def _command_turn(self) -> AsyncIterator[None]:
        """Take this command's turn on the vent's one connection.

        Connecting can take a while, and without turns the first command's disconnect
        cuts off anything waiting on the same connection. The connection stays open
        for the next command in line, since every extra connection is another chance
        to lose the vent.
        """
        self._commands_in_line += 1
        try:
            async with self._command_lock:
                yield
        finally:
            self._commands_in_line -= 1
            if not self._commands_in_line:
                await self._execute_disconnect()
                # Otherwise the idle timer can fire during the next command's connect.
                if self._disconnect_timer:
                    self._disconnect_timer.cancel()
                    self._disconnect_timer = None

    async def _send_setting(self, payload: list[int]) -> bool:
        """Send one cmd-3 setting write (TLV key,len,value...). Matches the app.

        See findings/CAPTURE_2026-06-07.md: cmd 3 = WRITE, key 0x10 = power/mode
        (01=off, 02=on, 03=auto), key 0x12 = fan speed (0-10).

        While waiting for its turn, a command is replaced by a newer one of the same
        kind (its first key), so a slider drag doesn't send every step.

        Returns False if the command was replaced and never sent.
        """
        kind = payload[0]
        ticket = object()
        self._newest_command[kind] = ticket
        async with self._command_turn():
            if self._newest_command[kind] is not ticket:
                _LOGGER.debug("%s: Skipping replaced command %s", self.name, payload)
                return False
            await self._send_setting_now(payload)
            if self._commands_in_line == 1:
                await self._read_back()
            return True

    async def refresh_settings(self) -> None:
        """Connect only to read the vent's current state and settings."""
        async with self._command_turn():
            try:
                await self._ensure_connected()
            except (*BLEAK_EXCEPTIONS, AssertionError) as err:
                raise HomeAssistantError(
                    f"{self.name}: failed to connect to read settings: {err!r}"
                ) from err
            if not await self._read_back():
                raise HomeAssistantError(f"{self.name}: failed to read settings")

    async def _read_keys(self, keys: list[int]) -> dict[int, bytes]:
        """Read setting keys (cmd 1) over the open connection. Replies are TLV."""
        command = self._protocol._add_head(keys, 1, self.sequence)
        data = await self._send_command(command)
        values: dict[int, bytes] = {}
        if not data:
            return values
        payload = bytes(data[10:-2])
        i = 0
        while i + 1 < len(payload):
            length = payload[i + 1]
            values[payload[i]] = payload[i + 2 : i + 2 + length]
            i += 2 + length
        return values

    async def _read_back(self) -> bool:
        """Read the vent's state and settings while already connected.

        A failed read must not fail the command that was just sent, so this returns
        False instead of raising.
        """
        try:
            for keys in READ_BACK_KEYS:
                values = await self._read_keys(keys)
                _LOGGER.debug(
                    "%s: Read back %s: %s",
                    self.name,
                    bytes(keys).hex(),
                    {hex(k): v.hex() for k, v in values.items()},
                )
                self._apply_read_back(values)
            if len(v := values.get(0x13, b"")) >= 2 and not v[1]:
                values = await self._read_keys([0x13])
                _LOGGER.debug("%s: Read back 13 again: %s", self.name, values)
                self._apply_read_back(values)
        except (*BLEAK_EXCEPTIONS, AssertionError) as err:
            _LOGGER.debug("%s: Read back failed: %r", self.name, err)
            return False
        self._fire_callbacks(CallbackType.UPDATE_RESPONSE)
        return True

    def _apply_read_back(self, values: dict[int, bytes]) -> None:
        """Decode read-back keys into the state (hp5.parseSetting, parseTFP)."""
        state = self._state
        if len(v := values.get(0x10, b"")) >= 1:
            state.work_type = v[0]
        if len(v := values.get(0x02, b"")) >= 5 and (tmp := get_short(v, 1)):
            state.tmp = tmp / 100
        if len(v := values.get(0x03, b"")) >= 2:
            state.current_speed = v[1] & 0x0F
        if len(v := values.get(0x11, b"")) >= 1:
            state.level_off = v[0]
        if len(v := values.get(0x12, b"")) >= 1:
            state.level_on = v[0]
        if state.work_type == WORK_TYPE_OFF:
            state.fan = 0
        elif state.work_type == WORK_TYPE_ON:
            state.fan = state.level_on or state.current_speed
        elif state.work_type == WORK_TYPE_AUTO:
            state.fan = state.current_speed
        if len(v := values.get(0x20, b"")) >= 1:
            state.unit_celsius = bool(v[0] & 1)
        if len(v := values.get(0x21, b"")) >= 2:
            state.display_brightness = v[0]
            state.display_on = bool(v[1])
        if len(v := values.get(0x22, b"")) >= 3:
            state.transition_f, state.transition_c, state.transition_hum = v[0], v[1], v[2]
        if len(v := values.get(0x24, b"")) >= 3:
            state.calibration_f = _signed(v[0])
            state.calibration_c = _signed(v[1])
            state.calibration_hum = v[2]
        # The first read after a power cycle has a high trigger of 0 until the vent has
        # loaded its stored triggers, so don't take those as the real values.
        if len(v := values.get(0x13, b"")) >= 7 and v[1]:
            state.auto_high_on = bool(v[0] & AUTO_HIGH_TMP_BIT)
            state.auto_low_on = bool(v[0] & AUTO_LOW_TMP_BIT)
            state.auto_high_f, state.auto_high_c = v[1], v[2]
            state.auto_low_f, state.auto_low_c = v[3], v[4]
            state.auto_high_hum, state.auto_low_hum = v[5], v[6]

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

    async def set_auto_mode(self) -> None:
        """Hand control to the vent's own Auto mode (key 0x10 = 3)."""
        _LOGGER.debug("%s: Set Auto mode", self.name)
        if not await self._send_setting([0x10, 1, WORK_TYPE_AUTO]):
            return
        self._state.work_type = WORK_TYPE_AUTO

    async def set_off_speed(self, speed: int) -> None:
        """Set the speed the vent runs at in off mode (key 0x11)."""
        if speed not in range(OFF_SPEED_RANGE[0], OFF_SPEED_RANGE[1] + 1):
            raise ValueError("off speed must be between 0 and 10")
        _LOGGER.debug("%s: Set off speed %s", self.name, speed)
        self._state.level_off = speed
        await self._send_setting([0x11, 1, speed])

    async def set_temperature_unit(self, celsius: bool) -> None:
        """Set the unit the vent displays (key 0x20)."""
        _LOGGER.debug("%s: Set unit %s", self.name, "C" if celsius else "F")
        self._state.unit_celsius = celsius
        await self._send_setting([0x20, 1, 1 if celsius else 0])

    def _pair(self, value: int, to_c, to_f) -> tuple[int, int]:
        """Return (F, C) for a value given in the vent's current unit."""
        if self._state.unit_celsius:
            return to_f(value), value
        return value, to_c(value)

    async def set_calibration(self, value: int) -> None:
        """Set the temperature offset, in the vent's current unit (key 0x24)."""
        _LOGGER.debug("%s: Set calibration %s", self.name, value)
        f, c = self._pair(value, f_delta_to_c, c_delta_to_f)
        self._state.calibration_f, self._state.calibration_c = f, c
        await self._send_setting([0x24, 3, f & 0xFF, c & 0xFF, self._state.calibration_hum])

    async def set_transition(self, value: int) -> None:
        """Set the Auto ramp step, in the vent's current unit (key 0x22)."""
        _LOGGER.debug("%s: Set transition %s", self.name, value)
        f, c = self._pair(value, f_delta_to_c, c_delta_to_f)
        self._state.transition_f, self._state.transition_c = f, c
        await self._send_setting([0x22, 3, f, c, self._state.transition_hum])

    async def _write_auto(self) -> None:
        """Write key 0x13 from tracked state. Every write carries the whole key.

        Writing this key switches the vent to Auto, so the same write puts the current
        mode back. Going to Auto stays a separate choice.
        """
        s = self._state
        switches = (AUTO_HIGH_TMP_BIT if s.auto_high_on else 0) | (
            AUTO_LOW_TMP_BIT if s.auto_low_on else 0
        )
        await self._send_setting(
            [
                0x13,
                7,
                switches,
                s.auto_high_f or AUTO_TRIGGER_RANGE_F[1],
                s.auto_high_c or AUTO_TRIGGER_RANGE_C[1],
                s.auto_low_f or AUTO_TRIGGER_RANGE_F[0],
                s.auto_low_c or AUTO_TRIGGER_RANGE_C[0],
                s.auto_high_hum,
                s.auto_low_hum,
                0x10,
                1,
                s.work_type or WORK_TYPE_OFF,
            ]
        )

    async def _ensure_auto_loaded(self) -> None:
        """Read the vent first, so a change to one trigger can't erase the stored other."""
        if self._state.auto_high_f is None:
            await self.refresh_settings()

    async def set_auto_high(self, value: int) -> None:
        """Set the Auto high trigger, in the vent's current unit."""
        _LOGGER.debug("%s: Set Auto high %s", self.name, value)
        await self._ensure_auto_loaded()
        self._state.auto_high_f, self._state.auto_high_c = self._pair(value, f_to_c, c_to_f)
        await self._write_auto()

    async def set_auto_low(self, value: int) -> None:
        """Set the Auto low trigger, in the vent's current unit."""
        _LOGGER.debug("%s: Set Auto low %s", self.name, value)
        await self._ensure_auto_loaded()
        self._state.auto_low_f, self._state.auto_low_c = self._pair(value, f_to_c, c_to_f)
        await self._write_auto()

    async def set_auto_high_enabled(self, enabled: bool) -> None:
        _LOGGER.debug("%s: Set Auto high enabled %s", self.name, enabled)
        await self._ensure_auto_loaded()
        self._state.auto_high_on = enabled
        await self._write_auto()

    async def set_auto_low_enabled(self, enabled: bool) -> None:
        _LOGGER.debug("%s: Set Auto low enabled %s", self.name, enabled)
        await self._ensure_auto_loaded()
        self._state.auto_low_on = enabled
        await self._write_auto()

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
    def is_on(self) -> bool:
        """On when commanded on at a speed, or handed over to Auto."""
        if self._state.work_type == WORK_TYPE_AUTO:
            return True
        return bool(self._state.work_type == WORK_TYPE_ON and self._state.fan)

    @property
    def is_auto(self) -> bool:
        return self._state.work_type == WORK_TYPE_AUTO

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

    def restore_fan_state(
        self, is_on: bool, speed: Optional[int], auto: bool = False
    ) -> None:
        """Seed the commanded state after a restart, since nothing polls the vent for it."""
        if auto:
            self._state.work_type = WORK_TYPE_AUTO
            self._state.fan = speed or 0
            return
        self._state.work_type = WORK_TYPE_ON if is_on else WORK_TYPE_OFF
        if speed:
            self._state.level_on = speed
        self._state.fan = (speed or self._state.level_on or 10) if is_on else 0

    def _resolve_characteristics(self, services) -> bool:
        """Find the control characteristics, and log the vent's GATT table.

        Newer vents (device type 48, e.g. the AC-RBF4-B) fail setup, and their layout
        isn't known yet (issue #1). When the known characteristics are missing this
        logs a warning, so the table shows up without debug logging.
        """
        resolved = super()._resolve_characteristics(services)
        table = "; ".join(
            f"{service.uuid}: "
            + ", ".join(
                f"{char.uuid} (handle {char.handle}, {'/'.join(char.properties)})"
                for char in service.characteristics
            )
            for service in services
        )
        (_LOGGER.debug if resolved else _LOGGER.warning)(
            "%s [%s] (type %s, version %s): GATT services: %s",
            self.name,
            self.address,
            self._state.type,
            self._state.version,
            table,
        )
        return resolved

    def _log_setup_failure(self, stage: str, err: Exception) -> None:
        _LOGGER.warning(
            "%s [%s] (type %s, version %s): setup failed at %s: %r",
            self.name,
            self.address,
            self._state.type,
            self._state.version,
            stage,
            err,
        )

    async def update(self) -> None:
        """Read the device once over GATT. Only the config flow uses this."""
        try:
            await self._ensure_connected()
        except Exception as err:
            self._log_setup_failure("connect", err)
            raise
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
        except Exception as err:
            # A TimeoutError here means the write went out but no reply came back.
            self._log_setup_failure("model query", err)
            raise
        finally:
            await self._execute_disconnect()
