# ========================================================
# RAPT Pill Fermentation Dashboard - FULL FIXED SCRIPT
# ========================================================

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
import logging

uk = zoneinfo.ZoneInfo("Europe/London")
app = Flask(__name__)

latest_data = {}
last_received_time = None

CONFIG_FILE = "config.json"
GITHUB_OWNER = "DaveFL22"
GITHUB_REPO = "rapt-pill-dashboard"
GITHUB_BRANCH = "main"
GITHUB_LOG_FOLDER = "Recipe_Brew_Logs"

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ============================================================
# CONFIG HANDLING
# ============================================================
def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Failed to load config: {e}")
    return {
        "profile_name": "Unknown Beer",
        "original_gravity": 1.050,
        "session_start": "2026-01-01T00:00:00",
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

def get_session_length(cfg):
    try:
        start_str = cfg["session_start"]
        if 'T' in start_str and ('+' not in start_str) and ('Z' not in start_str):
            dt = datetime.fromisoformat(start_str).replace(tzinfo=uk)
        else:
            dt = datetime.fromisoformat(start_str)
        delta = datetime.now(uk) - dt
        days = delta.days
        hours = delta.seconds // 3600
        return f"{days}d {hours}h"
    except Exception:
        return "--"

# ============================================================
# PER‑BREW FILENAME HELPERS
# ============================================================
def get_current_brew_log_base():
    cfg = get_config()
    profile = cfg.get("profile_name", "Unknown_Brew")
    start = cfg.get("session_start", "")
    safe_profile = re.sub(r"[^A-Za-z0-9]+", "_", profile).strip("_")
    try:
        dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
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
    <div class="max-w-5xl mx-auto flex flex-col gap-3 md:flex-row md:justify-between md:items-start mb-10">
        <div>
            <h1 class="text-4xl font-semibold">{{ profile_name }} - RAPT Pill Dashboard</h1>
            <p class="text-yellow-400 font-bold">Live Fermentation Monitor • OG: {{ original_gravity }}</p>
        </div>
        <div class="flex justify-end">
            <button onclick="openModal()" 
                class="flex items-center justify-center bg-amber-400 hover:bg-amber-300 text-black font-semibold px-6 py-3 rounded-2xl">
                + Start New Brew
            </button>
        </div>
    </div>

    <div class="max-w-5xl mx-auto mb-12">
        <a href="/view_log" target="_blank" rel="noopener"
           class="flex items-center justify-center gap-3 bg-green-600 hover:bg-green-500 text-white text-base px-6 py-3 rounded-2xl font-semibold">
            📄 <span>View Log</span>
        </a>
    </div>

    <div id="status" class="max-w-5xl mx-auto mb-2 p-5 rounded-3xl bg-zinc-900 text-lg font-medium">
        Waiting for data...
    </div>

    <div class="max-w-5xl mx-auto grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-6">
        <div class="card bg-zinc-900 rounded-3xl p-8">
            <p class="text-zinc-400 text-sm">TEMPERATURE</p>
            <p id="temp" class="value-line text-4xl font-semibold mt-4">--<span class="unit">°C</span></p>
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
        <button onclick="refreshData()" 
            class="w-full bg-white text-black hover:bg-amber-400 font-semibold py-4 rounded-3xl text-lg">
            ↻ REFRESH NOW
        </button>
    </div>

    <div class="max-w-5xl mx-auto mt-10">
        <p class="text-zinc-400 mb-2 text-sm">RAW DATA (debug):</p>
        <pre id="raw" class="bg-zinc-900 p-6 rounded-3xl text-xs font-mono overflow-auto max-h-96"></pre>
    </div>

    <!-- MODAL -->
    <div id="modal" class="hidden fixed inset-0 modal-bg flex items-center justify-center">
        <div class="bg-zinc-900 p-8 rounded-3xl w-full max-w-lg">
            <h2 class="text-2xl font-semibold mb-4">Start New Brew</h2>
            <form id="brewForm">
                <label class="block mb-3">
                    <span class="text-zinc-300">Profile Name</span>
                    <input name="profile_name" class="w-full mt-1 p-3 rounded-xl bg-zinc-800 text-white" required />
                </label>
                <label class="block mb-3">
                    <span class="text-zinc-300">Original Gravity</span>
                    <input name="original_gravity" type="number" step="0.001" value="1.050"
                        class="w-full mt-1 p-3 rounded-xl bg-zinc-800 text-white" required />
                </label>
                <label class="block mb-3">
                    <span class="text-zinc-300">Start Date</span>
                    <input name="start_date" type="date" value="{{ today }}"
                        class="w-full mt-1 p-3 rounded-xl bg-zinc-800 text-white" />
                </label>
                <label class="block mb-3">
                    <span class="text-zinc-300">Start Time</span>
                    <input name="start_time" type="time" value="{{ now }}"
                        class="w-full mt-1 p-3 rounded-xl bg-zinc-800 text-white" />
                </label>
                <label class="block mb-6">
                    <span class="text-zinc-300">Calibration Offset</span>
                    <input name="calibration_offset" type="number" step="0.0001" value="0.0000"
                        class="w-full mt-1 p-3 rounded-xl bg-zinc-800 text-white" />
                </label>
                <div class="flex justify-end gap-4">
                    <button type="button" onclick="closeModal()" 
                        class="px-5 py-3 rounded-xl bg-zinc-700 hover:bg-zinc-600">Cancel</button>
                    <button type="submit" 
                        class="px-5 py-3 rounded-xl bg-amber-400 hover:bg-amber-300 text-black font-semibold">
                        Save & Start Brew
                    </button>
                </div>
            </form>
        </div>
    </div>

<script>
function openModal() { document.getElementById('modal').classList.remove('hidden'); }
function closeModal() { document.getElementById('modal').classList.add('hidden'); }

document.getElementById('brewForm').onsubmit = function(e) {
    e.preventDefault();
    fetch('/start_brew', { method: 'POST', body: new FormData(e.target) })
        .then(() => { closeModal(); refreshData(); });
};

function refreshData() {
    const status = document.getElementById('status');
    status.innerHTML = '🔄 Loading...';

    fetch('/latest')
        .then(r => r.json())
        .then(result => {
            const d = result.data || {};
            document.getElementById('temp').innerHTML = `${d.temperature || '--'}<span class="unit">°C</span>`;
            
            const gravEl = document.getElementById('gravity');
            gravEl.textContent = d.gravity_corrected !== undefined ? parseFloat(d.gravity_corrected).toFixed(4) : '1.----';

            document.getElementById('abv').innerHTML = `${d.abv || '--'}<span class="unit">%</span>`;
            document.getElementById('battery').innerHTML = `${Math.round(d.battery || 0)}<span class="unit">%</span>`;
            document.getElementById('session').textContent = d.session_length || '--';
            document.getElementById('raw').textContent = JSON.stringify(d, null, 2);

            status.innerHTML = `✅ Last updated: ${result.timestamp || 'Never'}`;
        })
        .catch(() => status.innerHTML = '❌ Error loading data');
}

window.onload = () => refreshData();
setInterval(refreshData, 30000);
</script>
</body>
</html>
"""

# ============================================================
# DASHBOARD ROUTE
# ============================================================
@app.route("/")
def dashboard():
    today = date.today().isoformat()
    now = datetime.now(uk).strftime("%H:%M")
    cfg = get_config()
    return render_template_string(
        HTML_TEMPLATE,
        profile_name=cfg["profile_name"],
        original_gravity=cfg["original_gravity"],
        today=today,
        now=now,
    )

# ============================================================
# LATEST DATA ROUTE
# ============================================================
@app.route("/latest")
def get_latest():
    cfg = get_config()
    data_to_send = {}

    if latest_data:
        data_to_send = latest_data.copy()
        try:
            raw_sg = float(data_to_send.get("gravity", 0))
            temp_c = float(data_to_send.get("temperature", 20))
            sg_corr = corrected_gravity(raw_sg, temp_c, cfg.get("calibration_offset", 0))
            abv = calc_abv(cfg.get("original_gravity", 1.050), sg_corr)

            data_to_send["gravity_corrected"] = round(sg_corr, 4)
            data_to_send["abv"] = round(abv, 3)
        except Exception as e:
            logging.error(f"Live data processing error: {e}")
    else:
        # Fallback to CSV
        csv_file = get_current_brew_log_csv_filename()
        if os.path.exists(csv_file):
            try:
                with open(csv_file, "r") as f:
                    rows = list(csv.DictReader(f))
                    if rows:
                        last = rows[-1]
                        raw_sg = float(last["gravity"])
                        temp_c = float(last["temperature"])
                        sg_corr = corrected_gravity(raw_sg, temp_c, cfg.get("calibration_offset", 0))
                        abv = calc_abv(cfg.get("original_gravity", 1.050), sg_corr)

                        data_to_send = {
                            "temperature": round(temp_c, 2),
                            "gravity": raw_sg,
                            "gravity_corrected": round(sg_corr, 4),
                            "abv": round(abv, 3),
                            "battery": "--"
                        }
            except Exception as e:
                logging.error(f"CSV fallback error: {e}")

    data_to_send["session_length"] = get_session_length(cfg)
    ts = last_received_time.astimezone(uk).strftime("%H:%M:%S • %d %b") if last_received_time else "Never"

    return jsonify({"data": data_to_send, "timestamp": ts})

# ============================================================
# WEBHOOK
# ============================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    global latest_data, last_received_time
    try:
        data = request.get_json() if request.is_json else request.form.to_dict()

        if not data or "gravity" not in data or "temperature" not in data:
            return jsonify({"success": False, "error": "Missing gravity or temperature"}), 400

        now_uk = datetime.now(uk)
        last_received_time = now_uk
        latest_data = data.copy()

        raw_sg = float(data["gravity"])
        temp_c = float(data["temperature"])

        append_log_entry(now_uk, raw_sg, temp_c)
        threading.Thread(target=push_csv_to_github_background, daemon=True).start()

        logging.info(f"Data received - SG: {raw_sg:.4f}, Temp: {temp_c:.1f}°C")
        return jsonify({"success": True}), 200
    except Exception as e:
        logging.error(f"Webhook error: {e}")
        return jsonify({"success": False, "error": str(e)}), 400

# ============================================================
# START NEW BREW
# ============================================================
@app.route("/start_brew", methods=["POST"])
def start_brew():
    try:
        profile_name = request.form.get("profile_name", "Unnamed_Brew").strip()
        og = float(request.form.get("original_gravity", 1.050))
        start_date = request.form.get("start_date", "")
        start_time = request.form.get("start_time", "")
        offset = float(request.form.get("calibration_offset", 0))

        session_start = f"{start_date}T{start_time}" if start_date and start_time else datetime.now(uk).isoformat(timespec="minutes")

        new_config = {
            "profile_name": profile_name,
            "original_gravity": round(og, 3),
            "session_start": session_start,
            "calibration_offset": round(offset, 4),
        }

        with open(CONFIG_FILE, "w") as f:
            json.dump(new_config, f, indent=4)

        logging.info(f"New brew started: {profile_name}")
        return jsonify({"success": True})
    except Exception as e:
        logging.error(f"Start brew error: {e}")
        return jsonify({"success": False, "error": str(e)}), 400

# ============================================================
# LOGGING
# ============================================================
def append_log_entry(timestamp, raw_sg, temp_c):
    filename = get_current_brew_log_csv_filename()
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    file_exists = os.path.exists(filename)

    with open(filename, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "gravity", "temperature"])
        writer.writerow([timestamp.astimezone(uk).isoformat(), round(raw_sg, 5), round(temp_c, 2)])

# ============================================================
# DOWNLOAD ROUTES
# ============================================================
@app.route("/download_csv")
def download_csv():
    filename = get_current_brew_log_csv_filename()
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    if not os.path.exists(filename):
        with open(filename, "w", newline="") as f:
            csv.writer(f).writerow(["timestamp", "gravity", "temperature"])
    return send_file(filename, as_attachment=True, download_name=os.path.basename(filename))

@app.route("/download_json")
@app.route("/download_log")
def download_log():
    csv_file = get_current_brew_log_csv_filename()
    data = []
    if os.path.exists(csv_file):
        with open(csv_file, "r") as f:
            for row in csv.DictReader(f):
                try:
                    data.append({
                        "timestamp": row["timestamp"],
                        "gravity": float(row["gravity"]),
                        "temperature": float(row["temperature"])
                    })
                except:
                    continue
    json_name = os.path.basename(csv_file).replace(".csv", ".json")
    return Response(json.dumps(data, indent=2), mimetype="application/json",
                    headers={"Content-Disposition": f"attachment; filename={json_name}"})

# ============================================================
# GITHUB FUNCTIONS (simplified but functional)
# ============================================================
def _github_headers():
    token = os.environ.get("GITHUB_TOKEN")
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"} if token else None

def _github_get_file(headers, base_url):
    try:
        resp = requests.get(base_url, headers=headers, params={"ref": GITHUB_BRANCH}, timeout=10)
        return resp.json() if resp.status_code == 200 else None
    except:
        return None

def _github_put_file(headers, base_url, payload):
    try:
        return requests.put(base_url, headers=headers, json=payload, timeout=10)
    except:
        return None

@app.route("/push_to_github", methods=["POST"])
def push_to_github():
    headers = _github_headers()
    if not headers:
        return jsonify({"success": False, "error": "GITHUB_TOKEN not set"}), 500
    csv_file = get_current_brew_log_csv_filename()
    if not os.path.exists(csv_file):
        return jsonify({"success": False, "error": "CSV not found"}), 404

    try:
        with open(csv_file, "rb") as f:
            content_b64 = base64.b64encode(f.read()).decode("utf-8")
        filename = os.path.basename(csv_file)
        path = f"{GITHUB_LOG_FOLDER}/{filename}"
        base_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"

        existing = _github_get_file(headers, base_url)
        payload = {"message": f"Manual upload {filename}", "content": content_b64, "branch": GITHUB_BRANCH}
        if existing and existing.get("sha"):
            payload["sha"] = existing["sha"]

        resp = _github_put_file(headers, base_url, payload)
        return jsonify({"success": resp and resp.status_code in (200, 201)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/pull_from_github", methods=["POST"])
def pull_from_github():
    headers = _github_headers()
    if not headers:
        return jsonify({"success": False, "error": "GITHUB_TOKEN not set"}), 500

    csv_file = get_current_brew_log_csv_filename()
    os.makedirs(os.path.dirname(csv_file), exist_ok=True)
    filename = os.path.basename(csv_file)
    path = f"{GITHUB_LOG_FOLDER}/{filename}"
    base_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"

    try:
        existing = _github_get_file(headers, base_url)
        if not existing or not existing.get("content"):
            return jsonify({"success": False, "error": "File not found on GitHub"}), 404

        with open(csv_file, "wb") as f:
            f.write(base64.b64decode(existing["content"]))
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

def push_csv_to_github_background():
    headers = _github_headers()
    if not headers: return
    csv_file = get_current_brew_log_csv_filename()
    if not os.path.exists(csv_file): return
    try:
        with open(csv_file, "rb") as f:
            content_b64 = base64.b64encode(f.read()).decode("utf-8")
        filename = os.path.basename(csv_file)
        path = f"{GITHUB_LOG_FOLDER}/{filename}"
        base_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"

        existing = _github_get_file(headers, base_url)
        payload = {"message": f"Auto-upload {filename}", "content": content_b64, "branch": GITHUB_BRANCH}
        if existing and existing.get("sha"):
            payload["sha"] = existing["sha"]

        resp = _github_put_file(headers, base_url, payload)
        if resp and resp.status_code in (200, 201):
            logging.info(f"Auto-push successful: {filename}")
    except Exception as e:
        logging.error(f"Auto-push failed: {e}")

def restore_csv_from_github_on_startup():
    headers = _github_headers()
    if not headers: return
    csv_file = get_current_brew_log_csv_filename()
    os.makedirs(os.path.dirname(csv_file), exist_ok=True)
    filename = os.path.basename(csv_file)
    path = f"{GITHUB_LOG_FOLDER}/{filename}"
    base_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"

    try:
        existing = _github_get_file(headers, base_url)
        if existing and existing.get("content"):
            with open(csv_file, "wb") as f:
                f.write(base64.b64decode(existing["content"]))
            logging.info("CSV restored from GitHub on startup")
    except Exception as e:
        logging.error(f"Startup restore failed: {e}")

threading.Thread(target=restore_csv_from_github_on_startup, daemon=True).start()

# ============================================================
# HEALTH & KEEPALIVE
# ============================================================
@app.route("/health")
def health():
    return "OK", 200

def keepalive():
    time.sleep(30)
    while True:
        try:
            requests.get("https://rapt-pill-dashboard.onrender.com/health", timeout=3)
        except:
            pass
        time.sleep(300)

threading.Thread(target=keepalive, daemon=True).start()

# ============================================================
# FIXED LOG VIEWER HTML (Buttons now work)
# ============================================================
LOG_VIEWER_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Fermentation Log Viewer</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body class="bg-zinc-950 text-white p-6">
    <div class="max-w-5xl mx-auto">
        <div class="flex justify-between items-center mb-6">
            <h1 class="text-3xl font-semibold">Fermentation Log Viewer</h1>
            <button onclick="window.close()" 
                class="px-6 py-2 rounded-full font-bold text-white bg-gradient-to-b from-green-400 to-green-600 hover:from-green-300 hover:to-green-500">
                CLOSE
            </button>
        </div>

        <div class="flex gap-3 mb-6">
            <a href="/download_json" class="bg-blue-500 hover:bg-blue-400 text-white px-4 py-2 rounded-lg">Download JSON</a>
            <a href="/download_csv" class="bg-emerald-500 hover:bg-emerald-400 text-white px-4 py-2 rounded-lg">Download CSV</a>
        </div>

        <div class="flex flex-wrap gap-3 mb-6">
            <button id="btn1"  class="filter-btn px-4 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700">Last 1h</button>
            <button id="btn6"  class="filter-btn px-4 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700">Last 6h</button>
            <button id="btn12" class="filter-btn px-4 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700">Last 12h</button>
            <button id="btn24" class="filter-btn px-4 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700">Last 24h</button>
            <button id="btn48" class="filter-btn px-4 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700">Last 48h</button>
            <button id="btnAll" class="filter-btn px-4 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700">Show All</button>
        </div>

        <div class="flex gap-3 mb-10">
            <input type="datetime-local" id="startDateTime" class="bg-zinc-800 text-white px-3 py-2 rounded-lg">
            <input type="datetime-local" id="endDateTime" class="bg-zinc-800 text-white px-3 py-2 rounded-lg">
            <button onclick="applyDateRange()" class="bg-amber-500 hover:bg-amber-400 text-black px-4 py-2 rounded-lg">Apply Range</button>
        </div>

        <div class="flex gap-3 mb-10">
            <button onclick="pullCSV()" class="bg-zinc-800 hover:bg-zinc-700 text-amber-300 px-4 py-2 rounded-lg">⬇ Pull from GitHub</button>
            <button onclick="pushCSV()" class="bg-zinc-800 hover:bg-zinc-700 text-emerald-400 px-4 py-2 rounded-lg">⬆ Upload to GitHub</button>
        </div>

        <h2 class="text-xl font-semibold mb-2">Gravity Trend</h2>
        <canvas id="gravityChart" class="mb-12"></canvas>

        <h2 class="text-xl font-semibold mb-2">Temperature Trend (°C)</h2>
        <canvas id="tempChart"></canvas>
    </div>

<script>
let logData = {{ log_json | safe }};
let gravityChart = null;
let tempChart = null;

function setActiveButton(activeId) {
    document.querySelectorAll('.filter-btn').forEach(btn => {
        btn.classList.toggle('bg-blue-500', btn.id === activeId);
        btn.classList.toggle('text-white', btn.id === activeId);
        btn.classList.toggle('bg-zinc-800', btn.id !== activeId);
    });
}

function applyFilter(hours) {
    let cutoff = null;
    if (hours !== "all") {
        cutoff = new Date(Date.now() - hours * 60 * 60 * 1000);
    }
    const filtered = logData.filter(entry => !cutoff || new Date(entry.timestamp) >= cutoff);
    updateCharts(filtered);
    setActiveButton("btn" + (hours === "all" ? "All" : hours));
}

function applyDateRange() {
    const start = new Date(document.getElementById("startDateTime").value);
    const end = new Date(document.getElementById("endDateTime").value);
    if (isNaN(start.getTime()) || isNaN(end.getTime())) {
        alert("Please select both start and end date/time.");
        return;
    }
    const filtered = logData.filter(entry => {
        const t = new Date(entry.timestamp);
        return t >= start && t <= end;
    });
    updateCharts(filtered);
    document.querySelectorAll('.filter-btn').forEach(btn => btn.classList.remove('bg-blue-500', 'text-white'));
}

function updateCharts(data) {
    const labels = data.map(e => e.timestamp);
    const gravity = data.map(e => e.gravity_corrected);
    const temp = data.map(e => e.temperature);

    if (gravityChart) gravityChart.destroy();
    if (tempChart) tempChart.destroy();

    gravityChart = new Chart(document.getElementById("gravityChart"), {
        type: "line",
        data: { labels, datasets: [{ label: "Corrected Gravity", data: gravity, borderColor: "#22c55e", tension: 0.3, borderWidth: 3 }] },
        options: { responsive: true, maintainAspectRatio: false }
    });

    tempChart = new Chart(document.getElementById("tempChart"), {
        type: "line",
        data: { labels, datasets: [{ label: "Temperature (°C)", data: temp, borderColor: "#facc15", tension: 0.3, borderWidth: 3 }] },
        options: { responsive: true, maintainAspectRatio: false }
    });
}

function pushCSV() {
    if (confirm("⚠ WARNING: This will overwrite GitHub file. Continue?") && confirm("Are you sure?")) {
        fetch('/push_to_github', {method: 'POST'}).then(r => r.json()).then(res => alert(res.success ? "✅ Uploaded" : "❌ Failed"));
    }
}

function pullCSV() {
    if (confirm("Pull CSV from GitHub?")) {
        fetch('/pull_from_github', {method: 'POST'}).then(r => r.json()).then(res => {
            if (res.success) { alert("✅ Pulled successfully"); location.reload(); } else { alert("❌ Failed: " + (res.error || "")); }
        });
    }
}

// Initialize
document.getElementById("btn12").classList.add("bg-blue-500", "text-white");
applyFilter(12);
</script>
</body>
</html>
"""

# ============================================================
# VIEW LOG ROUTE (with corrected gravity calculation)
# ============================================================
@app.route("/view_log")
def view_log_page():
    csv_file = get_current_brew_log_csv_filename()
    os.makedirs(os.path.dirname(csv_file), exist_ok=True)
    if not os.path.exists(csv_file):
        with open(csv_file, "w", newline="") as f:
            csv.writer(f).writerow(["timestamp", "gravity", "temperature"])

    cfg = get_config()
    offset = cfg.get("calibration_offset", 0.0)
    og = cfg.get("original_gravity", 1.050)

    data = []
    with open(csv_file, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                raw_sg = float(row["gravity"])
                temp_c = float(row["temperature"])
                sg_corr = corrected_gravity(raw_sg, temp_c, offset)
                data.append({
                    "timestamp": row["timestamp"],
                    "gravity_corrected": round(sg_corr, 4),
                    "temperature": round(temp_c, 2)
                })
            except:
                continue

    return render_template_string(LOG_VIEWER_HTML, log_json=json.dumps(data))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)