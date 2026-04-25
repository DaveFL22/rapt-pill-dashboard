import os
import json
import csv
import base64
import threading
import time
from datetime import datetime, timezone, timedelta

import requests
from flask import Flask, request, jsonify, send_file, Response

app = Flask(__name__)

# UK timezone
uk = timezone(timedelta(hours=1))

# Load config.json
with open("config.json") as f:
    config = json.load(f)

# Globals
latest_data = {}
last_received_time = None

# GitHub settings
GITHUB_OWNER = config.get("github_owner")
GITHUB_REPO = config.get("github_repo")
GITHUB_BRANCH = config.get("github_branch", "main")
GITHUB_LOG_FOLDER = config.get("github_log_folder", "logs")



#Block 2

@app.route("/")
def index():
    return """
<!DOCTYPE html>
<html>
<head>
<title>RAPT Dashboard</title>
<style>
body { font-family: Arial; background: #111; color: #eee; }
.card { background: #222; padding: 20px; margin: 20px; border-radius: 10px; }
button { padding: 6px 12px; margin: 4px; }
</style>
</head>
<body>

<h1>RAPT Pill Dashboard</h1>

<div class="card">
    <h3>Gravity</h3>
    <p id="gravity">--</p>
</div>

<div class="card">
    <h3>Temperature</h3>
    <p id="temperature">--</p>

    <h4>Offset</h4>
    <p><span id="tempOffsetValue">0.0</span> °C</p>

    <button onclick="adjustTempOffset(-0.1)">-0.1°C</button>
    <button onclick="adjustTempOffset(0.1)">+0.1°C</button>
    <button onclick="saveTempOffset()">Save</button>
</div>

<div class="card">
    <h3>Battery</h3>
    <p id="battery">--</p>
</div>

<div class="card">
    <h3>Session Length</h3>
    <p id="session">--</p>
</div>

<script>
let tempOffset = 0.0;

function adjustTempOffset(amount) {
    tempOffset = parseFloat((tempOffset + amount).toFixed(2));
    document.getElementById("tempOffsetValue").innerText = tempOffset;
}

function saveTempOffset() {
    fetch("/set_temperature_offset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ temperature_offset: tempOffset })
    })
    .then(r => r.json())
    .then(data => alert("Temperature offset saved"));
}

function loadTempOffset() {
    fetch("/get_temperature_offset")
        .then(r => r.json())
        .then(data => {
            tempOffset = data.temperature_offset;
            document.getElementById("tempOffsetValue").innerText = tempOffset;
        });
}

function refreshLatest() {
    fetch("/latest")
        .then(r => r.json())
        .then(data => {
            document.getElementById("gravity").innerText = data.gravity;
            document.getElementById("temperature").innerText = data.temperature;
            document.getElementById("battery").innerText = data.battery;
            document.getElementById("session").innerText = data.session_length;
        });
}

loadTempOffset();
setInterval(refreshLatest, 5000);
</script>

</body>
</html>
"""



#Block 3

@app.route("/get_temperature_offset")
def get_temperature_offset():
    return jsonify({"temperature_offset": config.get("temperature_offset", 0.0)})


@app.route("/set_temperature_offset", methods=["POST"])
def set_temperature_offset():
    data = request.json
    new_offset = float(data.get("temperature_offset", 0.0))

    config["temperature_offset"] = new_offset

    with open("config.json", "w") as f:
        json.dump(config, f, indent=4)

    return jsonify({"success": True})


@app.route("/webhook", methods=["POST"])
def webhook():
    global latest_data, last_received_time

    data = request.json
    timestamp = datetime.now(uk)

    # Temperature correction
    raw_temp_c = float(data.get("temperature"))
    temp_c = raw_temp_c + config.get("temperature_offset", 0.0)

    # Gravity
    raw_sg = float(data.get("gravity"))
    corrected_sg = raw_sg + config.get("calibration_offset", 0.0)

    # Session length
    if last_received_time is None:
        session_length = "0 min"
    else:
        diff = timestamp - last_received_time
        session_length = f"{int(diff.total_seconds() // 60)} min"

    last_received_time = timestamp

    latest_data = {
        "gravity": round(corrected_sg, 3),
        "temperature": round(temp_c, 2),
        "battery": data.get("battery"),
        "session_length": session_length,
        "timestamp": timestamp.isoformat()
    }

    append_log_entry(timestamp, corrected_sg, temp_c)

    threading.Thread(target=push_csv_to_github_background, daemon=True).start()

    return "OK", 200


@app.route("/latest")
def latest():
    return jsonify(latest_data)



