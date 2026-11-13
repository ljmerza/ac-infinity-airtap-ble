"""AC Infinity cloud, the backup for vents Bluetooth can't control.

Type 48 vents (Airtap AI, the Wi-Fi models such as the AC-RBF4-B) don't take commands over
Bluetooth: the AC Infinity app only uses Bluetooth to get them onto Wi-Fi and controls them
through the cloud (issue #1). Endpoints and fields follow dalinicus/homeassistant-acinfinity
(MIT) and its AIRTAP pull request #154, which were checked against live vents there.
"""

from __future__ import annotations

import asyncio
import logging
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
REQUEST_TIMEOUT = 10
# The app truncates passwords to 25 characters and the API rejects longer ones.
MAX_PASSWORD_LENGTH = 25

# atType on AIRTAP fans, the same on a T4 and a T6 (PR #154): 0 is a fixed-speed fan that
# follows onSpead (0 = off), 3 is the vent's own Temp AI.
MODE_FAN = 0
MODE_COOL = 1
MODE_HEAT = 2
MODE_TEMP_AI = 3
MODE_NAMES = {MODE_FAN: "Fan", MODE_COOL: "Cool", MODE_HEAT: "Heat", MODE_TEMP_AI: "Temp AI"}
# modeAndSettingIdStr lists the ids of the fields being written: 16 = atType, 18 = onSpead.
_MODE_FIELDS = "[16]"
_MODE_AND_SPEED_FIELDS = "[16,18]"


class CloudError(Exception):
    """The cloud couldn't be reached or refused a request."""


class CloudAuthError(CloudError):
    """The email or password was rejected."""


def normalize_mac(value: str) -> str:
    return "".join(c for c in str(value).upper() if c in "0123456789ABCDEF")


def mac_candidates(ble_address: str, advert_mac: bytes | None) -> set[str]:
    """MACs the cloud might list this vent under.

    The advertisement starts with a MAC that is 2 below the Bluetooth address (ESP32 gives
    Bluetooth the Wi-Fi MAC + 2), and the cloud is expected to list the Wi-Fi one. Which one
    it lists isn't confirmed yet, so all of them are tried.
    """
    ble = normalize_mac(ble_address)
    candidates = {ble, f"{(int(ble, 16) - 2) % (1 << 48):012X}"}
    if advert_mac and len(advert_mac) >= 6:
        candidates.add(advert_mac[:6].hex().upper())
    return candidates


class AcInfinityCloud:
    """The few AC Infinity API calls an AIRTAP needs."""

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

    async def set_mode(self, dev_id: str, mode: int, speed: int | None = None) -> None:
        """Switch an AIRTAP's mode, and in Fan mode its speed (0-10).

        The minimal write the app sends; AIRTAPs refuse the full payloads controllers take.
        """
        params: dict[str, Any] = {"atType": mode, "devId": dev_id, "port": 0}
        if speed is None:
            params["modeAndSettingIdStr"] = _MODE_FIELDS
        else:
            params["modeAndSettingIdStr"] = _MODE_AND_SPEED_FIELDS
            # The API spells it onSpead; onSpeed is accepted but ignored.
            params["onSpead"] = speed
        await self._authed(
            "put", f"/api/dev/modeAndSetting?{urlencode(params)}", None, min_version=True
        )

    async def _authed(self, method: str, path: str, data, min_version: bool = False) -> dict:
        """A request that needs the login token. Logs in again once if it's refused.

        How the API reports an expired session isn't known, so any refusal gets one retry.
        """
        if self._token is None:
            await self.login()
        try:
            return await self._request(method, path, data=data and data(), min_version=min_version)
        except CloudAuthError:
            raise
        except CloudError as err:
            _LOGGER.debug("AC Infinity cloud refused %s, logging in again: %s", path, err)
            await self.login()
            return await self._request(method, path, data=data and data(), min_version=min_version)

    async def _request(
        self,
        method: str,
        path: str,
        data: dict | None = None,
        auth: bool = True,
        min_version: bool = False,
    ) -> dict:
        headers = {"User-Agent": "okhttp/4.12.0"}
        if auth:
            headers["token"] = self._token or ""
        if min_version:
            headers["minversion"] = "3.5"
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
            raise CloudError(f"{path}: {message}")
        return body


class AirtapCloudCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the account for one AIRTAP. data is its devInfoListAll entry."""

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

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            devices = await self.cloud.devices()
        except CloudError as err:
            raise UpdateFailed(str(err)) from err
        for device in devices:
            if str(device.get("devId")) == self.dev_id:
                return device
        raise UpdateFailed(f"device {self.dev_id} is no longer on the AC Infinity account")

    @property
    def info(self) -> dict[str, Any]:
        return (self.data or {}).get("deviceInfo") or {}

    async def set_mode(self, mode: int, speed: int | None = None) -> None:
        """Send a mode change, then show it right away.

        The cloud reflects a change about 20 s later, so the next poll is left to pick up
        the vent's own values instead of reading back immediately.
        """
        await self.cloud.set_mode(self.dev_id, mode, speed)
        data = dict(self.data or {})
        info = dict(data.get("deviceInfo") or {})
        info["curMode"] = mode
        if speed is not None:
            info["speak"] = speed
        data["deviceInfo"] = info
        self.async_set_updated_data(data)
