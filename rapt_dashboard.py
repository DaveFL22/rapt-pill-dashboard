#Block 1

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
#  CONFIG HANDLING
# ============================================================
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {
        "profile_name": "Unknown Beer",
        "original_gravity": 1.050,
        "session_start": "2026-01-01T00:00:00",
        "calibration_offset": 0.0000,
    }


def get_config():
    return load_config()


# ============================================================
#  GRAVITY + ABV FUNCTIONS
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
#  PER‑BREW FILENAME HELPERS
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


# Block 2A and 2B


# ============================================================
#  DASHBOARD HTML TEMPLATE (MERGED 2A + 2B, UPDATED)
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

    <!-- HEADER ROW -->
    <div class="max-w-5xl mx-auto flex flex-col gap-3 md:flex-row md:justify-between md:items-start mb-10">
        <div>
            <h1 class="text-4xl font-semibold">{{ profile_name }} - RAPT Pill Dashboard</h1>
            <p class="text-yellow-400 font-bold">
                Live Fermentation Monitor • OG: {{ original_gravity }}
            </p>
        </div>

        <div class="flex justify-end">
            <button onclick="openModal()"
                class="flex items-center justify-center bg-amber-400 hover:bg-amber-300 text-black font-semibold px-6 py-3 rounded-2xl">
                + Start New Brew
            </button>
        </div>
    </div>

    <!-- VIEW LOG BUTTON (left-aligned, same size as Start New Brew) -->
    <div class="max-w-5xl mx-auto mt-12 mb-12">
        <div class="w-full flex justify-start">
            <a href="/view_log" target="_blank" rel="noopener"
               class="flex items-center justify-center gap-3 
                      bg-green-600 hover:bg-green-500 
                      text-white text-base px-6 py-3 rounded-2xl font-semibold">
                📄 <span>View Log</span>
            </a>
        </div>
    </div>

    <!-- STATUS -->
    <div id="status" class="max-w-5xl mx-auto mb-2 p-5 rounded-3xl bg-zinc-900 text-lg font-medium">
        Waiting for data...
    </div>

    <div id="ghStatus" class="max-w-5xl mx-auto mb-6 text-sm text-zinc-400"></div>

    <!-- CARDS -->
    <div class="max-w-5xl mx-auto grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-6">

        <div class="card bg-zinc-900 rounded-3xl p-8">
            <p class="text-zinc-400 text-sm">TEMPERATURE</p>
            <p id="temp" class="value-line text-4xl font-semibold mt-4">
                --<span class="unit">°C</span>
            </p>
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

    <!-- MODAL -->
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
                    <span class="text-zinc-300">Calibration Offset</span>
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