#Block 4

def get_current_brew_log_csv_filename():
    brew_name = config.get("brew_name", "brew")
    safe_name = brew_name.replace(" ", "_")
    return f"fermentation_logs/{safe_name}.csv"


def append_log_entry(timestamp, sg, temp_c):
    filename = get_current_brew_log_csv_filename()
    os.makedirs(os.path.dirname(filename), exist_ok=True)

    new_file = not os.path.exists(filename)

    with open(filename, "a", newline="") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(["timestamp", "gravity", "temperature"])
        writer.writerow([timestamp.isoformat(), sg, temp_c])


def _github_headers():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return None
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json"
    }


def _github_get_file(headers, url):
    try:
        r = requests.get(url, headers=headers, timeout=10)
        return r.json() if r.status_code == 200 else None
    except:
        return None


def _github_put_file(headers, url, payload):
    try:
        return requests.put(url, headers=headers, json=payload, timeout=10)
    except:
        return None


def push_csv_to_github_background():
    try:
        headers = _github_headers()
        if not headers:
            return

        csv_file = get_current_brew_log_csv_filename()
        if not os.path.exists(csv_file):
            return

        with open(csv_file, "rb") as f:
            content_bytes = f.read()

        if len(content_bytes) < 10:
            return

        content_b64 = base64.b64encode(content_bytes).decode("utf-8")

        filename = os.path.basename(csv_file)
        path = f"{GITHUB_LOG_FOLDER}/{filename}"
        url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"

        existing = _github_get_file(headers, url)
        sha = existing.get("sha") if existing else None

        payload = {
            "message": f"Auto-upload fermentation log {filename}",
            "content": content_b64,
            "branch": GITHUB_BRANCH
        }
        if sha:
            payload["sha"] = sha

        _github_put_file(headers, url, payload)

    except Exception as e:
        print("Auto-push error:", e)


def restore_csv_from_github_on_startup():
    headers = _github_headers()
    if not headers:
        return

    csv_file = get_current_brew_log_csv_filename()
    os.makedirs(os.path.dirname(csv_file), exist_ok=True)

    filename = os.path.basename(csv_file)
    path = f"{GITHUB_LOG_FOLDER}/{filename}"
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"

    try:
        existing = _github_get_file(headers, url)
        if not existing:
            return

        content_b64 = existing.get("content", "")
        content_bytes = base64.b64decode(content_b64)

        if len(content_bytes) < 10:
            return

        with open(csv_file, "wb") as f:
            f.write(content_bytes)

        print("Startup restore: CSV restored")

    except Exception as e:
        print("Startup restore failed:", e)


threading.Thread(target=restore_csv_from_github_on_startup, daemon=True).start()


def keepalive():
    time.sleep(30)
    while True:
        try:
            requests.get("https://rapt-pill-dashboard.onrender.com/health", timeout=2)
        except:
            pass
        time.sleep(300)


threading.Thread(target=keepalive, daemon=True).start()


@app.route("/health")
def health():
    return "OK", 200



#Block 5


@app.route("/view_log")
def view_log():
    csv_file = get_current_brew_log_csv_filename()

    if not os.path.exists(csv_file):
        return """
        <html>
        <body style='background:#111; color:#eee; font-family:Arial;'>
            <h1>No log file found</h1>
            <p>A log file will be created after the first webhook is received.</p>
            <a href='/' style='color:#4af;'>Back to Dashboard</a>
        </body>
        </html>
        """

    rows = []
    with open(csv_file) as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    html = """
    <html>
    <head>
    <style>
        body { background:#111; color:#eee; font-family:Arial; }
        table { border-collapse: collapse; width: 100%; margin-top:20px; }
        th, td { border:1px solid #444; padding:8px; text-align:left; }
        th { background:#222; }
        tr:nth-child(even) { background:#1a1a1a; }
        a { color:#4af; }
        .topbar { margin-bottom:20px; }
    </style>
    </head>
    <body>

    <div class='topbar'>
        <h1>Fermentation Log</h1>
        <a href='/download_csv'>Download CSV</a> |
        <a href='/'>Back to Dashboard</a>
    </div>

    <table>
        <tr>
            <th>Timestamp</th>
            <th>Gravity</th>
            <th>Temperature (°C)</th>
        </tr>
    """

    for r in rows:
        html += f"""
        <tr>
            <td>{r['timestamp']}</td>
            <td>{r['gravity']}</td>
            <td>{r['temperature']}</td>
        </tr>
        """

    html += """
    </table>
    </body>
    </html>
    """

    return html
