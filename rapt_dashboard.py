from flask import Flask, jsonify, render_template_string, request, send_file, Response
from datetime import datetime, date
import json
import os
import csv
import re
import zoneinfo
import threading
import time
import requests
import base64

uk = zoneinfo.ZoneInfo("Europe/London")

app = Flask(__name__)

latest_data = {}
last_received_time = None
csv_lock = threading.Lock()

CONFIG_FILE = "config.json"

GITHUB_OWNER = "DaveFL22"
GITHUB_REPO = "rapt-pill-dashboard"
GITHUB_BRANCH = "main"
GITHUB_LOG_FOLDER = "Recipe_Brew_Logs"


# ============================================================
# CONFIG HANDLING
# ============================================================
def load_config():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass

    return {
        "profile_name": "Unknown Beer",
        "original_gravity": 1.050,
        "session_start": datetime.now(uk).isoformat(timespec="minutes"),
        "calibration_offset": 0.0000,
    }


def get_config():
    return load_config()


# ============================================================
# GRAVITY + ABV FUNCTIONS
# ============================================================
def sg_to_plato(sg):
    return -616.868 + 1111.14 * sg - 630.272 * (sg ** 2) + 135.997 * (sg ** 3)


def plato_to_sg(plato):
    return 1 + (plato / (258.6 - ((plato / 258.2) * 227.1)))


def corrected_gravity(raw_sg, temp_c, offset):
    plato = sg_to_plato(raw_sg)
    plato_corr = plato + (0.00023 * (temp_c - 20))
    sg_corr = plato_to_sg(plato_corr)
    sg_corr += offset
    return sg_corr


def calc_abv(og, fg):
    return (76.08 * (og - fg) / (1.775 - og)) * (fg / 0.794)


def safe_session_length(start_iso):
    try:
        session_start = datetime.fromisoformat(start_iso)
        if session_start.tzinfo is None:
            session_start = session_start.replace(tzinfo=uk)
        now_uk = datetime.now(uk)
        delta = now_uk - session_start
        days = delta.days
        hours = delta.seconds // 3600
        return f"{days} days {hours} hours"
    except Exception:
        return "--"


def get_current_brew_log_base():
    try:
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
    except Exception:
        return "fermentation_logs/Unknown_Brew_unknown_date"

    profile = cfg.get("profile_name", "Unknown_Brew")
    start = cfg.get("session_start", "")

    safe_profile = re.sub(r"[^A-Za-z0-9]+", "_", profile).strip("_")

    try:
        dt = datetime.fromisoformat(start)
        date_str = dt.strftime("%Y-%m-%d")
    except Exception:
        date_str = "unknown_date"

    return f"fermentation_logs/{safe_profile}_{date_str}"


def get_current_brew_log_csv_filename():
    return get_current_brew_log_base() + ".csv"


def append_log_entry(timestamp, raw_sg, temp_c):
    filename = get_current_brew_log_csv_filename()
    os.makedirs(os.path.dirname(filename), exist_ok=True)

    with csv_lock:
        file_exists = os.path.exists(filename)
        with open(filename, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["timestamp", "gravity", "temperature"])
            writer.writerow([
                timestamp.astimezone(uk).isoformat(),
                raw_sg,
                temp_c,
            ])


def read_last_csv_row():
    filename = get_current_brew_log_csv_filename()
    if not os.path.exists(filename):
        return None

    try:
        with csv_lock:
            with open(filename, "r") as f:
                rows = list(csv.DictReader(f))
                return rows[-1] if rows else None
    except Exception:
        return None


