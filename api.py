#!/usr/bin/env python3
"""
api.py – MMM-weconnectid_MQTT
Reads VW vehicle data from a local MQTT broker and outputs a JSON-compatible
dict that MMM-weconnectid_MQTT.js can consume.

Arguments:
  argv[1]  MQTT topic prefix, e.g. "vwdata/chris"
  argv[2]  Picture folder path (not used here, kept for future use)

The MQTT broker must be running on localhost:1883.
Data is published there by vw_mqtt.py (see repository README).
"""

import time
import sys
from datetime import datetime, timezone

try:
    import paho.mqtt.client as mqtt
except ModuleNotFoundError:
    print({"status": 0, "error": "paho-mqtt not installed. Run: pip3 install paho-mqtt"})
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MQTT_HOST  = "localhost"
MQTT_PORT  = 1883
MQTT_TOPIC = sys.argv[1] if len(sys.argv) > 1 else "vwdata/mycar"
TIMEOUT    = 5   # seconds to wait for retained MQTT messages

# ---------------------------------------------------------------------------
# Collect retained MQTT messages
# ---------------------------------------------------------------------------
received: dict = {}

def on_message(client, userdata, msg):
    key = msg.topic.replace(f"{MQTT_TOPIC}/", "", 1)
    received[key] = msg.payload.decode("utf-8")

def on_connect(client, userdata, flags, rc, properties=None):
    client.subscribe(f"{MQTT_TOPIC}/#")

mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqttc.on_connect = on_connect
mqttc.on_message = on_message

try:
    mqttc.connect(MQTT_HOST, MQTT_PORT)
except Exception as e:
    print({"status": 0, "error": f"Cannot connect to MQTT broker: {e}"})
    sys.exit(1)

mqttc.loop_start()
time.sleep(TIMEOUT)
mqttc.loop_stop()

if not received:
    print({"status": 0, "error": "No MQTT data received. Is vw_mqtt.py running?"})
    sys.exit(1)

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def get(key, default=0):
    return received.get(key, default)

# ---------------------------------------------------------------------------
# Auto-detect vehicle type
# Hybrid vehicles provide "fuel_level"; pure EVs provide "soc_hv".
# ---------------------------------------------------------------------------
is_hybrid = "fuel_level" in received

# ---------------------------------------------------------------------------
# Charging state – normalise to the values MMM-weconnectid.js expects
# ---------------------------------------------------------------------------
charge_state_raw = str(get("charging_state", "UNKNOWN"))
CHARGE_STATE_MAP = {
    # Pure EV (ID.3, ID.4, ID.7 …)
    "CHARGE_STATE_CHARGING":                "charging",
    "CHARGE_STATE_NOT_READY_FOR_CHARGING":  "readyForCharging",
    "CHARGE_STATE_READY_FOR_CHARGING":      "readyForCharging",
    "CHARGE_STATE_CONSERVATION":            "conservation",
    "CHARGE_STATE_OFF":                     "off",
    "CHARGE_STATE_ERROR":                   "error",
    "CHARGE_STATE_DISCHARGING":             "discharging",
    # Hybrid (Multivan, Golf GTE …)
    "chargingHvBattery":                    "charging",
    "notReadyForCharging":                  "readyForCharging",
    "readyForCharging":                     "readyForCharging",
    "conservation":                         "conservation",
    "off":                                  "off",
    "error":                                "error",
    "invalid":                              "off",
}
charge_state = CHARGE_STATE_MAP.get(charge_state_raw, charge_state_raw)

# ---------------------------------------------------------------------------
# Odometer
# ---------------------------------------------------------------------------
try:
    odometer_int = int(float(get("odometer", 0)))
except (ValueError, TypeError):
    odometer_int = 0
odometer_fmt = f"{odometer_int:,}".replace(",", ".")

# ---------------------------------------------------------------------------
# State of Charge
# ---------------------------------------------------------------------------
try:
    soc = int(float(get("soc", 0)))
except (ValueError, TypeError):
    soc = 0

# ---------------------------------------------------------------------------
# Range
# Pure EV:  range_km  (may be absent – VW EU Data Act does not always provide it)
# Hybrid:   range_electric / range_fuel / range_combined
# ---------------------------------------------------------------------------
try:
    if is_hybrid:
        remaining_km   = int(float(get("range_combined",  0)))
        electric_range = int(float(get("range_electric",  0)))
        gasoline_range = int(float(get("range_fuel",      0)))
    else:
        remaining_km   = int(float(get("range_km", 0)))
        electric_range = remaining_km
        gasoline_range = 0
except (ValueError, TypeError):
    remaining_km = electric_range = gasoline_range = 0

# ---------------------------------------------------------------------------
# Fuel level (hybrid only)
# ---------------------------------------------------------------------------
try:
    fuel_level = int(float(get("fuel_level", 0)))
except (ValueError, TypeError):
    fuel_level = 0

# ---------------------------------------------------------------------------
# Charge power
# Hybrid portal reports in tenths of kW (e.g. 32 → 3.2 kW)
# Pure EV portal already reports in kW
# ---------------------------------------------------------------------------
try:
    charge_power_raw = float(get("charge_power", 0))
    charge_power = charge_power_raw / 10.0 if is_hybrid else charge_power_raw
except (ValueError, TypeError):
    charge_power = 0.0

