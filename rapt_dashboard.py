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
            # Ensure key exists (reuse for temperature offset)
            if "calibration_offset" not in cfg:
                cfg["calibration_offset"] = 0.0
            return cfg
        except Exception as e:
            print("Error loading config:", e)
    
    return {
        "profile_name": "Unknown Beer",
        "original_gravity": 1.050,
        "session_start": datetime.now(uk).isoformat(),
        "calibration_offset": 0.0,   # Now used as temperature display offset (°C)
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

# ============================================================
# PER‑BREW FILENAME HELPERS
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
# DASHBOARD HTML TEMPLATE (with Temp Offset UI)
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
            <p class="text-yellow-400 font-bold">
                Live Fermentation Monitor • OG: {{ original_gravity }}
            </p>
        </div>
        <div class="flex flex-wrap gap-3 justify-end">
            <a href="/view_log" target="_blank" rel="noopener"
               class="flex items-center gap-2 bg-zinc-800 hover:bg-zinc-700 text-blue-400 text-sm px-4 py-3 rounded-2xl">
                📄 <span>View Log</span>
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

    <!-- STATUS -->
    <div id="status" class="max-w-5xl mx-auto mb-2 p-5 rounded-3xl bg-zinc-900 text-lg font-medium">
        Waiting for data...
    </div>
    <div id="ghStatus" class="max-w-5xl mx-auto mb-6 text-sm text-zinc-400"></div>

    <!-- CARDS -->
    <div class="max-w-5xl mx-auto grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-6">
        <!-- TEMPERATURE CARD with Offset Controls -->
        <div class="card bg-zinc-900 rounded-3xl p-8">
            <p class="text-zinc-400 text-sm">TEMPERATURE</p>
            <p id="temp" class="value-line text-4xl font-semibold mt-4">
                --<span class="unit">°C</span>
            </p>
            
            <!-- Temperature Offset Controls (reusing calibration_offset) -->
            <div class="mt-6 flex items-center justify-between text-sm">
                <span class="text-zinc-400">Temp Offset</span>
                <div class="flex items-center gap-3 bg-zinc-800 rounded-2xl px-3 py-1">
                    <button onclick="adjustTempOffset(-0.1)" 
                            class="w-9 h-9 flex items-center justify-center hover:bg-red-900/50 text-red-400 rounded-xl text-2xl font-light leading-none">
                        −
                    </button>
                    <span id="tempOffsetDisplay" class="font-mono text-emerald-400 w-20 text-center">+0.0 °C</span>
                    <button onclick="adjustTempOffset(0.1)" 
                            class="w-9 h-9 flex items-center justify-center hover:bg-emerald-900/50 text-emerald-400 rounded-xl text-2xl font-light leading-none">
                        +
                    </button>
                </div>
            </div>
        </div>

        <div class="card bg-zinc-900 rounded-3xl p-8">
            <p class="text-zinc-400 text-sm">SPECIFIC GRAVITY</p>
            <p id="gravity" class="value-line text-4xl font-semibold mt-4">1.----</p>
        </div>
        <div class="card bg-zinc-900 rounded-3xl p-8">
            <p class="text-zinc-400 text-sm">ESTIMATED ABV</p>
            <p id="abv" class="value-line text-4xl font-semibold mt-4">
                --<span class="unit">%</span>
            </p>
        </div>
        <div class="card bg-zinc-900 rounded-3xl p-8">
            <p class="text-zinc-400 text-sm">BATTERY</p>
            <p id="battery" class="value-line text-4xl font-semibold mt-4">
                --<span class="unit">%</span>
            </p>
        </div>
        <div class="card bg-zinc-900 rounded-3xl p-8">
            <p class="text-zinc-400 text-sm">SESSION LENGTH</p>
            <p id="session" class="value-line text-2xl font-semibold mt-4">--</p>
        </div>
    </div>

    <!-- REFRESH BUTTON -->
    <div class="max-w-5xl mx-auto mt-8">
        <button onclick="refreshData()"
            class="w-full bg-white text-black hover:bg-amber-400 font-semibold py-4 rounded-3xl text-lg">
            ↻ REFRESH NOW
        </button>
    </div>

    <!-- RAW DATA -->
    <div class="max-w-5xl mx-auto mt-10">
        <p class="text-zinc-400 mb-2 text-sm">RAW DATA (debug):</p>
        <pre id="raw" class="bg-zinc-900 p-6 rounded-3xl text-xs font-mono overflow-auto max-h-96"></pre>
    </div>

    <!-- MODAL (unchanged) -->
    <div id="modal" class="hidden fixed inset-0 modal-bg flex items-center justify-center">
        <div class="bg-zinc-900 p-8 rounded-3xl w-full max-w-lg">
            <h2 class="text-2xl font-semibold mb-4">Start New Brew & Fermentation Profile</h2>
            <form id="brewForm">
                <label class="block mb-3">
                    <span class="text-zinc-300">Profile Name</span>
                    <input name="profile_name" class="w-full mt-1 p-3 rounded-xl bg-zinc-800 text-white" />
                </label>
                <label class="block mb-3">
                    <span class="text-zinc-300">Original Gravity</span>
                    <input name="original_gravity" type="number" step="0.001"
                        class="w-full mt-1 p-3 rounded-xl bg-zinc-800 text-white" />
                </label>
                <label class="block mb-3">
                    <span class="text-zinc-300">Start Date</span>
                    <input name="start_date" type="date"
                        value="{{ today }}"
                        class="w-full mt-1 p-3 rounded-xl bg-zinc-800 text-white" />
                </label>
                <label class="block mb-3">
                    <span class="text-zinc-300">Start Time</span>
                    <input name="start_time" type="time"
                        value="{{ now }}"
                        class="w-full mt-1 p-3 rounded-xl bg-zinc-800 text-white" />
                </label>
                <label class="block mb-6">
                    <span class="text-zinc-300">Calibration Offset (Gravity)</span>
                    <input name="calibration_offset" type="number" step="0.0001"
                        value="0.0000"
                        class="w-full mt-1 p-3 rounded-xl bg-zinc-800 text-white" />
                </label>
                <div class="flex justify-end gap-4">
                    <button type="button" onclick="closeModal()"
                        class="px-5 py-3 rounded-xl bg-zinc-700 hover:bg-zinc-600">
                        Cancel
                    </button>
                    <button type="submit"
                        class="px-5 py-3 rounded-xl bg-amber-400 hover:bg-amber-300 text-black font-semibold">
                        Save & Start Brew
                    </button>
                </div>
            </form>
        </div>
    </div>

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
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ temperature_offset: currentTempOffset })
    }).then(() => refreshData());
}

