#!/usr/bin/env python3
"""
vw_mqtt.py – VW EU Data Act → MQTT bridge
==========================================
Fetches vehicle data from the Volkswagen EU Data Act portal every 15 minutes
and publishes it to a local MQTT broker.  MMM-weconnectid_MQTT reads those
retained messages on demand.

Requirements
------------
    pip3 install paho-mqtt
    pip3 install --no-deps carconnectivity
    pip3 install --no-deps git+https://github.com/mikrohard/CarConnectivity-connector-vw-eu-data-act.git

Configuration
-------------
Edit the CARS list below.  Each entry represents one vehicle / one VW account.
The "identifier" is fetched automatically on first run if left empty ("").

Usage
-----
    python3 vw_mqtt.py                  # run in foreground
    # or install as a systemd service (see README)
"""

import time
import logging
import json
import os

try:
    from carconnectivity_connectors.vw_eu_data_act.client import EudaApiClient
except ImportError:
    raise SystemExit(
        "ERROR: carconnectivity-connector-vw-eu-data-act is not installed.\n"
        "Run: pip3 install --no-deps carconnectivity\n"
        "     pip3 install --no-deps git+https://github.com/mikrohard/CarConnectivity-connector-vw-eu-data-act.git"
    )

try:
    import paho.mqtt.client as mqtt
except ImportError:
    raise SystemExit("ERROR: paho-mqtt is not installed.\nRun: pip3 install paho-mqtt")

# ===========================================================================
# USER CONFIGURATION – edit this section
# ===========================================================================

CARS = [
    {
        # VW account credentials
        "email":      "user1@example.com",
        "password":   "password1",
        # Vehicle Identification Number
        "vin":        "WVWZZZE1ZNP000000",
        # Data-request identifier from the EU Data Act portal.
        # Leave as "" to fetch automatically on first run.
        "identifier": "",
        # MQTT topic prefix – must match "username" in config.js
        "topic":      "vwdata/car1",
        # "electric" or "hybrid"
        "type":       "electric",
    },
    # Add more vehicles here:
    # {
    #     "email":      "user2@example.com",
    #     "password":   "password2",
    #     "vin":        "WV2ZZZST0TH000000",
    #     "identifier": "",
    #     "topic":      "vwdata/car2",
    #     "type":       "hybrid",
    # },
]

MQTT_HOST = "localhost"
MQTT_PORT = 1883
INTERVAL  = 900   # seconds between updates (15 minutes)

# Path to cache the auto-fetched identifiers so they survive a restart
IDENTIFIER_CACHE = os.path.join(os.path.dirname(__file__), ".identifier_cache.json")

# ===========================================================================
# Field mappings
# ===========================================================================

FIELD_MAP_ELECTRIC = {
    "battery_state_report.soc":                               "soc",
    "battery_level_HV.value":                                 "soc_hv",
    "mileage.value":                                          "odometer",
    "charging_state_report.current_charge_state":             "charging_state",
    "battery_state_report.charge_power":                      "charge_power",
    "battery_state_report.charge_rate":                       "charge_rate",
    "settings.target_soc":                                    "target_soc",
    "battery_state_report.remaining_charging_time_complete":  "remaining_charge_time",
    "min_temperature":                                        "battery_temp_min",
    "max_temperature":                                        "battery_temp_max",
    "outdoor_temperature":                                    "outdoor_temp",
    "locked":                                                 "locked",
    "charging_state_report.charge_type":                      "charge_type",
    "car_captured_time":                                      "car_captured_time",
    "range":                                                  "range_km",
}

FIELD_MAP_HYBRID = {
    "state_of_charge":                 "soc",
    "fuel_level_current_level":        "fuel_level",
    "cruising_range_primary_engine":   "range_electric",
    "cruising_range_secondary_engine": "range_fuel",
    "cruising_range_combined":         "range_combined",
    "charging_state":                  "charging_state",
    "remaining_charging_time":         "remaining_charge_time",
    "mileage":                         "odometer",
    "locked_state_front_left_door":    "locked",
    "outside_temperature":             "outdoor_temp",
    "charging_power":                  "charge_power",
    "actual_charge_rate":              "charge_rate",
    "charge_rate_unit":                "charge_rate_unit",
}

