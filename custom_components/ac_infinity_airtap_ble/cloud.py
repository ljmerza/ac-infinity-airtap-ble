"""AC Infinity cloud, the backup for vents Bluetooth can't control.

Type 48 vents (Airtap AI, the Wi-Fi models such as the AC-RBF4-B) don't take commands over
Bluetooth: the AC Infinity app only uses Bluetooth to get them onto Wi-Fi and controls them
through the cloud (issue #1). Login and device listing follow dalinicus/homeassistant-acinfinity
(MIT). Reads and writes follow the AC Infinity app (2.1.2) for this model: it reads the vent's
mode record and device settings, and every write sends the whole record back with the change,
naming what changed in modeAndSettingIdStr.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

CLOUD_HOST = "https://www.acinfinityserver.com"
POLL_INTERVAL = timedelta(seconds=30)
# How long a sent change is shown over the cloud's older values (it lags about 20 s).
PENDING_SECONDS = 60
REQUEST_TIMEOUT = 10
# The app truncates passwords to 25 characters and the API rejects longer ones.
MAX_PASSWORD_LENGTH = 25
AIRTAP_AI_TYPE = 48

# deviceInfo.curMode / atType.
MODE_FAN = 0
MODE_COOL = 1
MODE_HEAT = 2
MODE_TEMP_AI = 3
MODE_ADVANCED = 15
MODE_NAMES = {
    MODE_FAN: "Fan",
    MODE_COOL: "Cool",
    MODE_HEAT: "Heat",
    MODE_TEMP_AI: "Temp AI",
    MODE_ADVANCED: "Advanced",
}

# modeAndSettingIdStr ids, from the app's ControlAirTapModel and settings screens.
ID_MODE = 16  # atType
ID_SPEED = 18  # onSpead (the API's spelling; onSpeed is ignored)
ID_COOL_TRIGGER = 19  # targetTempF / targetTemp
ID_HEAT_TRIGGER = 20  # waterTempHighValueF / waterTempHighValue
ID_AI_THRESHOLD = 21  # devLtf / devLt
ID_POWER = 22  # powerState
ID_VENT_BEHAVIOR = 25  # co2Accuracy: 0 auto (closes when idle), 1 open
IDS_SETTINGS = (32, 33)  # devCompany, devLight, backlightSwitch, keytoneSwitch, devName
ID_CHILD_LOCK = 35  # devCsm1
ID_CALIBRATION = 36  # devCth (F) / devCt (C)

# The app never sends a Fahrenheit temperature below 32 (BeanUtils.resetTemperatureValues).
_FAHRENHEIT_FIELDS = (
    "devHtf", "targetTempF", "devLtf", "waterTempTargetValueF", "waterTempHighValueF",
    "waterTempLowValueF",
)
# devLtf is the Temp AI threshold (2-6), which the app's Temp AI save doesn't clamp.
_FAHRENHEIT_FIELDS_AI = tuple(f for f in _FAHRENHEIT_FIELDS if f != "devLtf")


class CloudError(Exception):
    """The cloud couldn't be reached or refused a request."""


class CloudAuthError(CloudError):
    """The email or password was rejected."""


def normalize_mac(value: str) -> str:
    return "".join(c for c in str(value).upper() if c in "0123456789ABCDEF")


def mac_candidates(ble_address: str, advert_mac: bytes | None) -> set[str]:
    """MACs the cloud might list this vent under.

    The cloud lists the MAC at the start of the advertisement, which is 2 below the Bluetooth
    address (ESP32 gives Bluetooth the Wi-Fi MAC + 2). The Bluetooth address is tried too.
    """
    ble = normalize_mac(ble_address)
    candidates = {ble, f"{(int(ble, 16) - 2) % (1 << 48):012X}"}
    if advert_mac and len(advert_mac) >= 6:
        candidates.add(advert_mac[:6].hex().upper())
    return candidates