# ============================================================
# YOUR ORIGINAL HTML TEMPLATE (UNCHANGED)
# ============================================================
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ profile_name }} - RAPT Pill Dashboard</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
body { font-family: 'Inter', system-ui, sans-serif; }
.card { transition: all 0.3s ease; }
.card:hover { transform: translateY(-4px); }
.unit { font-size: 0.25em; color: #22c55e; margin-left: 4px; }
.value-line { min-height: 3.2rem; }
.modal-bg { background: rgba(0,0,0,0.7); }
</style>
</head>

<body class="bg-zinc-950 text-white min-h-screen p-6">

<div class="max-w-5xl mx-auto flex flex-col gap-3 md:flex-row md:justify-between md:items-center mb-6">
<div>
<h1 class="text-4xl font-semibold">{{ profile_name }} - RAPT Pill Dashboard</h1>
<p class="text-yellow-400 font-bold">
Live Fermentation Monitor • OG: {{ original_gravity }}
</p>
</div>

<div class="flex flex-wrap gap-3 justify-end">
<a href="/view_log" target="_blank"
class="bg-zinc-800 hover:bg-zinc-700 text-blue-400 text-sm px-4 py-3 rounded-2xl">
📄 View Log
</a>

<button onclick="pushToGitHub()"
class="bg-zinc-800 hover:bg-zinc-700 text-emerald-400 text-sm px-4 py-3 rounded-2xl">
⬆ Upload Log to GitHub
</button>

<button onclick="pullFromGitHub()"
class="bg-zinc-800 hover:bg-zinc-700 text-amber-300 text-sm px-4 py-3 rounded-2xl">
⬇ Pull Log from GitHub
</button>

<button onclick="openModal()"
class="bg-amber-400 hover:bg-amber-300 text-black font-semibold px-6 py-3 rounded-2xl">
+ Start New Brew
</button>
</div>
</div>

<div id="status" class="max-w-5xl mx-auto mb-2 p-5 rounded-3xl bg-zinc-900 text-lg font-medium">
Waiting for data...
</div>

<div class="max-w-5xl mx-auto grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-6">

<div class="card bg-zinc-900 rounded-3xl p-8">
<p class="text-zinc-400 text-sm">TEMPERATURE</p>
<p id="temp" class="value-line text-4xl font-semibold mt-4">-- °C</p>
</div>

<div class="card bg-zinc-900 rounded-3xl p-8">
<p class="text-zinc-400 text-sm">SPECIFIC GRAVITY</p>
<p id="gravity" class="value-line text-4xl font-semibold mt-4">1.----</p>
</div>

<div class="card bg-zinc-900 rounded-3xl p-8">
<p class="text-zinc-400 text-sm">ESTIMATED ABV</p>
<p id="abv" class="value-line text-4xl font-semibold mt-4">-- %</p>
</div>

<div class="card bg-zinc-900 rounded-3xl p-8">
<p class="text-zinc-400 text-sm">BATTERY</p>
<p id="battery" class="value-line text-4xl font-semibold mt-4">-- %</p>
</div>

<div class="card bg-zinc-900 rounded-3xl p-8">
<p class="text-zinc-400 text-sm">SESSION LENGTH</p>
<p id="session" class="value-line text-2xl font-semibold mt-4">--</p>
</div>

</div>

<script>
function refreshData() {
fetch('/latest')
.then(r => r.json())
.then(result => {
const d = result.data || {}

document.getElementById('temp').innerText = (d.temperature || '--') + ' °C'
document.getElementById('gravity').innerText = d.gravity_corrected || '1.----'
document.getElementById('abv').innerText = (d.abv || '--') + ' %'
document.getElementById('battery').innerText = (d.battery || '--') + ' %'
document.getElementById('session').innerText = d.session_length || '--'

document.getElementById('status').innerText =
'Last updated: ' + (result.timestamp || 'Unknown')
})
}

setInterval(refreshData, 30000)
refreshData()
</script>

</body>
</html>
"""

# ============================================================
# ROUTES
# ============================================================
@app.route("/")
def dashboard():
    cfg = get_config()
    return render_template_string(
        HTML_TEMPLATE,
        profile_name=cfg["profile_name"],
        original_gravity=cfg["original_gravity"],
        today=date.today().isoformat(),
        now=datetime.now().strftime("%H:%M"),
    )


@app.route("/latest")
def get_latest():
    cfg = get_config()

    if not latest_data:
        last = read_last_csv_row()
        if last:
            try:
                raw_sg = float(last["gravity"])
                temp_c = float(last["temperature"])
                sg_corr = corrected_gravity(raw_sg, temp_c, cfg["calibration_offset"])
                abv = calc_abv(cfg["original_gravity"], sg_corr)

                return jsonify({
                    "data": {
                        "temperature": temp_c,
                        "gravity": raw_sg,
                        "gravity_corrected": round(sg_corr, 4),
                        "abv": round(abv, 3),
                        "battery": "--",
                        "session_length": safe_session_length(cfg["session_start"]),
                    },
                    "timestamp": last["timestamp"],
                })
            except Exception:
                pass

        return jsonify({"data": {}, "timestamp": "Never"})

    data_to_send = latest_data.copy()

    raw_sg = float(data_to_send.get("gravity") or 0)
    temp_c = float(data_to_send.get("temperature") or 20)

    sg_corr = corrected_gravity(raw_sg, temp_c, cfg["calibration_offset"])
    data_to_send["gravity_corrected"] = round(sg_corr, 4)
    data_to_send["abv"] = round(calc_abv(cfg["original_gravity"], sg_corr), 3)
    data_to_send["session_length"] = safe_session_length(cfg["session_start"])

    ts = last_received_time.astimezone(uk).strftime("%H:%M:%S • %d %b") if last_received_time else "Never"
    return jsonify({"data": data_to_send, "timestamp": ts})


@app.route("/webhook", methods=["POST"])
def webhook():
    global latest_data, last_received_time

    data = request.get_json() if request.is_json else request.form.to_dict()

    if "gravity" not in data or "temperature" not in data:
        return jsonify({"success": False}), 400

    now_uk = datetime.now(uk)
    last_received_time = now_uk
    latest_data = data

    append_log_entry(now_uk, float(data["gravity"]), float(data["temperature"]))

    threading.Thread(target=lambda: None, daemon=True).start()

    return jsonify({"success": True})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
