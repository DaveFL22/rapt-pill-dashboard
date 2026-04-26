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

CONFIG_FILE = "config.json"
GITHUB_OWNER = "DaveFL22"
GITHUB_REPO = "rapt-pill-dashboard"
GITHUB_BRANCH = "main"
GITHUB_LOG_FOLDER = "Recipe_Brew_Logs"

# ============================================================
# CONFIG HANDLING
# ============================================================
def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)
            if "calibration_offset" not in cfg:
                cfg["calibration_offset"] = 0.0
            return cfg
        except Exception as e:
            print("Error loading config:", e)
    
    return {
        "profile_name": "Unknown Beer",
        "original_gravity": 1.050,
        "session_start": datetime.now(uk).isoformat(),
        "calibration_offset": 0.0,   # Reused for Temperature Display Offset (°C)
    }

# ============================================================
# GRAVITY + ABV FUNCTIONS
# ============================================================
def sg_to_plato(sg):
    return -616.868 + 1111.14 * sg - 630.272 * (sg ** 2) + 135.997 * (sg ** 3)

def plato_to_sg(plato):
    return 1 + (plato / (258.6 - ((plato / 258.2) * 227.1)))

def corrected_gravity(raw_sg, temp_c, grav_offset):
    plato = sg_to_plato(raw_sg)
    plato_corr = plato + (0.00023 * (temp_c - 20))
    sg_corr = plato_to_sg(plato_corr)
    sg_corr += grav_offset
    return sg_corr

def calc_abv(og, fg):
    return (76.08 * (og - fg) / (1.775 - og)) * (fg / 0.794)

# ============================================================
# FILENAME HELPERS
# ============================================================
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

# ============================================================
# DASHBOARD HTML TEMPLATE
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

    <!-- HEADER -->
    <div class="max-w-5xl mx-auto flex flex-col gap-3 md:flex-row md:justify-between md:items-center mb-6">
        <div>
            <h1 class="text-4xl font-semibold">{{ profile_name }} - RAPT Pill Dashboard</h1>
            <p class="text-yellow-400 font-bold">Live Fermentation Monitor • OG: {{ original_gravity }}</p>
        </div>
        <div class="flex flex-wrap gap-3 justify-end">
            <a href="/view_log" target="_blank" class="flex items-center gap-2 bg-zinc-800 hover:bg-zinc-700 text-blue-400 text-sm px-4 py-3 rounded-2xl">📄 View Log</a>
            <button onclick="pushToGitHub()" class="bg-zinc-800 hover:bg-zinc-700 text-emerald-400 text-sm px-4 py-3 rounded-2xl">⬆ Upload Log</button>
            <button onclick="pullFromGitHub()" class="bg-zinc-800 hover:bg-zinc-700 text-amber-300 text-sm px-4 py-3 rounded-2xl">⬇ Pull Log</button>
            <button onclick="openModal()" class="bg-amber-400 hover:bg-amber-300 text-black font-semibold px-6 py-3 rounded-2xl">+ New Brew</button>
        </div>
    </div>

    <div id="status" class="max-w-5xl mx-auto mb-2 p-5 rounded-3xl bg-zinc-900 text-lg font-medium">Waiting for data...</div>
    <div id="ghStatus" class="max-w-5xl mx-auto mb-6 text-sm text-zinc-400"></div>

    <div class="max-w-5xl mx-auto grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-6">
        <!-- TEMPERATURE CARD -->
        <div class="card bg-zinc-900 rounded-3xl p-8">
            <p class="text-zinc-400 text-sm">TEMPERATURE</p>
            <p id="temp" class="value-line text-4xl font-semibold mt-4">--<span class="unit">°C</span></p>
            
            <div class="mt-6 flex items-center justify-between text-sm">
                <span class="text-zinc-400">Temp Offset</span>
                <div class="flex items-center gap-3 bg-zinc-800 rounded-2xl px-3 py-1">
                    <button onclick="adjustTempOffset(-0.1)" class="w-9 h-9 flex items-center justify-center hover:bg-red-900/50 text-red-400 rounded-xl text-2xl font-light">−</button>
                    <span id="tempOffsetDisplay" class="font-mono text-emerald-400 w-20 text-center">+0.0 °C</span>
                    <button onclick="adjustTempOffset(0.1)" class="w-9 h-9 flex items-center justify-center hover:bg-emerald-900/50 text-emerald-400 rounded-xl text-2xl font-light">+</button>
                </div>
            </div>
        </div>

        <div class="card bg-zinc-900 rounded-3xl p-8">
            <p class="text-zinc-400 text-sm">SPECIFIC GRAVITY</p>
            <p id="gravity" class="value-line text-4xl font-semibold mt-4">1.----</p>
        </div>
        <div class="card bg-zinc-900 rounded-3xl p-8">
            <p class="text-zinc-400 text-sm">ESTIMATED ABV</p>
            <p id="abv" class="value-line text-4xl font-semibold mt-4">--<span class="unit">%</span></p>
        </div>
        <div class="card bg-zinc-900 rounded-3xl p-8">
            <p class="text-zinc-400 text-sm">BATTERY</p>
            <p id="battery" class="value-line text-4xl font-semibold mt-4">--<span class="unit">%</span></p>
        </div>
        <div class="card bg-zinc-900 rounded-3xl p-8">
            <p class="text-zinc-400 text-sm">SESSION LENGTH</p>
            <p id="session" class="value-line text-2xl font-semibold mt-4">--</p>
        </div>
    </div>

    <div class="max-w-5xl mx-auto mt-8">
        <button onclick="refreshData()" class="w-full bg-white text-black hover:bg-amber-400 font-semibold py-4 rounded-3xl text-lg">↻ REFRESH NOW</button>
    </div>

    <div class="max-w-5xl mx-auto mt-10">
        <p class="text-zinc-400 mb-2 text-sm">RAW DATA (debug):</p>
        <pre id="raw" class="bg-zinc-900 p-6 rounded-3xl text-xs font-mono overflow-auto max-h-96"></pre>
    </div>

    <!-- Modal and other JS unchanged except for new functions -->
    <!-- (The full modal and other parts are the same as your original) -->

