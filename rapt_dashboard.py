# ============================================================
#  IMPORTS & GLOBALS
# ============================================================
import os
import csv
import json
import base64
import threading
import requests
import time
from datetime import datetime, date
from flask import Flask, request, jsonify, render_template_string, Response, send_file
from zoneinfo import ZoneInfo

app = Flask(__name__)

uk = ZoneInfo("Europe/London")

CONFIG_FILE = "config.json"
latest_data = {}
last_received_time = None


# ============================================================
#  CONFIG HANDLING (UPDATED WITH temperature_offset)
# ============================================================
def get_config():
    """Load config.json or return defaults if missing."""
    if not os.path.exists(CONFIG_FILE):
        return {
            "profile_name": "New_Brew",
            "original_gravity": 1.050,
            "session_start": datetime.now(uk).isoformat(timespec="minutes"),
            "calibration_offset": 0.0000,
            "temperature_offset": 0.0   # <-- REQUIRED NEW KEY
        }

    with open(CONFIG_FILE, "r") as f:
        cfg = json.load(f)

    # Ensure new key exists even on older installs
    if "temperature_offset" not in cfg:
        cfg["temperature_offset"] = 0.0

    return cfg


# ============================================================
#  CSV FILENAME HELPER
# ============================================================
def get_current_brew_log_csv_filename():
    cfg = get_config()
    safe_name = cfg["profile_name"].replace(" ", "_")
    return f"logs/{safe_name}.csv"


# ============================================================
#  GRAVITY CORRECTION
# ============================================================
def corrected_gravity(raw_sg, temp_c, calibration_offset):
    """
    Apply calibration offset to gravity.
    Temperature correction is handled separately.
    """
    try:
        return raw_sg + float(calibration_offset)
    except:
        return raw_sg


# ============================================================
#  ABV CALCULATION
# ============================================================
def calc_abv(og, fg):
    try:
        return (og - fg) * 131.25
    except:
        return 0.0



#Block 2

# ============================================================
#  DASHBOARD HTML TEMPLATE  (UPDATED WITH TEMPERATURE OFFSET UI)
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

        <!-- TEMPERATURE CARD (UPDATED) -->
        <div class="card bg-zinc-900 rounded-3xl p-8">
            <p class="text-zinc-400 text-sm">TEMPERATURE</p>

            <!-- Corrected Temperature -->
            <p id="temp" class="value-line text-4xl font-semibold mt-4">
                --<span class="unit">°C</span>
            </p>

            <!-- Offset Display -->
            <p class="text-sm text-zinc-400 mt-3">
                Offset: <span id="tempOffsetDisplay">0.0</span>°C
            </p>

            <!-- Offset Controls -->
            <div class="flex gap-2 mt-3">
                <button onclick="adjustOffset(-0.1)"
                    class="px-3 py-1 bg-zinc-800 hover:bg-zinc-700 rounded-xl text-sm">-0.1</button>

                <button onclick="adjustOffset(0.1)"
                    class="px-3 py-1 bg-zinc-800 hover:bg-zinc-700 rounded-xl text-sm">+0.1</button>

                <button onclick="saveTemperatureOffset()"
                    class="px-4 py-1 bg-emerald-500 hover:bg-emerald-400 text-black font-semibold rounded-xl text-sm">
                    Save
                </button>
            </div>
        </div>

        <!-- SPECIFIC GRAVITY -->
        <div class="card bg-zinc-900 rounded-3xl p-8">
            <p class="text-zinc-400 text-sm">SPECIFIC GRAVITY</p>
            <p id="gravity" class="value-line text-4xl font-semibold mt-4">1.----</p>
        </div>

        <!-- ESTIMATED ABV -->
        <div class="card bg-zinc-900 rounded-3xl p-8">
            <p class="text-zinc-400 text-sm">ESTIMATED ABV</p>
            <p id="abv" class="value-line text-4xl font-semibold mt-4">
                --<span class="unit">%</span>
            </p>
        </div>

        <!-- BATTERY -->
        <div class="card bg-zinc-900 rounded-3xl p-8">
            <p class="text-zinc-400 text-sm">BATTERY</p>
            <p id="battery" class="value-line text-4xl font-semibold mt-4">
                --<span class="unit">%</span>
            </p>
        </div>

        <!-- SESSION LENGTH -->
        <div class="card bg-zinc-900 rounded-3xl p-8">
            <p class="text-zinc-400 text-sm">SESSION LENGTH</p>
            <p id="session" class="value-line text-2xl font-semibold mt-4">--</p>
        </div>

    </div>

    <!-- RAW DATA (HIDDEN) -->
    <pre id="raw" class="hidden"></pre>

    <!-- MODAL -->
    <div id="modal" class="hidden fixed inset-0 flex items-center justify-center modal-bg">
        <div class="bg-zinc-900 p-8 rounded-3xl w-96">
            <h2 class="text-xl font-semibold mb-4">Start New Brew</h2>

            <form id="brewForm">
                <label class="block mb-2 text-sm text-zinc-400">Profile Name</label>
                <input name="profile_name" class="w-full mb-4 p-2 rounded bg-zinc-800 text-white" required>

                <label class="block mb-2 text-sm text-zinc-400">Original Gravity</label>
                <input name="original_gravity" class="w-full mb-4 p-2 rounded bg-zinc-800 text-white" required>

                <label class="block mb-2 text-sm text-zinc-400">Calibration Offset</label>
                <input name="calibration_offset" class="w-full mb-4 p-2 rounded bg-zinc-800 text-white" required>

                <label class="block mb-2 text-sm text-zinc-400">Start Date</label>
                <input type="date" name="start_date" class="w-full mb-4 p-2 rounded bg-zinc-800 text-white">

                <label class="block mb-2 text-sm text-zinc-400">Start Time</label>
                <input type="time" name="start_time" class="w-full mb-4 p-2 rounded bg-zinc-800 text-white">

                <div class="flex justify-end gap-3 mt-4">
                    <button type="button" onclick="closeModal()"
                        class="px-4 py-2 bg-zinc-700 hover:bg-zinc-600 rounded-xl">Cancel</button>

                    <button type="submit"
                        class="px-4 py-2 bg-amber-400 hover:bg-amber-300 text-black font-semibold rounded-xl">
                        Start
                    </button>
                </div>
            </form>
        </div>
    </div>

    <!-- JAVASCRIPT (BLOCK 3 WILL BE INSERTED HERE) -->