# ---------------------------------------------------------------------------
# Charge rate (km/h)
# Hybrid: actual_charge_rate in tenths of km/h; 65535 = invalid/not charging
# Pure EV: already in km/h
# ---------------------------------------------------------------------------
try:
    charge_rate_raw = float(get("charge_rate", 0))
    if is_hybrid:
        charge_rate = charge_rate_raw / 10.0 if charge_rate_raw < 65535 else 0.0
    else:
        charge_rate = charge_rate_raw
except (ValueError, TypeError):
    charge_rate = 0.0

# ---------------------------------------------------------------------------
# Target SoC
# ---------------------------------------------------------------------------
try:
    target_soc = int(float(get("target_soc", 0)))
except (ValueError, TypeError):
    target_soc = 0

# ---------------------------------------------------------------------------
# Remaining charge time
# Pure EV:  "1200s"  (seconds with trailing 's')
# Hybrid:   "195"    (minutes as plain integer string)
# ---------------------------------------------------------------------------
remaining_time_raw = str(get("remaining_charge_time", "0"))
try:
    if remaining_time_raw.endswith("s"):
        remaining_seconds = int(remaining_time_raw.replace("s", "").strip())
    else:
        remaining_seconds = int(float(remaining_time_raw)) * 60
    remaining_time_fmt = time.strftime("%H:%M", time.gmtime(remaining_seconds))
except (ValueError, TypeError):
    remaining_time_fmt = "00:00"

# ---------------------------------------------------------------------------
# Outdoor temperature
# Hybrid portal: value is in tenths of Kelvin (e.g. 3001 = 300.1 K = 26.95 °C)
# Pure EV portal: value is in °C already
# ---------------------------------------------------------------------------
try:
    outdoor_temp_raw = float(get("outdoor_temp", 0))
    if is_hybrid and outdoor_temp_raw > 1000:
        outdoor_temp = round(outdoor_temp_raw / 10.0 - 273.15, 1)
    else:
        outdoor_temp = outdoor_temp_raw
except (ValueError, TypeError):
    outdoor_temp = 0.0

# ---------------------------------------------------------------------------
# Lock state
# Hybrid portal: integer code  2 = locked, 1 = unlocked
# Pure EV portal: "true" / "false"
# ---------------------------------------------------------------------------
locked_raw = str(get("locked", "true"))
if locked_raw == "2":
    locked = "true"
elif locked_raw == "1":
    locked = "false"
else:
    locked = locked_raw   # "true" or "false" as-is

# ---------------------------------------------------------------------------
# Timestamp from vehicle data
# Two formats seen in the wild:
#   with milliseconds:  "2026-08-08T12:31:43.000Z"
#   without:            "2026-06-15T06:03:15Z"
# ---------------------------------------------------------------------------
car_captured_raw = str(get("car_captured_time", ""))
timestamp = car_captured_raw   # fallback: show raw value
for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
    try:
        dt = datetime.strptime(car_captured_raw, fmt)
        dt = dt.replace(tzinfo=timezone.utc).astimezone(tz=None)
        timestamp = dt.strftime("%d.%m.%Y %H:%M")
        break
    except ValueError:
        continue

# ---------------------------------------------------------------------------
# Model name (derived from vehicle type)
# Override by adding a "model" field to your MQTT topic if desired.
# ---------------------------------------------------------------------------
model = str(get("model", "Hybrid" if is_hybrid else "Electric Vehicle"))

# ---------------------------------------------------------------------------
# Build output dict (must be printable as a Python dict literal – the JS
# side replaces single quotes with double quotes before JSON.parse()).
# ---------------------------------------------------------------------------
output = {
    "status":               1,
    "error":                "",
    "remainingSoC":         soc,
    "remainingKm":          remaining_km,
    "chargingState":        charge_state,
    "chargePower":          charge_power,
    "targetSoC":            target_soc,
    "remainingChargingTime": remaining_time_fmt,
    "kmph":                 charge_rate,
    "odometer":             odometer_fmt,
    "odometer_miles":       f"{int(odometer_int * 0.6214):,}".replace(",", "."),
    "electricRange":        electric_range,
    "electricRange_miles":  int(electric_range * 0.6214),
    "gasolineRange":        gasoline_range,
    "gasolineRange_miles":  int(gasoline_range * 0.6214),
    "remainingMiles":       int(remaining_km * 0.6214),
    "miph":                 int(charge_rate * 0.6214),
    "fuelLevel":            fuel_level,
    "outdoorTemp":          outdoor_temp,
    "latitude":             0,
    "longitude":            0,
    "position":             "UNKNOWN",
    "timestamp":            timestamp,
    "model":                model,
    # Door / window states – not available via EU Data Act portal
    "bonnetDoor":           "closed",
    "trunkDoor":            "closed",
    "rearRightDoor":        "closed",
    "rearLeftDoor":         "closed",
    "frontRightDoor":       "closed",
    "frontLeftDoor":        "closed",
    "rearRightWindow":      "closed",
    "rearLeftWindow":       "closed",
    "frontRightWindow":     "closed",
    "frontLeftWindow":      "closed",
    "overallStatus":        "safe" if locked == "true" else "unsafe",
    "rightLight":           "off",
    "leftLight":            "off",
    "climatisation":        "off",
    "temperature":          20,
    "locked":               locked,
}

print(output)
