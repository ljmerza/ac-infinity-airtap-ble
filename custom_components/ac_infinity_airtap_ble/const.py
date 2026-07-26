from bleak.exc import BleakError

DOMAIN = "ac_infinity_airtap_ble"

# Domain used up to 1.x. It collided with dalinicus/homeassistant-acinfinity,
# so 2.0.0 moved to the name above. Entries created by 1.x are still in
# .storage/core.config_entries under this domain and are adopted on discovery;
# see legacy.py.
LEGACY_DOMAIN = "ac_infinity"

MANUFACTURER = "AC Infinity"

DEVICE_TIMEOUT = 30
UPDATE_SECONDS = 15

BLEAK_EXCEPTIONS = (AttributeError, BleakError, TimeoutError)

DEVICE_MODEL = {1: "Controller 67",
                6: "Airtap Series",
                7: "Controller 69",
                11: "Controller 69 Pro"}

FAMILY_E_MODELS = {7, 9, 11, 12}
