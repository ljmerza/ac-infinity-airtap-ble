# Changelog

## 2.0.0

**Breaking:** the integration domain changed from `ac_infinity` to
`ac_infinity_airtap_ble`, and `custom_components/ac_infinity/` moved to
`custom_components/ac_infinity_airtap_ble/`.

The old domain collided with [dalinicus/homeassistant-acinfinity](https://github.com/dalinicus/homeassistant-acinfinity),
which is already in the HACS default catalogue and installs to the same path.
Both integrations could not be installed side by side, and HACS cannot list two
repositories that claim one domain.

Home Assistant cannot move a config entry between domains, so upgrading from 1.x
requires deleting the existing entry, updating, restarting, and adding the
integration again. Remove any stale `config/custom_components/ac_infinity/`
directory if you installed manually.

- Add brand assets (`brand/icon.png`, `brand/icon@2x.png`).

## 1.3.0

- Rename the project to the correct spelling (**AirTap**); the repository moved to
  `ljmerza/ac-infinity-airtap-ble` (the old `ac-infinity-airtrap-ble` URL redirects).
- Add HACS + hassfest validation and Ruff lint GitHub Actions workflows.
- Declare `integration_type: device` in the manifest.

## 1.2.0

- Initial release: local Bluetooth LE control of AC Infinity AirTap booster-fan
  vents — fan on/off and 10-step speed control, plus a temperature sensor.
