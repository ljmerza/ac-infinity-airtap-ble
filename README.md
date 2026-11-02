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

- **Fan**: on / off, 10-step speed control and an **Auto** preset (`fan.*`).
- **Temperature** and **Current Speed** sensors (`sensor.*`).
- **Auto mode**: high and low temperature triggers, each with an enable switch, plus
  the Auto Transition step. The vent runs these itself, so Auto keeps working when
  Home Assistant or Bluetooth is down.
- **Off Speed**, **Temperature Calibration** and **Temperature Unit** settings.
- **Display** switch and **Display Brightness** select.
- **Refresh Settings** button.

The AirTap T4 reports no humidity, so no humidity entity is created.

### How Auto mode runs

| Auto trigger state | Speed the vent reported |
|---|---|
| Triggered | On speed (the fan's speed setting) |
| Not triggered | Off Speed |

Changing a trigger keeps the vent's current mode. Choosing the Auto preset is what
starts Auto. Pick a speed or turn the fan off to leave it.

Plain off mode reported speed 0 with an Off Speed of 10, so Off Speed only seems to
apply in Auto. A vent with a high Off Speed can run fast in Auto when it isn't triggered.

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
notify `…1002`). Commands are `cmd 3` writes: key `0x10` = mode (`01` off / `02` on /
`03` auto), key `0x12` = fan speed (`0`–`10`), key `0x11` = off speed, key `0x13` = Auto
triggers, key `0x20` = unit, key `0x22` = Auto transition, key `0x24` = calibration.

The integration only connects to carry a command or a Refresh Settings press. It does
not poll, because the vent can stop advertising until it is power cycled after a failed
connection. While connected for a command, it also reads the vent's mode, speed and
settings (`cmd 1`), so those stay correct without extra connections. A setting changed
on the vent itself or in the app shows up after the next command or refresh. Between
those, temperature comes from the advertisement's manufacturer data, so it can trail
the real value slightly.

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