function openModal() { document.getElementById('modal').classList.remove('hidden') }
function closeModal() { document.getElementById('modal').classList.add('hidden') }

document.getElementById('brewForm').onsubmit = function(e) {
    e.preventDefault();
    const formData = new FormData(e.target);
    fetch('/start_brew', { method: 'POST', body: formData })
        .then(() => { closeModal(); refreshData(); });
}

function setGhStatus(msg, ok=true) {
    const el = document.getElementById('ghStatus');
    el.textContent = msg;
    el.style.color = ok ? '#4ade80' : '#f97373';
    if (msg) setTimeout(() => { el.textContent = ''; }, 6000);
}

function pushToGitHub() { /* existing */ 
    setGhStatus('Uploading log to GitHub...', true);
    fetch('/push_to_github', { method: 'POST' })
        .then(r => r.json()).then(res => {
            if (res.success) setGhStatus('✅ Log uploaded to GitHub.');
            else setGhStatus('❌ Upload failed: ' + (res.error || 'Unknown error'), false);
        });
}

function pullFromGitHub() { /* existing */ 
    setGhStatus('Pulling log from GitHub...', true);
    fetch('/pull_from_github', { method: 'POST' })
        .then(r => r.json()).then(res => {
            if (res.success) { setGhStatus('✅ Log pulled from GitHub.'); refreshData(); }
            else setGhStatus('❌ Pull failed: ' + (res.error || 'Unknown error'), false);
        });
}

function refreshData() {
    const status = document.getElementById('status');
    status.innerHTML = '🔄 Loading...';
    
    fetch('/latest')
        .then(r => r.json())
        .then(result => {
            const d = result.data || {};
            
            // Temperature with offset applied
            let displayTemp = '--';
            if (typeof d.temperature === 'number') {
                const offset = d.calibration_offset || 0;
                displayTemp = (d.temperature + offset).toFixed(1);
            }
            document.getElementById('temp').innerHTML = `${displayTemp}<span class="unit">°C</span>`;

            if (d.gravity_corrected) {
                document.getElementById('gravity').textContent = parseFloat(d.gravity_corrected).toFixed(4);
            } else {
                document.getElementById('gravity').textContent = '1.----';
            }
            
            document.getElementById('abv').innerHTML = `${d.abv || '--'}<span class="unit">%</span>`;
            document.getElementById('battery').innerHTML = `${Math.round(d.battery || 0)}<span class="unit">%</span>`;
            document.getElementById('session').textContent = d.session_length || '--';
            document.getElementById('raw').textContent = JSON.stringify(d, null, 2);
            
            status.innerHTML = `✅ Last updated: ${result.timestamp || 'Unknown'}`;
            
            // Update offset display
            currentTempOffset = d.calibration_offset || 0.0;
            updateTempOffsetDisplay();
        })
        .catch(err => {
            console.error(err);
            status.innerHTML = '❌ Error loading data';
        });
}

