# AC Infinity AirTap (BLE)

A Home Assistant custom integration for **local Bluetooth LE control** of
[AC Infinity AIRTAP](https://acinfinity.com/register-booster-fans/) register /
booster-fan vents (e.g. the AIRTAP T4) — no vendor cloud or phone app required.

This is a trimmed, BLE-only build focused on the AirTap: **fan control +
temperature**, nothing else.

> ### Upgrading from 1.x — handled automatically
>
> The integration domain changed from `ac_infinity` to `ac_infinity_airtap_ble`
> in 2.0.0. The old domain collided with
> [dalinicus/homeassistant-acinfinity](https://github.com/dalinicus/homeassistant-acinfinity),
> which also installs to `custom_components/ac_infinity/` — the two could not
> coexist, and only one could be listed in HACS.
>
> Update and restart. When your vent next advertises, its old configuration is
> adopted automatically: the stale entry is removed and recreated under the new
> domain, keeping your entity IDs. Nothing to delete, nothing to re-add.
>
> If a vent never advertises — the AirTap is unreliable about this, see
> [Requirements](#requirements) — add it once via **Add Integration** and enter
> its MAC address. That path adopts the old configuration too.
>
> Installed manually rather than through HACS? Delete the stale
> `config/custom_components/ac_infinity/` directory, or Home Assistant will keep
> loading 1.x alongside 2.x.
>
> Only *device*-based automations need attention: the device is recreated, so it
> gets a new internal device ID. Entity-based automations are unaffected.

## Features

- **Fan**: on / off and 10-step speed control (`fan.*`).
- **Temperature** sensor (`sensor.*`).

That's intentionally it — the auto-mode/threshold configuration entities of the
upstream project have been removed. The AirTap T4 has no humidity sensor, so no
humidity entity is created.

## Requirements

- A **connectable** Bluetooth path in range of the vent — typically an
  [ESPHome `bluetooth_proxy`](https://esphome.io/components/bluetooth_proxy.html)
  node, or a local adapter on the HA host.
- **Active scanning** is recommended on that proxy
  (`esp32_ble_tracker: scan_parameters: active: true`). The AirTap does not
  reliably broadcast its manufacturer data, so passive-only scanners may never
  surface it for auto-discovery.

## Install

**HACS (custom repository):**

1. HACS → ⋯ → *Custom repositories* → add
   `https://github.com/ljmerza/ac-infinity-airtap-ble` as an **Integration**.
2. Install **AC Infinity AirTap (BLE)**, then restart Home Assistant.

**Manual:** copy `custom_components/ac_infinity_airtap_ble/` into your HA `config/custom_components/`.

## Setup

Settings → **Devices & Services** → **Add Integration** → *AC Infinity AirTap (BLE)*.

- If the vent is advertising AC Infinity manufacturer data (company id `0x0902`),
  it is offered for auto-discovery / appears in the device list.
- If it isn't (common on the AirTap), the flow falls back to a **manual entry**
  where you paste the vent's **BLE MAC address**. The integration then connects
  to it directly through your proxy.

## How it talks to the vent

Control uses the AC Infinity GATT protocol (service `70D51000-…`, write `…1001`,
notify `…1002`). Commands are `cmd 3` writes: key `0x10` = power (`01` off /
`02` on), key `0x12` = fan speed (`0`–`10`). Temperature is read from the
device's periodic `0x1EFF` status frame. State is refreshed on a short polling
timer because the vent's advertisements are static and get deduplicated by HA's
Bluetooth manager.

## Troubleshooting

Enable debug logging:

```yaml
logger:
  default: info
  logs:
    ac_infinity_ble: debug
    custom_components.ac_infinity_airtap_ble: debug
```

## Credits

This builds directly on the work of others:

- [**mtsphere/ac-infinity-airtap-hacs**](https://github.com/mtsphere/ac-infinity-airtap-hacs)
  — the AirTap-focused fork this project is based on.
- [**hunterjm/ac-infinity-hacs**](https://github.com/hunterjm/ac-infinity-hacs)
  and the [**ac-infinity-ble**](https://github.com/hunterjm/ac-infinity-ble)
  library by Jason Hunter — the original integration and BLE codec.

Licensed under the MIT License (see [`LICENSE`](LICENSE)); original copyright
retained.