function refreshData() {
    const status = document.getElementById('status')
    status.innerHTML = '🔄 Loading...'

    fetch('/latest')
        .then(r => r.json())
        .then(result => {
            const d = result.data || {}

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

window.onload = function() {
    refreshData()
}

setInterval(function() {
    refreshData()
}, 30000)
</script>

</body>
</html>
"""




#Block 3


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
        now=now,
    )


# ============================================================
#  LATEST DATA (WITH FALLBACK TO LAST CSV ENTRY)
# ============================================================
@app.route("/latest")
def get_latest():
    cfg = get_config()

    # If no live data, fall back to last CSV entry
    if not latest_data:
        csv_file = get_current_brew_log_csv_filename()
        if os.path.exists(csv_file):
            with open(csv_file, "r") as f:
                rows = list(csv.DictReader(f))
                if rows:
                    last = rows[-1]
                    raw_sg = float(last["gravity"])
                    temp_c = float(last["temperature"])

                    sg_corr = corrected_gravity(raw_sg, temp_c, cfg["calibration_offset"])
                    abv = calc_abv(cfg["original_gravity"], sg_corr)

                    fallback = {
                        "temperature": temp_c,
                        "gravity": raw_sg,
                        "gravity_corrected": round(sg_corr, 4),
                        "abv": round(abv, 3),
                        "battery": "--",
                        "session_length": "--",
                    }

                    return jsonify({"data": fallback, "timestamp": last["timestamp"]})

        return jsonify({"data": {}, "timestamp": "Never"})

    # Normal live-data path
    data_to_send = latest_data.copy()

    try:
        raw_sg = float(data_to_send.get("gravity") or 0)
        temp_c = float(data_to_send.get("temperature") or 20)

        sg_corr = corrected_gravity(raw_sg, temp_c, cfg["calibration_offset"])
        data_to_send["gravity_corrected"] = round(sg_corr, 4)

        abv = calc_abv(cfg["original_gravity"], sg_corr)
        data_to_send["abv"] = round(abv, 3)

        try:
            session_start = datetime.fromisoformat(cfg["session_start"]).replace(tzinfo=uk)
            now_uk = datetime.now(uk)
            delta = now_uk - session_start
            days = delta.days
            hours = delta.seconds // 3600
            data_to_send["session_length"] = f"{days} days {hours} hours"
        except Exception:
            data_to_send["session_length"] = "--"

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
        # Accept JSON or form data
        data = request.get_json(silent=True) or request.form.to_dict()

        now_uk = datetime.now(uk)
        last_received_time = now_uk

        # Store raw data for dashboard
        latest_data = data

        # Extract gravity safely
        raw_sg = (
            data.get("gravity")
            or data.get("Gravity")
            or data.get("specific_gravity")
            or data.get("sg")
            or data.get("SG")
            or 0
        )

        try:
            raw_sg = float(raw_sg)
        except:
            raw_sg = 0.0

        # Extract temperature safely
        temp_c = (
            data.get("temperature")
            or data.get("Temperature")
            or data.get("temp")
            or data.get("Temp")
            or data.get("temp_c")
            or 0
        )

        try:
            temp_c = float(temp_c)
        except:
            temp_c = 0.0

        # Write to CSV
        append_log_entry(now_uk, raw_sg, temp_c)

        # Auto-push to GitHub in background
        threading.Thread(target=push_csv_to_github_background, daemon=True).start()

        return jsonify({"success": True}), 200

    except Exception as e:
        # Never return 400 unless absolutely necessary
        return jsonify({"success": False, "error": str(e)}), 200



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
    }

    with open(CONFIG_FILE, "w") as f:
        json.dump(new_config, f, indent=4)

    return jsonify({"success": True})


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


#Block 4


# ============================================================
#  BLOCK 4 — BACKEND ROUTES (CORRECTED FOR DYNAMIC CSV + GITHUB)
# ============================================================

import os
import csv
import time
import json
import base64
import threading
import requests
from flask import request, jsonify

# ------------------------------------------------------------
#  GITHUB SETTINGS
# ------------------------------------------------------------
GITHUB_OWNER = "YOUR_GITHUB_USERNAME"
GITHUB_REPO = "YOUR_REPOSITORY_NAME"
GITHUB_LOG_FOLDER = "Recipe_Brew_Logs"   # Folder where CSVs live

GITHUB_HEADERS = {
    "Authorization": f"token {os.environ.get('GITHUB_TOKEN')}",
    "Accept": "application/vnd.github.v3+json"
}

UPLOAD_PASSWORD = os.environ.get("UPLOAD_PASSWORD")


# ------------------------------------------------------------
#  GET CURRENT BREW CSV FILENAME (LOCAL)
# ------------------------------------------------------------
def get_current_brew_log_csv_filename():
    """
    Builds the correct CSV filename based on:
    - profile_name
    - session_start
    stored in config.json
    """
    with open("config.json", "r") as f:
        cfg = json.load(f)

    profile = cfg.get("profile_name", "Unknown_Profile")
    start = cfg.get("session_start", "1970-01-01").split("T")[0]

    safe_profile = profile.replace(" ", "_")
    filename = f"{safe_profile}_{start}.csv"

    return os.path.join("fermentation_logs", filename)


# ------------------------------------------------------------
#  GET GITHUB PATH FOR CURRENT BREW CSV
# ------------------------------------------------------------
def get_github_csv_path():
    """
    Returns the GitHub path for the current brew's CSV.
    Example:
    Recipe_Brew_Logs/Madreezy_Spanish_Lager_2026-04-12.csv
    """
    local_csv = get_current_brew_log_csv_filename()
    filename = os.path.basename(local_csv)
    return f"{GITHUB_LOG_FOLDER}/{filename}"


# ------------------------------------------------------------
#  VERIFY PASSWORD BEFORE ALLOWING GITHUB UPLOAD
# ------------------------------------------------------------
@app.route('/verify_upload_password', methods=['POST'])
def verify_upload_password():
    data = request.get_json(silent=True) or {}
    pw = data.get('password', '')

    if not UPLOAD_PASSWORD:
        return jsonify({"success": False, "error": "Server password not configured"}), 500

    if pw == UPLOAD_PASSWORD:
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": "Incorrect password"}), 401


# ------------------------------------------------------------
#  PUSH CSV TO GITHUB
# ------------------------------------------------------------
@app.route('/push_to_github', methods=['POST'])
def push_to_github():
    try:
        local_csv = get_current_brew_log_csv_filename()

        if not os.path.exists(local_csv):
            return jsonify({"success": False, "error": "Local CSV does not exist"})

        with open(local_csv, "r") as f:
            csv_data = f.read()

        github_path = get_github_csv_path()
        sha_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{github_path}"

        # Get SHA if file exists
        sha_res = requests.get(sha_url, headers=GITHUB_HEADERS)
        sha = sha_res.json().get("sha") if sha_res.status_code == 200 else None

        payload = {
            "message": "Upload fermentation log",
            "content": base64.b64encode(csv_data.encode()).decode()
        }

        if sha:
            payload["sha"] = sha

        upload_res = requests.put(sha_url, headers=GITHUB_HEADERS, data=json.dumps(payload))

        if upload_res.status_code in (200, 201):
            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "error": upload_res.text})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ------------------------------------------------------------
#  PULL CSV FROM GITHUB
# ------------------------------------------------------------
@app.route('/pull_from_github', methods=['POST'])
def pull_from_github():
    try:
        github_path = get_github_csv_path()
        url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{github_path}"

        res = requests.get(url, headers=GITHUB_HEADERS)

        if res.status_code != 200:
            return jsonify({"success": False, "error": "File not found on GitHub"})

        content = res.json().get("content", "")
        decoded = base64.b64decode(content).decode("utf-8")

        local_csv = get_current_brew_log_csv_filename()
        os.makedirs(os.path.dirname(local_csv), exist_ok=True)

        with open(local_csv, "w") as f:
            f.write(decoded)

        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ------------------------------------------------------------
#  BACKGROUND THREADS
# ------------------------------------------------------------
def background_keepalive():
    while True:
        try:
            requests.get("https://api.github.com", timeout=5)
        except:
            pass
        time.sleep(300)


def background_restore_csv():
    """
    If the local CSV is missing on startup, restore it from GitHub.
    """
    local_csv = get_current_brew_log_csv_filename()

    if os.path.exists(local_csv):
        return

    try:
        github_path = get_github_csv_path()
        url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{github_path}"

        res = requests.get(url, headers=GITHUB_HEADERS)

        if res.status_code == 200:
            content = res.json().get("content", "")
            decoded = base64.b64decode(content).decode("utf-8")

            os.makedirs(os.path.dirname(local_csv), exist_ok=True)
            with open(local_csv, "w") as f:
                f.write(decoded)

    except:
        pass


# Start background threads
threading.Thread(target=background_keepalive, daemon=True).start()
threading.Thread(target=background_restore_csv, daemon=True).start()



#Block 5B

# ============================================================
#  BLOCK 5B — FULL ADVANCED FERMENTATION LOG VIEWER (FINAL)
# ============================================================

LOG_VIEWER_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Fermentation Log Viewer</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>

<body class="bg-zinc-950 text-white p-6">

    <!-- CLOSE BUTTON -->
    <div class="flex justify-end mb-4">
        <a href="/" class="px-5 py-2 rounded-xl font-semibold text-black"
           style="background: linear-gradient(180deg, #4ade80, #22c55e); box-shadow: 0 0 10px #22c55e;">
            ✕ Close
        </a>
    </div>

    <div class="max-w-6xl mx-auto">

        <h1 class="text-4xl font-semibold mb-6">Fermentation Log Viewer</h1>

        <!-- GITHUB BUTTONS -->
        <div class="flex flex-wrap gap-4 mb-8">

            <button onclick="pullFromGitHub()"
                class="bg-blue-600 hover:bg-blue-500 px-6 py-3 rounded-2xl font-semibold">
                ⬇ Pull Latest from GitHub
            </button>

            <button onclick="pushToGitHub()"
                class="bg-green-600 hover:bg-green-500 px-6 py-3 rounded-2xl font-semibold">
                ⬆ Upload CSV to GitHub
            </button>

            <a href="/download_csv"
                class="bg-amber-400 hover:bg-amber-300 text-black px-6 py-3 rounded-2xl font-semibold text-center">
                ⬇ Download CSV
            </a>

        </div>

        <!-- STATUS -->
        <div id="ghStatus" class="text-sm text-zinc-400 mb-6"></div>

        <!-- FILTER BUTTON ROW -->
        <div class="flex flex-wrap gap-3 mb-4">

            <button class="filter-btn bg-zinc-700 hover:bg-zinc-600 px-4 py-2 rounded-lg"
                    onclick="applyFilter('1h')">Last 1h</button>

            <button class="filter-btn bg-zinc-700 hover:bg-zinc-600 px-4 py-2 rounded-lg"
                    onclick="applyFilter('6h')">Last 6h</button>

            <button class="filter-btn bg-amber-500 text-black px-4 py-2 rounded-lg"
                    id="defaultFilter"
                    onclick="applyFilter('12h')">Last 12h</button>

            <button class="filter-btn bg-zinc-700 hover:bg-zinc-600 px-4 py-2 rounded-lg"
                    onclick="applyFilter('24h')">Last 24h</button>

            <button class="filter-btn bg-zinc-700 hover:bg-zinc-600 px-4 py-2 rounded-lg"
                    onclick="applyFilter('48h')">Last 48h</button>

            <button class="filter-btn bg-zinc-700 hover:bg-zinc-600 px-4 py-2 rounded-lg"
                    onclick="applyFilter('all')">Show All</button>

        </div>

        <!-- DATETIME RANGE ROW -->
        <div class="flex flex-wrap gap-4 mb-8">

            <input type="datetime-local" id="startDateTime"
                   class="bg-zinc-800 text-white px-3 py-2 rounded-lg">

            <input type="datetime-local" id="endDateTime"
                   class="bg-zinc-800 text-white px-3 py-2 rounded-lg">

            <button onclick="applyDateRange()"
                    class="bg-amber-500 hover:bg-amber-400 text-black px-4 py-2 rounded-lg">
                Apply
            </button>

        </div>

        <!-- CHARTS -->
        <div class="bg-zinc-900 p-6 rounded-3xl mb-10">
            <h2 class="text-xl font-semibold mb-4">Gravity</h2>
            <canvas id="gravityChart" height="120"></canvas>
        </div>

        <div class="bg-zinc-900 p-6 rounded-3xl mb-10">
            <h2 class="text-xl font-semibold mb-4">Temperature</h2>
            <canvas id="tempChart" height="120"></canvas>
        </div>

        <!-- JSON DEBUG PANEL -->
        <div class="bg-zinc-900 p-6 rounded-3xl">
            <h2 class="text-xl font-semibold mb-4">Raw Log Data</h2>
            <pre id="jsonOutput" class="text-xs text-zinc-300 whitespace-pre-wrap"></pre>
        </div>

    </div>

<script>
let logData = [];
let gravityChart, tempChart;

function setGhStatus(msg, loading=false) {
    const el = document.getElementById('ghStatus');
    el.innerHTML = loading ? "⏳ " + msg : msg;
}

// ------------------------------------------------------------
//  LOAD LOG DATA
// ------------------------------------------------------------
function loadLog() {
    fetch('/get_log')
        .then(r => r.json())
        .then(rows => {
            logData = rows;
            document.getElementById("jsonOutput").textContent = JSON.stringify(rows, null, 2);
            updateCharts(rows);
        });
}

// ------------------------------------------------------------
//  FILTER BUTTON LOGIC
// ------------------------------------------------------------
function applyFilter(hours) {
    document.querySelectorAll(".filter-btn").forEach(btn =>
        btn.classList.remove("bg-amber-500", "text-black")
    );

    event.target.classList.add("bg-amber-500", "text-black");

    if (hours === "all") {
        updateCharts(logData);
        return;
    }

    const cutoff = new Date(Date.now() - (parseInt(hours) * 60 * 60 * 1000));

    const filtered = logData.filter(entry => new Date(entry.timestamp) >= cutoff);

    updateCharts(filtered);
}

// ------------------------------------------------------------
//  DATETIME RANGE FILTER
// ------------------------------------------------------------
function applyDateRange() {
    const start = new Date(document.getElementById("startDateTime").value);
    const end = new Date(document.getElementById("endDateTime").value);

    if (isNaN(start) || isNaN(end)) {
        alert("Please select both start and end date/time.");
        return;
    }

    const filtered = logData.filter(entry => {
        const t = new Date(entry.timestamp);
        return t >= start && t <= end;
    });

    updateCharts(filtered);
}

// ------------------------------------------------------------
//  UPDATE CHARTS
// ------------------------------------------------------------
function updateCharts(rows) {
    const labels = rows.map(r => r.timestamp);
    const gravity = rows.map(r => r.gravity_corrected);
    const temp = rows.map(r => r.temperature);

    if (gravityChart) gravityChart.destroy();
    if (tempChart) tempChart.destroy();

    gravityChart = new Chart(document.getElementById('gravityChart'), {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: "Corrected Gravity",
                data: gravity,
                borderColor: "#fbbf24",
                backgroundColor: "rgba(251,191,36,0.2)",
                tension: 0.3
            }]
        }
    });

    tempChart = new Chart(document.getElementById('tempChart'), {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: "Temperature (°C)",
                data: temp,
                borderColor: "#3b82f6",
                backgroundColor: "rgba(59,130,246,0.2)",
                tension: 0.3
            }]
        }
    });
}

// ------------------------------------------------------------
//  SECURE GITHUB UPLOAD
// ------------------------------------------------------------
function pushToGitHub() {

    if (!confirm("⚠️ WARNING:\\n\\nUploading to GitHub will overwrite the file stored online.\\n\\nIf the GitHub file contains MORE data than your local log, you WILL LOSE DATA.\\n\\nMake sure you have pulled the latest version first.\\n\\nContinue?")) {
        return;
    }

    if (!confirm("FINAL WARNING:\\n\\nAre you absolutely sure you want to upload the CSV to GitHub?\\nThis action cannot be undone.")) {
        return;
    }

    const pw = prompt("Enter upload password to continue:");
    if (!pw) return;

    setGhStatus("Verifying password...", true);

    fetch('/verify_upload_password', {
        method: 'POST',
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: pw })
    })
    .then(r => r.json().then(body => ({ ok: r.ok, body })))
    .then(res => {

        if (!res.ok || !res.body.success) {
            setGhStatus("❌ Incorrect password. Upload cancelled.");
            return;
        }

        setGhStatus("Uploading log to GitHub...", true);

        return fetch('/push_to_github', { method: 'POST' })
            .then(r => r.json())
            .then(result => {
                if (result.success) {
                    setGhStatus("✅ Log uploaded to GitHub.");
                } else {
                    setGhStatus("❌ Upload failed: " + (result.error || "Unknown error"));
                }
            });
    })
    .catch(err => {
        console.error(err);
        setGhStatus("❌ Error verifying password or uploading.");
    });
}

// ------------------------------------------------------------
//  PULL FROM GITHUB
// ------------------------------------------------------------
function pullFromGitHub() {
    setGhStatus("Pulling latest log from GitHub...", true);

    fetch('/pull_from_github', { method: 'POST' })
        .then(r => r.json())
        .then(res => {
            if (res.success) {
                setGhStatus("✅ Pulled latest log from GitHub.");
                loadLog();
            } else {
                setGhStatus("❌ Pull failed: " + (res.error || "Unknown error"));
            }
        })
        .catch(err => {
            console.error(err);
            setGhStatus("❌ Network error during pull.");
        });
}

// Load everything
loadLog();
</script>

</body>
</html>
"""



#Block 5C

# ============================================================
#  BLOCK 5C — VIEW LOG ROUTE (CORRECTED FOR DYNAMIC CSV)
# ============================================================

@app.route("/view_log")
def view_log_page():
    # Get the correct CSV filename for the current brew session
    csv_file = get_current_brew_log_csv_filename()

    # Ensure the folder exists
    os.makedirs(os.path.dirname(csv_file), exist_ok=True)

    # If CSV does not exist yet, create it with headers
    if not os.path.exists(csv_file):
        with open(csv_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp",
                "gravity",
                "gravity_corrected",
                "temperature",
                "abv",
                "battery"
            ])

    # Load CSV data
    data = []
    with open(csv_file, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                data.append({
                    "timestamp": row.get("timestamp", ""),
                    "gravity": float(row.get("gravity", 0)),
                    "gravity_corrected": float(row.get("gravity_corrected", 0)),
                    "temperature": float(row.get("temperature", 0)),
                    "abv": float(row.get("abv", 0)),
                    "battery": float(row.get("battery", 0))
                })
            except:
                continue

    # Render the advanced viewer template
    return render_template_string(LOG_VIEWER_TEMPLATE, log_json=json.dumps(data))


