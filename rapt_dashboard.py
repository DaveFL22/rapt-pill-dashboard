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
        "calibration_offset": 0.0000
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
    except:
        return "fermentation_logs/Unknown_Brew_unknown_date"

    profile = cfg.get("profile_name", "Unknown_Brew")
    start = cfg.get("session_start", "")

    safe_profile = re.sub(r"[^A-Za-z0-9]+", "_", profile).strip("_")

    try:
        dt = datetime.fromisoformat(start)
        date_str = dt.strftime("%Y-%m-%d")
    except:
        date_str = "unknown_date"

    return f"fermentation_logs/{safe_profile}_{date_str}"


def get_current_brew_log_csv_filename():
    return get_current_brew_log_base() + ".csv"



#Block 2

# ============================================================
#  DASHBOARD HTML TEMPLATE
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
</html>"""



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
        now=now
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
                        "session_length": "--"
                    }

                    return jsonify({"data": fallback, "timestamp": last["timestamp"]})

        return jsonify({"data": {}, "timestamp": "Never"})

    # Normal live-data path
    data_to_send = latest_data.copy()

    try:
        raw_sg = float(data_to_send.get('gravity') or 0)
        temp_c = float(data_to_send.get('temperature') or 20)

        sg_corr = corrected_gravity(raw_sg, temp_c, cfg["calibration_offset"])
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

        latest_data = data

        raw_sg = float(data.get("gravity") or 0)
        temp_c = float(data.get("temperature") or 0)

        # Write to CSV
        append_log_entry(now_uk, raw_sg, temp_c)

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
        "calibration_offset": float(offset) if offset else 0.0000
    }

    with open(CONFIG_FILE, "w") as f:
        json.dump(new_config, f, indent=4)

    return jsonify({"success": True})



#Block 4

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



#Block 5

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