<script>
let currentTempOffset = 0.0;

function updateTempOffsetDisplay() {
    const el = document.getElementById('tempOffsetDisplay');
    const sign = currentTempOffset >= 0 ? '+' : '';
    el.textContent = `${sign}${currentTempOffset.toFixed(1)} °C`;
}

function adjustTempOffset(delta) {
    currentTempOffset = Math.round((currentTempOffset + delta) * 10) / 10;
    updateTempOffsetDisplay();
    fetch('/set_temp_offset', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ temperature_offset: currentTempOffset })
    }).then(() => refreshData());
}

// ... (rest of your original JavaScript: openModal, refreshData, pushToGitHub, etc. remains the same) ...

function refreshData() {
    const status = document.getElementById('status');
    status.innerHTML = '🔄 Loading...';
    fetch('/latest')
        .then(r => r.json())
        .then(result => {
            const d = result.data || {};
            
            // Apply temperature offset for display
            let displayTemp = '--';
            if (typeof d.temperature === 'number') {
                const offset = d.calibration_offset || 0;
                displayTemp = (d.temperature + offset).toFixed(1);
            }
            document.getElementById('temp').innerHTML = `${displayTemp}<span class="unit">°C</span>`;

            if (d.gravity_corrected) {
                document.getElementById('gravity').textContent = parseFloat(d.gravity_corrected).toFixed(4);
            }
            document.getElementById('abv').innerHTML = `${d.abv || '--'}<span class="unit">%</span>`;
            document.getElementById('battery').innerHTML = `${Math.round(d.battery || 0)}<span class="unit">%</span>`;
            document.getElementById('session').textContent = d.session_length || '--';
            document.getElementById('raw').textContent = JSON.stringify(d, null, 2);

            status.innerHTML = `✅ Last updated: ${result.timestamp || 'Unknown'}`;
            currentTempOffset = d.calibration_offset || 0.0;
            updateTempOffsetDisplay();
        })
        .catch(() => status.innerHTML = '❌ Error loading data');
}