window.onload = function() { refreshData(); };
setInterval(refreshData, 30000);
</script>
</body>
</html>"""

# ============================================================
# DASHBOARD ROUTE
# ============================================================
@app.route("/")
def dashboard():
    today = date.today().isoformat()
    now = datetime.now().strftime("%H:%M")
    cfg = get_config()
    return render_template_string(
        HTML_TEMPLATE,
        profile_name=cfg["profile_name"],
        original_gravity=cfg["original_gravity"],
        today=today,
        now=now,
    )

# ============================================================
# LATEST DATA
# ============================================================
@app.route("/latest")
def get_latest():
    cfg = get_config()
    if not latest_data:
        csv_file = get_current_brew_log_csv_filename()
        if os.path.exists(csv_file):
            with open(csv_file, "r") as f:
                rows = list(csv.DictReader(f))
                if rows:
                    last = rows[-1]
                    raw_sg = float(last["gravity"])
                    temp_c = float(last["temperature"])
                    # Apply temperature offset
                    temp_with_offset = temp_c + cfg.get("calibration_offset", 0.0)
                    sg_corr = corrected_gravity(raw_sg, temp_with_offset, cfg.get("calibration_offset", 0.0))  # gravity offset still separate
                    abv = calc_abv(cfg["original_gravity"], sg_corr)
                    fallback = {
                        "temperature": temp_c,   # raw for debug
                        "gravity": raw_sg,
                        "gravity_corrected": round(sg_corr, 4),
                        "abv": round(abv, 3),
                        "battery": "--",
                        "session_length": "--",
                        "calibration_offset": cfg.get("calibration_offset", 0.0)
                    }
                    return jsonify({"data": fallback, "timestamp": last["timestamp"]})
        return jsonify({"data": {}, "timestamp": "Never"})

    # Live data path
    data_to_send = latest_data.copy()
    try:
        raw_sg = float(data_to_send.get("gravity") or 0)
        temp_c = float(data_to_send.get("temperature") or 20)
        temp_with_offset = temp_c + cfg.get("calibration_offset", 0.0)
        
        sg_corr = corrected_gravity(raw_sg, temp_with_offset, cfg.get("calibration_offset", 0.0))
        data_to_send["gravity_corrected"] = round(sg_corr, 4)
        abv = calc_abv(cfg["original_gravity"], sg_corr)
        data_to_send["abv"] = round(abv, 3)
        
        try:
            session_start = datetime.fromisoformat(cfg["session_start"]).replace(tzinfo=uk)
            now_uk = datetime.now(uk)
            delta = now_uk - session_start
            data_to_send["session_length"] = f"{delta.days} days {delta.seconds // 3600} hours"
        except Exception:
            data_to_send["session_length"] = "--"
    except Exception as e:
        print("Error in get_latest:", e)
    
    data_to_send["calibration_offset"] = cfg.get("calibration_offset", 0.0)
    
    ts = last_received_time.astimezone(uk).strftime("%H:%M:%S • %d %b") if last_received_time else "Never"
    return jsonify({"data": data_to_send, "timestamp": ts})

# ============================================================
# NEW: SET TEMPERATURE OFFSET (reuses calibration_offset)
# ============================================================
@app.route("/set_temp_offset", methods=["POST"])
def set_temp_offset():
    try:
        data = request.get_json()
        offset = float(data.get("temperature_offset", 0.0))
        
        cfg = load_config()
        cfg["calibration_offset"] = round(offset, 1)
        
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=4)
        
        # Auto-push updated config to GitHub
        threading.Thread(target=push_csv_to_github_background, daemon=True).start()
        
        return jsonify({"success": True, "calibration_offset": cfg["calibration_offset"]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

# ============================================================
# Remaining routes (webhook, start_brew, health, etc.) remain unchanged
# ============================================================
# (Copy all the rest of your original code from @app.route("/webhook") downwards unchanged)

# ... [All your existing webhook, start_brew, health, keepalive, append_log_entry, 
# download routes, GitHub helpers, push/pull functions, view_log route, etc.] ...

# Only change needed in get_current_brew_log_base if you want, but it's fine as-is.

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)