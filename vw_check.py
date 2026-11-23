#!/usr/bin/env python3
"""Manuelle Diagnose-Abfrage des VW EU Data Act Portals."""
import json, os, getpass
from datetime import datetime, timezone
from carconnectivity_connectors.vw_eu_data_act.client import EudaApiClient

NOW = datetime.now(timezone.utc)

def parse(ts):
    ts = str(ts)
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    if ts.isdigit() and len(ts) == 13:
        return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None

def age(dt):
    if dt is None:
        return "?"
    m = (NOW - dt).total_seconds() / 60
    return f"vor {m:.0f} min ({m/60:.1f} h)"

email = input("VW E-Mail: ").strip()
password = getpass.getpass("VW Passwort (Eingabe unsichtbar): ")
vin = input("VIN: ").strip()

api = EudaApiClient(email=email, password=password, country="de", language="de")
api.login()
identifier = api.get_metadata(vin).get("Identifier")
print("\nJetzt (UTC):", NOW.strftime("%Y-%m-%d %H:%M:%S"))
print("Identifier :", identifier)

listing = api.list_datasets(vin, identifier)
listing = sorted(listing, key=lambda e: e.get("createdOn", ""))
print(f"\n=== Die letzten 8 von {len(listing)} Dateien im Portal ===")
for e in listing[-8:]:
    dt = parse(e.get("createdOn", ""))
    print(f"{e['name']:50s} {e.get('size','?'):>7s} B  erstellt {e.get('createdOn')}  {age(dt)}")

content = [e for e in listing if not e["name"].endswith("_no_content_found.zip")]
if not content:
    raise SystemExit("Keine Datei mit Inhalt gefunden.")
newest = content[-1]
print("\nNeueste Datei MIT Inhalt:", newest["name"])
print("Neueste Datei insgesamt :", listing[-1]["name"])

data = api.download_dataset(vin, identifier, newest["name"])
outfile = os.path.expanduser(f"~/vw_dataset_{vin}.json")
with open(outfile, "w") as f:
    json.dump(data, f, indent=2)
print("Rohdaten gespeichert in :", outfile)

TS_FIELDS = ("timestamp", "car_captured_time", "car_captured_utc_timestamp")
entries = data.get("Data", [])

print("\n=== Zeitstempel im Datensatz ===")
for name in TS_FIELDS:
    vals = [(parse(e["value"]), e["value"]) for e in entries
            if e.get("dataFieldName") == name and e.get("value") and parse(e["value"])]
    if vals:
        mn, mx = min(vals)[1], max(vals)[1]
        print(f"{name:30s} Anzahl {len(vals):3d}")
        print(f"   aeltester: {mn}")
        print(f"   neuester : {mx}  {age(max(vals)[0])}")

tsu = [(parse(e["timestampUtc"]), e["timestampUtc"]) for e in entries
       if e.get("timestampUtc") and parse(e["timestampUtc"])]
if tsu:
    print(f"{'timestampUtc (pro Feld)':30s} Anzahl {len(tsu):3d}")
    print(f"   aeltester: {min(tsu)[1]}")
    print(f"   neuester : {max(tsu)[1]}  {age(max(tsu)[0])}")

print("\n=== Was vw_mqtt.py veroeffentlichen wuerde ===")
last_occ = None
for e in entries:
    if e.get("dataFieldName") == "car_captured_time" and e.get("value"):
        last_occ = e["value"]
print("car_captured_time (LETZTES Vorkommen in der Liste):", last_occ, age(parse(last_occ)) if last_occ else "")

print("\n=== Akku-Werte ===")
for e in entries:
    n = e.get("dataFieldName", "")
    if "soc" in n.lower() or "state_of_charge" in n or "battery_level" in n:
        print(f"{n:45s} = {e.get('value')}   {e.get('timestampUtc','')}")
