# Changelog

## 2.2.0

- Add Auto mode: an Auto preset on the fan, high and low temperature triggers with
  enable switches, and an Auto Transition setting. The vent runs Auto itself.
- Add Off Speed, Temperature Calibration and Temperature Unit settings.
- Add a Current Speed sensor and a Refresh Settings button.
- Read the vent's mode, speed and settings while already connected for a command, so
  the display controls and new settings show real values instead of unknown. Settings
  changed on the vent or in the app appear after the next command or refresh.
- Use the advertised speed for the fan. A proxy can replay an old copy of the
  advertisement for minutes, so repeats and a proxy's first copy aren't trusted, which
  also keeps old temperatures out.
- Restore display and settings values after a Home Assistant restart.

## 2.1.0

- Stop polling the vent. It was connecting about 70 times an hour, and a vent can
  stop advertising for good after a failed connection, until it is power cycled.
  Temperature now comes from the advertisement, and the integration only connects
  to send a command.
- The advertised temperature can trail the value read over a connection (up to
  about 1.5 degrees C seen). Changes made on the vent itself or in the app no
  longer show up in Home Assistant.
- Fix a running vent flipping to off. An advertisement with a fan byte of 0 was
  overwriting the commanded speed, so this release ignores that byte. This entry
  first said the byte is always 0, which was wrong: it appears to report the real
  speed while the vent runs.
- Restore the fan's on/off state and speed after a Home Assistant restart.
- The display switch and brightness show as unknown until you set them, because
  nothing reads them from the vent anymore.
- Fix commands being dropped with a "device unreachable?" error when a second one
  arrived while the first was still connecting. Commands now take turns on one
  connection, and while waiting, a newer command replaces an older one of the same
  kind.

## 2.0.2

- Pass `sw_version` to the device registry as a string. The upstream
  `ac_infinity_ble.DeviceInfo.version` field is an `int`, and handing that
  straight to `DeviceInfo` made Home Assistant log a deprecation warning on
  every startup, once per platform. Non-string values stop being accepted in
  Home Assistant 2026.12.0.

## 2.0.1

Upgrading from 1.x no longer requires deleting and re-adding the integration.

2.0.0 renamed the domain but left users to migrate by hand, because Home
Assistant cannot move a config entry between domains. Entries created by 1.x do
survive in `.storage/core.config_entries` under the old `ac_infinity` domain,
so the config flow now adopts them: the orphaned entry is removed and recreated
under the current domain with the same data, title and unique ID.

The old entry is removed *before* the new one is created, which releases its
entity registry rows, so entity IDs are preserved rather than gaining `_2`
suffixes. Adoption happens on Bluetooth discovery and on manual MAC entry, so
vents that do not advertise reliably are covered too.

Only entries carrying a BLE address are adopted, so a co-installed
[dalinicus/homeassistant-acinfinity](https://github.com/dalinicus/homeassistant-acinfinity)
(cloud, same old domain) is never touched.

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