</body>
</html>
"""



# Bolck 3

<script>
function openModal() {
    document.getElementById('modal').classList.remove('hidden')
}

function closeModal() {
    document.getElementById('modal').classList.add('hidden')
}

document.getElementById('brewForm').onsubmit = function(e) {
    e.preventDefault()

    const formData = new FormData(e.target)
    fetch('/start_brew', {
        method: 'POST',
        body: formData
    }).then(() => {
        closeModal()
        refreshData()
    })
}

function setGhStatus(msg, ok=true) {
    const el = document.getElementById('ghStatus')
    el.textContent = msg
    el.style.color = ok ? '#4ade80' : '#f97373'
    if (msg) {
        setTimeout(() => { el.textContent = ''; }, 6000)
    }
}

function pushToGitHub() {
    setGhStatus('Uploading log to GitHub...', true)
    fetch('/push_to_github', { method: 'POST' })
        .then(r => r.json())
        .then(res => {
            if (res.success) {
                setGhStatus('✅ Log uploaded to GitHub.')
            } else {
                setGhStatus('❌ Upload failed: ' + (res.error || 'Unknown error'), false)
            }
        })
        .catch(err => {
            console.error(err)
            setGhStatus('❌ Upload failed: network error', false)
        })
}

function pullFromGitHub() {
    setGhStatus('Pulling log from GitHub...', true)
    fetch('/pull_from_github', { method: 'POST' })
        .then(r => r.json())
        .then(res => {
            if (res.success) {
                setGhStatus('✅ Log pulled from GitHub.')
                refreshData()
            } else {
                setGhStatus('❌ Pull failed: ' + (res.error || 'Unknown error'), false)
            }
        })
        .catch(err => {
            console.error(err)
            setGhStatus('❌ Pull failed: network error', false)
        })
}

//
// ============================================================
//  TEMPERATURE OFFSET FUNCTIONS (NEW)
// ============================================================
//

let tempOffset = 0.0

function loadTemperatureOffset() {
    fetch('/get_temperature_offset')
        .then(r => r.json())
        .then(res => {
            tempOffset = parseFloat(res.temperature_offset || 0)
            document.getElementById('tempOffsetDisplay').textContent =
                tempOffset.toFixed(1)
        })
        .catch(() => {
            tempOffset = 0.0
        })
}

function adjustOffset(delta) {
    tempOffset = parseFloat(tempOffset) + delta
    document.getElementById('tempOffsetDisplay').textContent =
        tempOffset.toFixed(1)
}

function saveTemperatureOffset() {
    const fd = new FormData()
    fd.append("temperature_offset", tempOffset)

    fetch('/set_temperature_offset', {
        method: 'POST',
        body: fd
    })
    .then(r => r.json())
    .then(res => {
        if (res.success) {
            setGhStatus('Temperature offset saved.', true)
            refreshData()
        } else {
            setGhStatus('Failed to save offset.', false)
        }
    })
    .catch(() => {
        setGhStatus('Network error saving offset.', false)
    })
}

//
// ============================================================
//  REFRESH DATA (UPDATED TO USE CORRECTED TEMPERATURE)
// ============================================================
//

function refreshData() {
    const status = document.getElementById('status')
    status.innerHTML = '🔄 Loading...'

    fetch('/latest')
        .then(r => r.json())
        .then(result => {
            const d = result.data || {}

            // Corrected temperature already applied by backend
            document.getElementById('temp').innerHTML =
                `${d.temperature || '--'}<span class="unit">°C</span>`

            if (d.gravity_corrected) {
                document.getElementById('gravity').textContent =
                    `${parseFloat(d.gravity_corrected).toFixed(4)}`
            } else {
                document.getElementById('gravity').textContent = '1.----'
            }

            document.getElementById('abv').innerHTML =
                `${d.abv || '--'}<span class="unit">%</span>`

            document.getElementById('battery').innerHTML =
                `${Math.round(d.battery || 0)}<span class="unit">%</span>`

            document.getElementById('session').textContent =
                d.session_length || '--'

            document.getElementById('raw').textContent =
                JSON.stringify(d, null, 2)

            status.innerHTML = `✅ Last updated: ${result.timestamp || 'Unknown'}`
        })
        .catch(err => {
            console.error(err)
            status.innerHTML = '❌ Error loading data'
        })
}

//
// ============================================================
//  PAGE LOAD
// ============================================================
//

window.onload = function() {
    loadTemperatureOffset()
    refreshData()
}

setInterval(function() {
    refreshData()
}, 30000)
</script>

</body>
</html>



#Block 4

# ============================================================
#  DASHBOARD ROUTE
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
        now=now
    )


# ============================================================
#  TEMPERATURE OFFSET ENDPOINTS
# ============================================================
@app.route("/get_temperature_offset")
def get_temperature_offset():
    cfg = get_config()
    temp_offset = cfg.get("temperature_offset", 0.0)
    return jsonify({"temperature_offset": temp_offset})


@app.route("/set_temperature_offset", methods=["POST"])
def set_temperature_offset():
    cfg = get_config()
    try:
        new_offset = request.form.get("temperature_offset", "0").strip()
        new_offset = float(new_offset) if new_offset else 0.0
    except ValueError:
        new_offset = 0.0

    cfg["temperature_offset"] = new_offset

    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=4)

    return jsonify({"success": True, "temperature_offset": new_offset})


# ============================================================
#  LATEST DATA (WITH FALLBACK TO LAST CSV ENTRY)
# ============================================================
@app.route("/latest")
def get_latest():
    cfg = get_config()
    temp_offset = cfg.get("temperature_offset", 0.0)

    # If no live data, fall back to last CSV entry
    if not latest_data:
        csv_file = get_current_brew_log_csv_filename()
        if os.path.exists(csv_file):
            with open(csv_file, "r") as f:
                rows = list(csv.DictReader(f))
                if rows:
                    last = rows[-1]
                    raw_sg = float(last["gravity"])
                    raw_temp_c = float(last["temperature"])

                    # Apply temperature offset for display + correction
                    corrected_temp_c = raw_temp_c + temp_offset

                    sg_corr = corrected_gravity(raw_sg, corrected_temp_c, cfg["calibration_offset"])
                    abv = calc_abv(cfg["original_gravity"], sg_corr)

                    fallback = {
                        "temperature": round(corrected_temp_c, 2),
                        "gravity": raw_sg,
                        "gravity_corrected": round(sg_corr, 4),
                        "abv": round(abv, 3),
                        "battery": "--",
                        "session_length": "--"
                    }

                    return jsonify({"data": fallback, "timestamp": last["timestamp"]})

        return jsonify({"data": {}, "timestamp": "Never"})

    # Normal live-data path
    data_to_send = latest_data.copy()

    try:
        raw_sg = float(data_to_send.get('gravity') or 0)
        raw_temp_c = float(data_to_send.get('temperature') or 20)

        # Apply temperature offset for display + correction
        corrected_temp_c = raw_temp_c + temp_offset
        data_to_send['temperature'] = round(corrected_temp_c, 2)

        sg_corr = corrected_gravity(raw_sg, corrected_temp_c, cfg["calibration_offset"])
        data_to_send['gravity_corrected'] = round(sg_corr, 4)

        abv = calc_abv(cfg["original_gravity"], sg_corr)
        data_to_send['abv'] = round(abv, 3)

        try:
            session_start = datetime.fromisoformat(cfg["session_start"]).replace(tzinfo=uk)
            now_uk = datetime.now(uk)
            delta = now_uk - session_start
            days = delta.days
            hours = delta.seconds // 3600
            data_to_send['session_length'] = f"{days} days {hours} hours"
        except:
            data_to_send['session_length'] = "--"

    except Exception as e:
        print("Error:", e)

    ts = last_received_time.astimezone(uk).strftime("%H:%M:%S • %d %b") if last_received_time else "Never"
    return jsonify({"data": data_to_send, "timestamp": ts})


# ============================================================
#  WEBHOOK — LOGS TO CSV + AUTO‑PUSH TO GITHUB
# ============================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    global latest_data, last_received_time

    try:
        data = request.get_json() if request.is_json else request.form.to_dict()

        now_uk = datetime.now(uk)
        last_received_time = now_uk

        # Store raw data as-is (raw temperature)
        latest_data = data

        raw_sg = float(data.get("gravity") or 0)
        raw_temp_c = float(data.get("temperature") or 0)

        # Write RAW temperature to CSV (as agreed)
        append_log_entry(now_uk, raw_sg, raw_temp_c)

        # Auto-push to GitHub in background
        threading.Thread(target=push_csv_to_github_background, daemon=True).start()

        return jsonify({"success": True}), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


# ============================================================
#  START NEW BREW (SAFE VERSION)
# ============================================================
@app.route("/start_brew", methods=["POST"])
def start_brew():
    profile_name = request.form.get("profile_name", "").strip() or "Unnamed_Brew"
    og = request.form.get("original_gravity", "").strip()
    start_date = request.form.get("start_date", "")
    start_time = request.form.get("start_time", "")
    offset = request.form.get("calibration_offset", "").strip()

    if start_date and start_time:
        session_start = f"{start_date}T{start_time}"
    else:
        session_start = datetime.now(uk).isoformat(timespec="minutes")

    new_config = {
        "profile_name": profile_name,
        "original_gravity": float(og) if og else 1.050,
        "session_start": session_start,
        "calibration_offset": float(offset) if offset else 0.0000,
        # New temperature offset key, default 0.0
        "temperature_offset": 0.0
    }

    with open(CONFIG_FILE, "w") as f:
        json.dump(new_config, f, indent=4)

    return jsonify({"success": True})



#Block 5

# ============================================================
#  HEALTH CHECK (for Render keepalive)
# ============================================================
@app.route("/health")
def health():
    return "OK", 200


# ============================================================
#  KEEPALIVE THREAD (SAFE VERSION)
# ============================================================
RENDER_HEALTH_URL = "https://rapt-pill-dashboard.onrender.com/health"

def keepalive():
    # Delay first run so worker can fully boot before health checks start
    time.sleep(30)

    while True:
        try:
            requests.get(RENDER_HEALTH_URL, timeout=2)
        except Exception as e:
            print("Keepalive failed:", e)

        time.sleep(300)  # 5 minutes

threading.Thread(target=keepalive, daemon=True).start()


# ============================================================
#  LOGGING (APPEND-ONLY CSV)
# ============================================================
def append_log_entry(timestamp, raw_sg, temp_c):
    filename = get_current_brew_log_csv_filename()
    os.makedirs(os.path.dirname(filename), exist_ok=True)

    file_exists = os.path.exists(filename)

    with open(filename, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "gravity", "temperature"])
        writer.writerow([
            timestamp.astimezone(uk).isoformat(),
            raw_sg,
            temp_c
        ])


# ============================================================
#  DOWNLOAD CSV
# ============================================================
@app.route("/download_csv")
def download_csv():
    filename = get_current_brew_log_csv_filename()
    if not os.path.exists(filename):
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "gravity", "temperature"])

    return send_file(
        filename,
        as_attachment=True,
        download_name=os.path.basename(filename),
        mimetype="text/csv"
    )


# ============================================================
#  DOWNLOAD JSON (GENERATED FROM CSV)
# ============================================================
@app.route("/download_log")
def download_log():
    csv_file = get_current_brew_log_csv_filename()
    if not os.path.exists(csv_file):
        os.makedirs(os.path.dirname(csv_file), exist_ok=True)
        with open(csv_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "gravity", "temperature"])

    data = []
    with open(csv_file, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            data.append({
                "timestamp": row["timestamp"],
                "gravity": float(row["gravity"]),
                "temperature": float(row["temperature"])
            })

    json_name = os.path.basename(csv_file).replace(".csv", ".json")
    return Response(
        json.dumps(data, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename={json_name}"}
    )


# ============================================================
#  GITHUB HELPERS
# ============================================================
def _github_headers():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return None
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json"
    }


def _github_get_file(headers, base_url):
    try:
        resp = requests.get(base_url, headers=headers, params={"ref": GITHUB_BRANCH}, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception as e:
        print("GitHub GET failed:", e)
        return None


def _github_put_file(headers, base_url, payload):
    try:
        return requests.put(base_url, headers=headers, json=payload, timeout=10)
    except Exception as e:
        print("GitHub PUT failed:", e)
        return None


# ============================================================
#  MANUAL PUSH TO GITHUB
# ============================================================
@app.route("/push_to_github", methods=["POST"])
def push_to_github():
    headers = _github_headers()
    if headers is None:
        return jsonify({"success": False, "error": "GITHUB_TOKEN not set"}), 500

    csv_file = get_current_brew_log_csv_filename()
    if not os.path.exists(csv_file):
        return jsonify({"success": False, "error": "Local CSV file does not exist"}), 404

    try:
        with open(csv_file, "rb") as f:
            content_bytes = f.read()

        if len(content_bytes) < 10:
            return jsonify({"success": False, "error": "CSV too small — refusing to upload"}), 500

        content_b64 = base64.b64encode(content_bytes).decode("utf-8")

        filename = os.path.basename(csv_file)
        path = f"{GITHUB_LOG_FOLDER}/{filename}"
        base_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"

        existing = _github_get_file(headers, base_url)
        sha = existing.get("sha") if existing else None

        payload = {
            "message": f"Manual upload fermentation log {filename}",
            "content": content_b64,
            "branch": GITHUB_BRANCH
        }
        if sha:
            payload["sha"] = sha

        resp = _github_put_file(headers, base_url, payload)
        if resp and resp.status_code in (200, 201):
            return jsonify({"success": True}), 200

        return jsonify({"success": False, "error": f"GitHub error: {resp.status_code if resp else 'No response'}"}), 500

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
#  MANUAL PULL FROM GITHUB
# ============================================================
@app.route("/pull_from_github", methods=["POST"])
def pull_from_github():
    headers = _github_headers()
    if headers is None:
        return jsonify({"success": False, "error": "GITHUB_TOKEN not set"}), 500

    csv_file = get_current_brew_log_csv_filename()
    os.makedirs(os.path.dirname(csv_file), exist_ok=True)

    filename = os.path.basename(csv_file)
    path = f"{GITHUB_LOG_FOLDER}/{filename}"
    base_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"

    try:
        existing = _github_get_file(headers, base_url)
        if not existing:
            return jsonify({"success": False, "error": "File not found on GitHub"}), 404

        content_b64 = existing.get("content", "")
        if not content_b64:
            return jsonify({"success": False, "error": "GitHub file empty"}), 500

        content_bytes = base64.b64decode(content_b64)
        if len(content_bytes) < 10:
            return jsonify({"success": False, "error": "GitHub file too small — refusing to overwrite"}), 500

        with open(csv_file, "wb") as f:
            f.write(content_bytes)

        return jsonify({"success": True}), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
#  AUTO‑PUSH BACKGROUND FUNCTION (SAFE VERSION)
# ============================================================
def push_csv_to_github_background():
    try:
        headers = _github_headers()
        if headers is None:
            print("Auto-push skipped: GITHUB_TOKEN not set")
            return

        csv_file = get_current_brew_log_csv_filename()
        if not os.path.exists(csv_file):
            print("Auto-push skipped: CSV missing")
            return

        with open(csv_file, "rb") as f:
            content_bytes = f.read()

        if len(content_bytes) < 10:
            print("Auto-push skipped: CSV too small")
            return

        content_b64 = base64.b64encode(content_bytes).decode("utf-8")

        filename = os.path.basename(csv_file)
        path = f"{GITHUB_LOG_FOLDER}/{filename}"
        base_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"

        existing = _github_get_file(headers, base_url)
        sha = existing.get("sha") if existing else None

        payload = {
            "message": f"Auto-upload fermentation log {filename}",
            "content": content_b64,
            "branch": GITHUB_BRANCH
        }
        if sha:
            payload["sha"] = sha

        resp = _github_put_file(headers, base_url, payload)
        if resp and resp.status_code in (200, 201):
            print("Auto-push OK:", filename)
        else:
            print("Auto-push failed:", resp.status_code if resp else "No response")

    except Exception as e:
        print("Auto-push exception:", e)


# ============================================================
#  AUTO‑RESTORE CSV ON STARTUP (SAFE THREADED VERSION)
# ============================================================
def restore_csv_from_github_on_startup():
    """Ensures local CSV is restored after deploy before first webhook."""
    headers = _github_headers()
    if headers is None:
        print("Startup restore skipped: no GITHUB_TOKEN")
        return

    csv_file = get_current_brew_log_csv_filename()
    os.makedirs(os.path.dirname(csv_file), exist_ok=True)

    filename = os.path.basename(csv_file)
    path = f"{GITHUB_LOG_FOLDER}/{filename}"
    base_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"

    try:
        existing = _github_get_file(headers, base_url)
        if not existing:
            print("Startup restore: no file on GitHub")
            return

        content_b64 = existing.get("content", "")
        if not content_b64:
            print("Startup restore: GitHub file empty")
            return

        content_bytes = base64.b64decode(content_b64)
        if len(content_bytes) < 10:
            print("Startup restore: GitHub file too small — skipping")
            return

        with open(csv_file, "wb") as f:
            f.write(content_bytes)

        print("Startup restore: CSV restored from GitHub")

    except Exception as e:
        print("Startup restore failed:", e)


# Run restore on startup in a background thread (prevents worker blocking)
threading.Thread(
    target=restore_csv_from_github_on_startup,
    daemon=True
).start()



#Block 6

# ============================================================
#  VIEW LOG (MODERN UI, SEPARATE GRAPHS, ZOOM, DROP MARKERS)
# ============================================================
@app.route("/view_log")
def view_log():
    csv_file = get_current_brew_log_csv_filename()
    if not os.path.exists(csv_file):
        os.makedirs(os.path.dirname(csv_file), exist_ok=True)
        with open(csv_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "gravity", "temperature"])

    data = []
    with open(csv_file, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                data.append({
                    "timestamp": row["timestamp"],
                    "gravity": float(row["gravity"]),
                    "temperature": float(row["temperature"])
                })
            except:
                continue

    pretty = json.dumps(data, indent=2)
    timestamps = [d["timestamp"] for d in data]
    gravities = [d["gravity"] for d in data]
    temps = [d["temperature"] for d in data]

    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Fermentation Log Viewer</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.0.1"></script>
    <style>
        :root {{
            color-scheme: dark;
        }}
        body {{
            background: #020617;
            color: #e5e7eb;
            font-family: system-ui, sans-serif;
            margin: 0;
            padding: 24px;
        }}
        .container {{
            max-width: 1100px;
            margin: 0 auto;
        }}
        h1 {{
            font-size: 1.8rem;
            font-weight: 600;
            margin-bottom: 4px;
        }}
        .sub {{
            color: #9ca3af;
            font-size: 0.9rem;
            margin-bottom: 16px;
        }}
        a {{
            color: #fbbf24;
            text-decoration: none;
            font-size: 0.9rem;
            margin-right: 12px;
        }}
        .card {{
            background: #020617;
            border-radius: 18px;
            border: 1px solid #1f2937;
            padding: 18px 20px;
            margin-top: 16px;
        }}
        .filters {{
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }}
        .btn {{
            border-radius: 999px;
            border: 1px solid #374151;
            background: #020617;
            color: #e5e7eb;
            font-size: 0.8rem;
            padding: 6px 12px;
            cursor: pointer;
        }}
        .btn-primary {{
            background: #2563eb;
            border-color: #2563eb;
        }}
        .range-row {{
            display: flex;
            gap: 10px;
            margin-top: 10px;
            flex-wrap: wrap;
        }}
        input[type="datetime-local"] {{
            background: #020617;
            border-radius: 999px;
            border: 1px solid #374151;
            color: #e5e7eb;
            padding: 6px 10px;
            font-size: 0.8rem;
        }}
        .chart-container {{
            height: 300px;
        }}
        pre {{
            background: #020617;
            border-radius: 18px;
            border: 1px solid #1f2937;
            padding: 16px;
            font-size: 0.75rem;
            overflow: auto;
            max-height: 320px;
        }}
        .chart-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
        }}
        .chart-title {{
            font-size: 0.95rem;
            font-weight: 500;
        }}
        #ghStatusLog {{
            font-size: 0.8rem;
            margin-top: 6px;
            color: #9ca3af;
        }}
    </style>
</head>



#Block 7

<body>
<div class="container">
    <h1>Fermentation Log Viewer</h1>
    <p class="sub">Visualise and explore your fermentation history from the CSV log.</p>

    <p>
        <a href="/download_log">⬇ Download JSON</a>
        <a href="/download_csv">⬇ Download CSV</a>
    </p>

    <div class="card">
        <div class="filters">
            <button class="btn btn-primary" onclick="applyPreset(24)">Last 24h</button>
            <button class="btn" onclick="applyPreset(48)">Last 48h</button>
            <button class="btn" onclick="resetFilter()">Show All</button>
        </div>

        <div class="range-row">
            <div>
                <label>Start</label><br>
                <input type="datetime-local" id="startRange">
            </div>
            <div>
                <label>End</label><br>
                <input type="datetime-local" id="endRange">
            </div>
            <div>
                <label>&nbsp;</label><br>
                <button class="btn btn-primary" onclick="applyCustomRange()">Apply</button>
            </div>
        </div>

        <div class="range-row" style="margin-top:14px;">
            <button class="btn" onclick="pushToGitHubLog()">Push CSV → GitHub</button>
            <button class="btn" onclick="pullFromGitHubLog()">Pull CSV ← GitHub</button>
            <span id="ghStatusLog"></span>
        </div>
    </div>

    <div class="card">
        <div class="chart-header">
            <div class="chart-title">Gravity</div>
            <button class="btn" onclick="resetZoom('gravity')">Reset Zoom</button>
        </div>
        <div class="chart-container">
            <canvas id="gravityChart"></canvas>
        </div>
    </div>

    <div class="card">
        <div class="chart-header">
            <div class="chart-title">Temperature</div>
            <button class="btn" onclick="resetZoom('temp')">Reset Zoom</button>
        </div>
        <div class="chart-container">
            <canvas id="tempChart"></canvas>
        </div>
    </div>

    <div class="card">
        <h3>JSON Output (filtered)</h3>
        <pre id="jsonOutput">{pretty}</pre>
    </div>
</div>

<script>
    const fullData = {json.dumps(data)};

    function filterData(start, end) {{
        return fullData.filter(row => {{
            const t = new Date(row.timestamp).getTime();
            return t >= start && t <= end;
        }});
    }}

    function applyPreset(hours) {{
        const end = Date.now();
        const start = end - (hours * 3600 * 1000);
        render(filterData(start, end));
    }}

    function applyCustomRange() {{
        const s = document.getElementById("startRange").value;
        const e = document.getElementById("endRange").value;
        if (!s || !e) return;
        render(filterData(new Date(s).getTime(), new Date(e).getTime()));
    }}

    function resetFilter() {{
        render(fullData);
    }}

    function computeDropPoints(data) {{
        const dropPoints = [];
        const threshold = 0.0005; // rapid drop threshold
        for (let i = 1; i < data.length; i++) {{
            const prev = data[i - 1];
            const curr = data[i];
            const drop = prev.gravity - curr.gravity;
            if (drop > threshold) {{
                dropPoints.push({{ x: curr.timestamp, y: curr.gravity }});
            }}
        }}
        return dropPoints;
    }}

    function render(data) {{
        const ts = data.map(d => d.timestamp);
        const gs = data.map(d => d.gravity);
        const ts2 = data.map(d => d.temperature);
        const dropPoints = computeDropPoints(data);

        document.getElementById("jsonOutput").textContent =
            JSON.stringify(data, null, 2);

        gravityChart.data.labels = ts;
        gravityChart.data.datasets[0].data = gs;
        gravityChart.data.datasets[1].data = dropPoints;
        gravityChart.update();

        tempChart.data.labels = ts;
        tempChart.data.datasets[0].data = ts2;
        tempChart.update();
    }}

    function resetZoom(which) {{
        if (which === 'gravity') {{
            gravityChart.resetZoom();
        }} else if (which === 'temp') {{
            tempChart.resetZoom();
        }}
    }}

    function setGhStatusLog(msg, ok=true) {{
        const el = document.getElementById('ghStatusLog');
        el.textContent = msg;
        el.style.color = ok ? '#4ade80' : '#f97373';
        if (msg) {{
            setTimeout(() => {{ el.textContent = ''; }}, 6000);
        }}
    }}

    function pushToGitHubLog() {{
        setGhStatusLog('Uploading log to GitHub...', true);
        fetch('/push_to_github', {{ method: 'POST' }})
            .then(r => r.json())
            .then(res => {{
                if (res.success) {{
                    setGhStatusLog('✅ Log uploaded to GitHub.');
                }} else {{
                    setGhStatusLog('❌ Upload failed: ' + (res.error || 'Unknown error'), false);
                }}
            }})
            .catch(err => {{
                console.error(err);
                setGhStatusLog('❌ Upload failed: network error', false);
            }});
    }}

    function pullFromGitHubLog() {{
        setGhStatusLog('Pulling log from GitHub...', true);
        fetch('/pull_from_github', {{ method: 'POST' }})
            .then(r => r.json())
            .then(res => {{
                if (res.success) {{
                    setGhStatusLog('✅ Log pulled from GitHub.');
                    window.location.reload();
                }} else {{
                    setGhStatusLog('❌ Pull failed: ' + (res.error || 'Unknown error'), false);
                }}
            }})
            .catch(err => {{
                console.error(err);
                setGhStatusLog('❌ Pull failed: network error', false);
            }});
    }}

    const gravityChart = new Chart(
        document.getElementById('gravityChart').getContext('2d'),
        {{
            type: 'line',
            data: {{
                labels: {timestamps},
                datasets: [
                    {{
                        label: 'Gravity',
                        data: {gravities},
                        borderColor: '#22c55e',
                        tension: 0.25
                    }},
                    {{
                        label: 'Rapid Drop',
                        data: [],
                        pointRadius: 6,
                        pointBackgroundColor: 'red',
                        showLine: false,
                        type: 'scatter'
                    }}
                ]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                scales: {{
                    x: {{
                        ticks: {{ color: '#9ca3af' }},
                        grid: {{ color: '#111827' }}
                    }},
                    y: {{
                        ticks: {{ color: '#9ca3af' }},
                        grid: {{ color: '#111827' }}
                    }}
                }},
                plugins: {{
                    legend: {{
                        labels: {{ color: '#e5e7eb' }}
                    }},
                    zoom: {{
                        zoom: {{
                            wheel: {{ enabled: true }},
                            pinch: {{ enabled: true }},
                            mode: 'x'
                        }},
                        pan: {{
                            enabled: true,
                            mode: 'x'
                        }}
                    }}
                }}
            }}
        }}
    );

    const tempChart = new Chart(
        document.getElementById('tempChart').getContext('2d'),
        {{
            type: 'line',
            data: {{
                labels: {timestamps},
                datasets: [{{
                    label: 'Temperature (°C)',
                    data: {temps},
                    borderColor: '#fbbf24',
                    tension: 0.25
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                scales: {{
                    x: {{
                        ticks: {{ color: '#9ca3af' }},
                        grid: {{ color: '#111827' }}
                    }},
                    y: {{
                        ticks: {{ color: '#9ca3af' }},
                        grid: {{ color: '#111827' }}
                    }}
                }},
                plugins: {{
                    legend: {{
                        labels: {{ color: '#e5e7eb' }}
                    }},
                    zoom: {{
                        zoom: {{
                            wheel: {{ enabled: true }},
                            pinch: {{ enabled: true }},
                            mode: 'x'
                        }},
                        pan: {{
                            enabled: true,
                            mode: 'x'
                        }}
                    }}
                }}
            }}
        }}
    );

    render(fullData);
</script>

</body>
</html>
"""
