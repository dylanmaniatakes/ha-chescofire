# Chester County CAD → MQTT Forwarder

This script monitors **Chester County, Pennsylvania emergency dispatch incidents** using the official data source at **webcad.chesco.org** and publishes structured incident data to **MQTT** for Home Assistant or other automation platforms.

---

## Features

- Scrapes live Chester County fire/EMS/police incidents  
- Filters incidents by municipality (optional)  
- Publishes incident summaries as JSON to a configurable MQTT topic  
- Uses a `.env` file for all sensitive settings  
- Includes a prebuilt systemd service file for automatic startup  
- Designed to run from `/opt/ha-chescofire/`

---

## Requirements

- Python 3.10 or newer  
- MQTT broker (Mosquitto, EMQX, Home Assistant Add-on, etc.)  
- Linux system with `systemd`

---

## Installation

### 1. Install the folder

```bash
sudo mkdir -p /opt/ha-chescofire
sudo cp -r ha-chescofire/* /opt/ha-chescofire/
sudo chmod -R 755 /opt/ha-chescofire
```

---

### 2. Install Python dependencies

```bash
cd /opt/ha-chescofire
pip install -r requirements.txt
```

---

### 3. Create your `.env` file

Create `/opt/ha-chescofire/.env`:

```
MQTT_HOST=10.0.0.10
MQTT_PORT=1883
MQTT_USERNAME=mqtt
MQTT_PASSWORD=yourpassword

# Topic your automations or HA sensors subscribe to
MQTT_TOPIC=chesco/cad/official_summary

# Poll interval in seconds
POLL_INTERVAL=60

# Optional comma-separated list of municipalities to include
TARGET_FILTERS=Oxford Borough,West Nottingham Township
```

---

## Running Manually

```bash
cd /opt/ha-chescofire
python3 run.py
```

---

## Running as a Service

A working systemd unit is included:  
`ha-chescofire.service`

### Install it:

```bash
sudo cp ha-chescofire.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable ha-chescofire
sudo systemctl start ha-chescofire
```

### View logs:

```bash
journalctl -u ha-chescofire -f
```

---

## MQTT Output Format

Example payload:

```json
{
  "timestamp": "2025-01-21T19:43:00-05:00",
  "type": "MEDICAL",
  "description": "INJURED PERSON",
  "location": "LOCUST ST",
  "municipality": "West Chester Borough",
  "station": "45",
  "units": ["45-1", "45-2"]
}
```

---

## Home Assistant Example

```yaml
mqtt:
  sensor:
    - name: "Chesco CAD Summary"
      state_topic: "chesco/cad/official_summary"
      value_template: "{{ value_json.type }}"
      json_attributes_topic: "chesco/cad/official_summary"
```

---

## Notes

- Do not overload the CAD website.  
- This is for awareness and automation use, not for emergency alerting.  
- No affiliation with Chester County or its public safety agencies.
