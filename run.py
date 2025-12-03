#!/usr/bin/env python3
import re
import time
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
import paho.mqtt.client as mqtt
from urllib.parse import urljoin

import os
from dotenv import load_dotenv

load_dotenv()

URL = "https://webcad.chesco.org/WebCad/webcad.asp"

# Optional: later you can filter by municipality like before
TARGET_FILTERS = {
    f.strip()
    for f in os.getenv("TARGET_FILTERS", "").split(",")
    if f.strip()
}

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")

MQTT_TOPIC = os.getenv("MQTT_TOPIC", "chesco/cad/official_summary")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "60"))

# Regex for one incident row, after stripping HTML:
#   F25065066 FIRE ALARM LANCASTER AVE & COUNTRY CLUB DR East Caln Township 11-29-2025 00:31:04 46
ROW_RE = re.compile(
    r"^(?P<id>[A-Z]\d+)\s+"
    r"(?P<type>.+?)\s{2,}"
    r"(?P<location>.+?)\s+"
    r"(?P<municipality>.+?)\s+"
    r"(?P<date>\d{2}-\d{2}-\d{4})\s+"
    r"(?P<time>\d{2}:\d{2}:\d{2})\s+"
    r"(?P<station>\S+)$"
)

UNIT_RE = re.compile(
    r"^(?P<date>\d{2}-\d{2}-\d{4})\s+"
    r"(?P<time>\d{2}:\d{2}:\d{2})\s+"
    r"(?P<unit>[^>]+)>\s+(?P<status>.+)$"
)


def get_incidents():
    try:
        r = requests.get(
            URL,
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ha-chescofire/1.0)"}
        )
        r.raise_for_status()
    except Exception as e:
        print(f"Failed to fetch main page {URL}: {e}", flush=True)
        return []

    soup = BeautifulSoup(r.text, "html.parser")

    # Build mapping from incident numbers to their comments URLs
    incident_links = {}
    for a in soup.find_all("a"):
        text = a.get_text(strip=True)
        href = a.get("href") or ""
        if not text or not href:
            continue
        if "livecadcomments" in href.lower():
            incident_links[text] = urljoin(URL, href)

    # Flatten to text lines
    text = soup.get_text("\n", strip=True)
    lines = [l for l in text.splitlines() if l.strip()]

    print("DEBUG (official): first 40 lines from page:")
    for i, line in enumerate(lines[:40]):
        print(f"{i:02d}: {repr(line)}")

    now = datetime.now(ZoneInfo("America/New_York"))
    cutoff = now - timedelta(hours=8)

    incidents = []

    current_category = None  # "FIRE", "EMS", "TRAFFIC"

    # We expect layout like:
    #   Fire Incidents
    #   Incident No.
    #   Incident Type
    #   Incident Location
    #   Municipality
    #   Dispatch Time
    #   Station
    #   F25065066
    #   FIRE ALARM
    #   LANCASTER AVE & COUNTRY CLUB DR
    #   East Caln Township
    #   11-29-2025 00:31:04
    #   46
    #
    # Same pattern repeats for EMS Incidents and Traffic Incidents.
    id_re = re.compile(r"^[A-Z]\d+$")

    i = 0
    while i < len(lines):
        line = lines[i]

        # Track which section we're in
        if line == "Fire Incidents":
            current_category = "FIRE"
            i += 1
            continue
        if line == "EMS Incidents":
            current_category = "EMS"
            i += 1
            continue
        if line == "Traffic Incidents":
            current_category = "TRAFFIC"
            i += 1
            continue

        if line.startswith("Incident No.") or line.startswith("Last Updated"):
            i += 1
            continue

        # Detect start of an incident block by ID pattern
        if not id_re.match(line):
            i += 1
            continue

        # Ensure we have at least 6 lines for a full incident
        if i + 5 >= len(lines):
            break

        incident_id = lines[i].strip()
        incident_type = lines[i + 1].strip()
        location = lines[i + 2].strip()
        municipality = lines[i + 3].strip()
        ts_str = lines[i + 4].strip()
        station = lines[i + 5].strip()

        i += 6  # move to the next possible block

        # Timestamp should look like "MM-DD-YYYY HH:MM:SS"
        if not re.match(r"^\d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2}$", ts_str):
            continue

        try:
            dt = datetime.strptime(ts_str, "%m-%d-%Y %H:%M:%S").replace(
                tzinfo=ZoneInfo("America/New_York")
            )
        except ValueError:
            continue

        if dt < cutoff:
            continue

        description = (
            f"{location} | {municipality} | {incident_type} | "
            f"{current_category or 'UNKNOWN'} | Stn {station}"
        )

        category = current_category or "UNKNOWN"

        comments_url = incident_links.get(incident_id, "")
        units_on_scene = get_units_on_scene(comments_url)

        incident = {
            "timestamp": dt.isoformat(),
            "id": incident_id,
            "location": location,
            "municipality": municipality,
            "type": incident_type,
            "category": category,
            "station": station,
            "description": description,
            "raw_timestamp": ts_str,
            "comments_url": comments_url,
            "units_on_scene": units_on_scene,
        }

        incidents.append(incident)

    return incidents


def get_units_on_scene(comments_url: str):
    if not comments_url:
        return []

    try:
        r = requests.get(
            comments_url,
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ha-chescofire/1.0)"}
        )
        r.raise_for_status()
    except Exception as e:
        print(f"Failed to fetch comments {comments_url}: {e}", flush=True)
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text("\n", strip=True)
    lines = [l for l in text.splitlines() if l.strip()]

    print("DEBUG (comments): first 20 lines from", comments_url)
    for i, line in enumerate(lines[:20]):
        print(f"{i:02d}: {repr(line)}")

    units = []
    for line in lines:
        line_stripped = line.strip()
        line_lower = line_stripped.lower()

        # Only care about lines that mention some form of "scene"
        if "scene" not in line_lower:
            continue
        if ">" not in line_stripped:
            continue

        # Example line formats include:
        # "11-29-2025 00:40:31 ENG45> On Scene"
        # "ENG45> ON SCENE"
        # "ENG45> At Scene"
        before, _sep, _after = line_stripped.partition(">")
        # Take the last token before ">" as the unit (e.g. ENG45)
        tokens = before.split()
        if not tokens:
            continue
        unit = tokens[-1].strip()
        if unit and unit not in units:
            units.append(unit)

    return units


def filter_incidents(incidents):
    if not TARGET_FILTERS:
        return incidents

    filtered = []
    for inc in incidents:
        if any(key in inc.get("municipality", "") for key in TARGET_FILTERS):
            filtered.append(inc)
    return filtered


def main_loop():
    client = mqtt.Client(
        client_id="chesco_cad_official",
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    while True:
        try:
            try:
                client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            except OSError as e:
                print(f"MQTT connect failed: {e}. Will retry after delay.", flush=True)
                time.sleep(POLL_INTERVAL)
                continue

            all_incidents = get_incidents()
            my_incidents = filter_incidents(all_incidents)

            payload = {
                "last_update": datetime.now(ZoneInfo("America/New_York")).isoformat(),
                "total_incidents": len(all_incidents),
                "filtered_incidents": len(my_incidents),
                "incidents": my_incidents,
            }

            print(json.dumps(payload, indent=2), flush=True)

            client.publish(MQTT_TOPIC, json.dumps(payload), retain=True)
            print("Published to MQTT (official)", flush=True)

            client.disconnect()

        except Exception as e:
            print(f"Error in loop: {e}", flush=True)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main_loop()