# ===========================================================================
# Identifier cache helpers
# ===========================================================================

def load_identifier_cache() -> dict:
    try:
        with open(IDENTIFIER_CACHE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_identifier_cache(cache: dict) -> None:
    try:
        with open(IDENTIFIER_CACHE, "w") as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        log.warning("Could not save identifier cache: %s", e)

# ===========================================================================
# Core logic
# ===========================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("vw_mqtt")


def get_identifier(api: EudaApiClient, vin: str, cache: dict) -> str:
    """Return identifier from cache or fetch it from the portal."""
    if vin in cache and cache[vin]:
        return cache[vin]
    log.info("[%s] Fetching data-request identifier from portal...", vin)
    meta = api.get_metadata(vin)
    identifier = meta.get("Identifier") or meta.get("identifier") or ""
    if identifier:
        cache[vin] = identifier
        save_identifier_cache(cache)
        log.info("[%s] Identifier: %s", vin, identifier)
    else:
        log.warning("[%s] Could not fetch identifier – portal may not be set up yet.", vin)
    return identifier


def fetch_and_publish(api: EudaApiClient, car: dict, mqttc, cache: dict) -> None:
    vin      = car["vin"]
    topic    = car["topic"]
    car_type = car.get("type", "electric")

    identifier = car.get("identifier") or get_identifier(api, vin, cache)
    if not identifier:
        log.warning("[%s] No identifier available – skipping.", vin)
        return

    log.info("[%s] Fetching dataset list...", vin)
    listing = api.list_datasets(vin, identifier)
    content = [
        e for e in listing
        if e.get("name") and not e["name"].endswith("_no_content_found.zip")
    ]
    if not content:
        log.warning("[%s] No data available yet.", vin)
        return

    newest = sorted(content, key=lambda e: e.get("createdOn", ""))[-1]
    log.info("[%s] Downloading %s...", vin, newest["name"])
    data = api.download_dataset(vin, identifier, newest["name"])

    # Flatten Data list into a dict (last value wins for duplicate field names)
    fields: dict = {}
    latest_ts: str = ""
    for entry in data.get("Data", []):
        name  = entry.get("dataFieldName")
        value = entry.get("value")
        ts    = entry.get("timestampUtc", "")
        if name and value is not None:
            fields[name] = value
        if ts and ts > latest_ts:
            latest_ts = ts

    # Publish the newest per-entry timestamp (used by hybrid vehicles)
    if latest_ts:
        mqttc.publish(f"{topic}/car_captured_time", latest_ts, retain=True)
        log.info("  %s/car_captured_time = %s", topic, latest_ts)

    field_map = FIELD_MAP_HYBRID if car_type == "hybrid" else FIELD_MAP_ELECTRIC
    sent = 0
    for field_name, subtopic in field_map.items():
        if field_name in fields:
            mqttc.publish(f"{topic}/{subtopic}", str(fields[field_name]), retain=True)
            log.info("  %s/%s = %s", topic, subtopic, fields[field_name])
            sent += 1
    log.info("[%s] Done: %d fields published.", vin, sent)


def main() -> None:
    mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    mqttc.connect(MQTT_HOST, MQTT_PORT)
    mqttc.loop_start()

    cache = load_identifier_cache()
    # Pre-fill cache from CARS entries that already have an identifier
    for car in CARS:
        if car.get("identifier") and car["vin"] not in cache:
            cache[car["vin"]] = car["identifier"]

    while True:
        for car in CARS:
            try:
                api = EudaApiClient(
                    email=car["email"],
                    password=car["password"],
                    country="de",
                    language="de",
                )
                api.login()
                fetch_and_publish(api, car, mqttc, cache)
                api.close()
            except Exception as e:
                log.error("[%s] Error: %s", car["vin"], e)

        log.info("Waiting %d seconds until next update...", INTERVAL)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