window.onload = refreshData;
setInterval(refreshData, 30000);
</script>
</body>
</html>"""

# ============================================================
# ROUTES
# ============================================================
@app.route("/")
def dashboard():
    today = date.today().isoformat()
    now = datetime.now().strftime("%H:%M")
    cfg = load_config()
    return render_template_string(HTML_TEMPLATE, 
                                  profile_name=cfg["profile_name"],
                                  original_gravity=cfg["original_gravity"],
                                  today=today, now=now)

@app.route("/health")
def health():
    return "OK", 200

@app.route("/latest")
def get_latest():
    cfg = load_config()
    temp_offset = cfg.get("calibration_offset", 0.0)

    if not latest_data:
        csv_file = get_current_brew_log_csv_filename()
        if os.path.exists(csv_file):
            with open(csv_file, "r") as f:
                rows = list(csv.DictReader(f))
                if rows:
                    last = rows[-1]
                    raw_sg = float(last["gravity"])
                    raw_temp = float(last["temperature"])
                    temp_with_offset = raw_temp + temp_offset
                    sg_corr = corrected_gravity(raw_sg, temp_with_offset, cfg.get("calibration_offset", 0.0))
                    abv = calc_abv(cfg["original_gravity"], sg_corr)
                    fallback = {
                        "temperature": raw_temp,
                        "gravity": raw_sg,
                        "gravity_corrected": round(sg_corr, 4),
                        "abv": round(abv, 3),
                        "battery": "--",
                        "session_length": "--",
                        "calibration_offset": temp_offset
                    }
                    return jsonify({"data": fallback, "timestamp": last["timestamp"]})
        return jsonify({"data": {}, "timestamp": "Never"})

    data_to_send = latest_data.copy()
    try:
        raw_sg = float(data_to_send.get("gravity") or 0)
        raw_temp = float(data_to_send.get("temperature") or 20)
        temp_with_offset = raw_temp + temp_offset

        sg_corr = corrected_gravity(raw_sg, temp_with_offset, cfg.get("calibration_offset", 0.0))
        data_to_send["gravity_corrected"] = round(sg_corr, 4)
        data_to_send["abv"] = round(calc_abv(cfg["original_gravity"], sg_corr), 3)

        session_start = datetime.fromisoformat(cfg["session_start"]).replace(tzinfo=uk)
        delta = datetime.now(uk) - session_start
        data_to_send["session_length"] = f"{delta.days} days {delta.seconds // 3600} hours"
    except Exception as e:
        print("Error processing latest data:", e)

    data_to_send["calibration_offset"] = temp_offset
    ts = last_received_time.astimezone(uk).strftime("%H:%M:%S • %d %b") if last_received_time else "Never"
    return jsonify({"data": data_to_send, "timestamp": ts})

@app.route("/webhook", methods=["POST"])
def webhook():
    global latest_data, last_received_time
    try:
        data = request.get_json() if request.is_json else request.form.to_dict()
        now_uk = datetime.now(uk)
        last_received_time = now_uk
        latest_data = data

        raw_sg = float(data.get("gravity") or 0)
        temp_c = float(data.get("temperature") or 0)
        append_log_entry(now_uk, raw_sg, temp_c)

        # Auto push in background
        threading.Thread(target=push_csv_to_github_background, daemon=True).start()

        return jsonify({"success": True}), 200
    except Exception as e:
        print("Webhook error:", str(e))
        return jsonify({"success": False, "error": str(e)}), 400

@app.route("/set_temp_offset", methods=["POST"])
def set_temp_offset():
    try:
        data = request.get_json()
        offset = float(data.get("temperature_offset", 0.0))
        
        cfg = load_config()
        cfg["calibration_offset"] = round(offset, 1)
        
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=4)
        
        threading.Thread(target=push_csv_to_github_background, daemon=True).start()
        
        return jsonify({"success": True, "calibration_offset": cfg["calibration_offset"]})
    except Exception as e:
        print("Set temp offset error:", str(e))
        return jsonify({"success": False, "error": str(e)}), 400

# ============================================================
# Keep the rest of your original code unchanged below:
# - start_brew, append_log_entry, download routes, GitHub helpers,
#   push_to_github, pull_from_github, push_csv_to_github_background,
#   restore on startup, view_log route, etc.
# ============================================================

# (Paste all your remaining original functions here - they don't need changes)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)