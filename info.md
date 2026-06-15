# AC Infinity AirTap (BLE)

Local **Bluetooth LE** control of AC Infinity AirTap register / booster-fan vents
(e.g. the AIRTAP T4) — no vendor cloud or phone app required.

- **Fan**: on / off and 10-step speed control.
- **Temperature** sensor.

Requires a connectable Bluetooth path in range of the vent (an ESPHome
`bluetooth_proxy` node or a local adapter on the Home Assistant host). Active
scanning is recommended.

After installing via HACS, restart Home Assistant, then add the integration from
**Settings → Devices & Services → Add Integration → AC Infinity AirTap (BLE)**.