def record_params(record: dict[str, Any], skip_clamp: str | None = None) -> dict[str, str]:
    """A record as query parameters, the way the app sends one.

    Nulls are left out (BeanUtils.transBean2Map), nested lists and objects too, and
    Fahrenheit temperatures under 32 are raised to 32.
    """
    params: dict[str, str] = {}
    for key, value in record.items():
        if value is None or isinstance(value, (list, dict)):
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        params[key] = str(value)
    for field in _FAHRENHEIT_FIELDS if skip_clamp is None else _FAHRENHEIT_FIELDS_AI:
        if field in params and params[field].lstrip("-").isdigit() and int(params[field]) < 32:
            params[field] = "32"
    return params


class AcInfinityCloud:
    """The AC Infinity API calls an AIRTAP needs."""

    def __init__(self, hass: HomeAssistant, email: str, password: str) -> None:
        self._session = async_get_clientsession(hass)
        self._email = email
        self._password = password
        self._token: str | None = None

    async def login(self) -> None:
        body = await self._request(
            "post",
            "/api/user/appUserLogin",
            data={"appEmail": self._email, "appPasswordl": self._password[:MAX_PASSWORD_LENGTH]},
            auth=False,
        )
        self._token = str(body["data"]["appId"])

    async def devices(self) -> list[dict[str, Any]]:
        """Every device on the account, as /api/user/devInfoListAll lists them."""
        body = await self._authed(
            "post", "/api/user/devInfoListAll", lambda: {"userId": self._token}
        )
        return body.get("data") or []

    async def mode_settings(self, dev_id: str) -> dict[str, Any] | None:
        """The vent's mode record (atType, onSpead, triggers, ...) with devSetting nested."""
        body = await self._authed(
            "post", "/api/dev/getdevModeSettingList", lambda: {"devId": dev_id, "port": 0},
            device_headers=True,
        )
        return body.get("data")

    async def device_settings(self, dev_id: str) -> dict[str, Any] | None:
        """The vent's device settings (unit, display, lock, calibration, ...)."""
        body = await self._authed(
            "post", "/api/dev/getDevSetting", lambda: {"devId": dev_id, "port": 0},
            device_headers=True,
        )
        return body.get("data")

    async def write(self, ids: list[int], params: dict[str, str]) -> None:
        """PUT a whole record back with the ids of the fields that changed."""
        query = {**params, "modeAndSettingIdStr": "[" + ",".join(map(str, ids)) + "]"}
        await self._authed(
            "put", f"/api/dev/modeAndSetting?{urlencode(query)}", None, device_headers=True
        )

    async def _authed(
        self, method: str, path: str, data, device_headers: bool = False
    ) -> dict:
        """A request that needs the login token. Logs in again once if it's refused.

        How the API reports an expired session isn't known, so any refusal gets one retry.
        """
        if self._token is None:
            await self.login()
        try:
            return await self._request(
                method, path, data=data and data(), device_headers=device_headers
            )
        except CloudAuthError:
            raise
        except CloudError as err:
            _LOGGER.debug("AC Infinity cloud refused %s, logging in again: %s", path, err)
            await self.login()
            return await self._request(
                method, path, data=data and data(), device_headers=device_headers
            )

    async def _request(
        self,
        method: str,
        path: str,
        data: dict | None = None,
        auth: bool = True,
        device_headers: bool = False,
    ) -> dict:
        headers = {"User-Agent": "okhttp/4.12.0"}
        if auth:
            headers["token"] = self._token or ""
        if device_headers:
            # What the app's interceptor sends for this model.
            headers["minversion"] = "3.5"
            headers["devType"] = str(AIRTAP_AI_TYPE)
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.request(
                    method, f"{CLOUD_HOST}{path}", data=data, headers=headers
                ) as response:
                    if response.status != 200:
                        raise CloudError(f"{path}: HTTP {response.status}")
                    body = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise CloudError(f"{path}: {err!r}") from err
        if not isinstance(body, dict) or body.get("code") != 200:
            message = body.get("msg") if isinstance(body, dict) else body
            if not auth:
                raise CloudAuthError(f"login refused: {message}")
            raise CloudError(f"{path.split('?')[0]}: {message}")
        return body


class AirtapCloudCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the account for one AIRTAP.

    data is its devInfoListAll entry (live state). mode and settings hold its mode record and
    device settings, which entities read their settings from.
    """

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, cloud: AcInfinityCloud, dev_id: str
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{entry.title} (AC Infinity cloud)",
            update_interval=POLL_INTERVAL,
        )
        self.cloud = cloud
        self.dev_id = str(dev_id)
        self._mode: dict[str, Any] = {}
        self._settings: dict[str, Any] = {}
        # Changes sent but maybe not in the cloud's records yet: field -> (value, until).
        # The cloud takes about 20 s to reflect a write, so reads in between are stale.
        self._pending: dict[str, tuple[Any, float]] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            devices = await self.cloud.devices()
        except CloudError as err:
            raise UpdateFailed(str(err)) from err
        device = next((d for d in devices if str(d.get("devId")) == self.dev_id), None)
        if device is None:
            raise UpdateFailed(f"device {self.dev_id} is no longer on the AC Infinity account")
        # Settings are secondary: keep the last ones if a read fails.
        for attr, read in (("_mode", self.cloud.mode_settings), ("_settings", self.cloud.device_settings)):
            try:
                if (value := await read(self.dev_id)) is not None:
                    setattr(self, attr, value)
            except CloudError as err:
                _LOGGER.debug("%s: reading %s failed: %s", self.name, attr.strip("_"), err)
        return device

    def _pending_values(self) -> dict[str, Any]:
        now = time.monotonic()
        self._pending = {k: v for k, v in self._pending.items() if v[1] > now}
        return {k: v[0] for k, v in self._pending.items()}

    def _remember(self, changes: dict[str, Any]) -> None:
        until = time.monotonic() + PENDING_SECONDS
        self._pending.update({k: (v, until) for k, v in changes.items()})

    @property
    def info(self) -> dict[str, Any]:
        """deviceInfo, with changes the cloud hasn't caught up with yet."""
        info = dict((self.data or {}).get("deviceInfo") or {})
        pending = self._pending_values()
        if "atType" in pending:
            info["curMode"] = pending["atType"]
        if "powerState" in pending:
            info["powerState"] = pending["powerState"]
        return info

    @property
    def mode(self) -> dict[str, Any]:
        """The mode record, devSetting merged over it as the app does, plus pending changes."""
        merged = {k: v for k, v in self._mode.items() if k not in ("devSetting", "devAdvGroups")}
        merged.update({k: v for k, v in (self._mode.get("devSetting") or {}).items() if v is not None})
        merged.update(self._pending_values())
        return merged

    @property
    def settings(self) -> dict[str, Any]:
        """The device settings, plus pending changes."""
        return {**self._settings, **self._pending_values()}

    def mode_value(self, key: str) -> Any:
        return self.mode.get(key)

    async def set_control(self, field_id: int, **changes: Any) -> None:
        """Change a control (power, mode, speed, triggers, vent behaviour), as the app does.

        Reads the mode record fresh, merges devSetting over it, applies the change and sends
        it all back. The cloud reflects the change about 20 s later, so it's shown right away
        and the next poll picks up the vent's own values.
        """
        try:
            if (record := await self.cloud.mode_settings(self.dev_id)) is not None:
                self._mode = record
        except CloudError as err:
            _LOGGER.debug("%s: reading the mode before a write failed: %s", self.name, err)
        merged = {**self.mode, **changes}
        merged.setdefault("devId", self.dev_id)
        merged["port"] = 0
        await self.cloud.write(
            [field_id],
            record_params(merged, skip_clamp="devLtf" if field_id == ID_AI_THRESHOLD else None),
        )
        self._remember(changes)
        self.async_set_updated_data(dict(self.data or {}))

    async def set_setting(self, field_ids: list[int], **changes: Any) -> None:
        """Change a device setting (unit, display, sound, lock, calibration), as the app does.

        Reads the settings fresh and sends them all back with the change.
        """
        try:
            if (record := await self.cloud.device_settings(self.dev_id)) is not None:
                self._settings = record
        except CloudError as err:
            _LOGGER.debug("%s: reading settings before a write failed: %s", self.name, err)
        merged = {**self.settings, **changes}
        merged.setdefault("devId", self.dev_id)
        merged["port"] = 0
        await self.cloud.write(field_ids, record_params(merged))
        self._remember(changes)
        self.async_set_updated_data(dict(self.data or {}))
