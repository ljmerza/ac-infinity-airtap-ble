"""Adoption of config entries left behind by 1.x.

1.x used the domain ``ac_infinity``, which collided with
dalinicus/homeassistant-acinfinity — a cloud integration that claims the same
domain and the same ``custom_components/ac_infinity/`` install path. 2.0.0
renamed this integration to ``ac_infinity_airtap_ble``.

Home Assistant cannot move a config entry between domains, and the entry a user
created under 1.x keeps its old domain forever. Those entries stay readable in
``.storage/core.config_entries`` even once the old files are gone, so the config
flow adopts them: the orphaned entry is removed and an identical one is created
under the new domain.

The removal happens *before* the new entry is created so the old entity registry
rows are released first. The new entities then reclaim the same entity IDs
instead of gaining ``_2`` suffixes.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_SERVICE_DATA
from homeassistant.core import HomeAssistant

from .const import LEGACY_DOMAIN

_LOGGER = logging.getLogger(__name__)


def _is_ours(entry: ConfigEntry) -> bool:
    """Whether a legacy ``ac_infinity`` entry was created by this integration.

    dalinicus/homeassistant-acinfinity uses the same domain but is cloud-based,
    so its entries hold account credentials rather than a BLE address. Requiring
    both BLE keys keeps us from ever adopting one of its entries.
    """
    return CONF_ADDRESS in entry.data and CONF_SERVICE_DATA in entry.data


def async_find_legacy_entry(hass: HomeAssistant, address: str) -> ConfigEntry | None:
    """Return the orphaned 1.x entry for ``address``, if there is one."""
    target = address.upper()
    for entry in hass.config_entries.async_entries(LEGACY_DOMAIN):
        if not _is_ours(entry):
            continue
        if str(entry.data.get(CONF_ADDRESS, "")).upper() == target:
            return entry
    return None


async def async_adopt_legacy_entry(hass: HomeAssistant, entry: ConfigEntry) -> dict:
    """Remove a legacy entry and return the data to recreate it under the new domain.

    Returns the entry's ``data`` so the caller can hand it straight to
    ``async_create_entry``. Removing first frees the old entity IDs.
    """
    data = dict(entry.data)
    _LOGGER.info(
        "Adopting configuration from the legacy %s entry '%s' (%s); "
        "it will be recreated under the current domain",
        LEGACY_DOMAIN,
        entry.title,
        entry.data.get(CONF_ADDRESS),
    )
    await hass.config_entries.async_remove(entry.entry_id)
    return data
