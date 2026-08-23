# AC Infinity AirTap (BLE)

<p align="center">
<img src="https://img.shields.io/github/stars/ljmerza/ac-infinity-airtap-ble?style=for-the-badge&label=Stars&color=orange" alt="Stars">
<a href="https://github.com/ljmerza/ac-infinity-airtap-ble/releases/latest"><img src="https://img.shields.io/github/v/release/ljmerza/ac-infinity-airtap-ble?style=for-the-badge&color=purple" alt="Version"></a>
<a href="https://github.com/ljmerza/ac-infinity-airtap-ble/actions/workflows/lint.yml"><img src="https://img.shields.io/github/actions/workflow/status/ljmerza/ac-infinity-airtap-ble/lint.yml?style=for-the-badge&label=Lint" alt="Lint"></a>
<a href="https://github.com/ljmerza/ac-infinity-airtap-ble/actions/workflows/validate.yml"><img src="https://img.shields.io/github/actions/workflow/status/ljmerza/ac-infinity-airtap-ble/validate.yml?style=for-the-badge&label=Validate" alt="Validate"></a>
<a href="https://github.com/ljmerza/ac-infinity-airtap-ble/blob/main/LICENSE"><img src="https://img.shields.io/github/license/ljmerza/ac-infinity-airtap-ble?style=for-the-badge&label=License&color=green" alt="License"></a>
</p>

<p align="center">
<a href="https://www.buymeacoffee.com/JMISm06AD"><img src="https://img.shields.io/badge/Buy%20Me%20A%20Coffee-FFDD00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me A Coffee"></a>
</p>

A Home Assistant custom integration for **local Bluetooth LE control** of
[AC Infinity AIRTAP](https://acinfinity.com/register-booster-fans/) register /
booster-fan vents (e.g. the AIRTAP T4) — no vendor cloud or phone app required.

This is a trimmed, BLE-only build focused on the AirTap: **fan control +
temperature**, nothing else.

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
