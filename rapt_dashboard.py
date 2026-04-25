# ============================================================
#  RAPT PILL DASHBOARD - FULL APP
#  - Flask app
#  - Single CSV log file
#  - GitHub push/pull via API
#  - Temperature offset support
#  - Tailwind UI dashboard
# ============================================================

import os
import csv
import base64
import json
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string

# ============================================================
#  CONFIG
# ============================================================

app = Flask(__name__)

# Fixed CSV filename you specified
LOG_FILE = "Madreezy_Spanish_Lager_2026-04-12.csv"

# Simple metadata for the brew (you can adjust)
PROFILE_NAME = "Madreezy Spanish Lager"
ORIGINAL_GRAVITY = "1.050"

# Temperature offset storage file (local)
TEMP_OFFSET_FILE = "temperature_offset.txt"

# GitHub settings (using your existing setup)
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_OWNER = "DaveFL22"
GITHUB_REPO = "rapt-pill-dashboard"
GITHUB_BRANCH = "main"
GITHUB_FILE_PATH = LOG_FILE  # stored at repo root with this name


# ============================================================
#  HELPER FUNCTIONS
# ============================================================

def ensure_log_exists():
    """Ensure the CSV log file exists with a header."""
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp",
                "temperature",
                "gravity",
                "gravity_corrected",
                "abv",
                "battery",
                "session_length"
            ])


def read_last_row():
    """Read the last data row from the CSV log."""
    if not os.path.exists(LOG_FILE):
        return None

    last = None
    with open(LOG_FILE, "r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            last = row
    return last


def get_temperature_offset():
    """Read the stored temperature offset from file."""
    if not os.path.exists(TEMP_OFFSET_FILE):
        return 0.0
    try:
        with open(TEMP_OFFSET_FILE, "r") as f:
            return float(f.read().strip() or "0")
    except Exception:
        return 0.0


def set_temperature_offset(value: float):
    """Write the temperature offset to file."""
    with open(TEMP_OFFSET_FILE, "w") as f:
        f.write(str(value))


def github_api_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }


def github_file_url():
    return f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"


def github_get_file():
    """Get file content + sha from GitHub (if exists)."""
    import requests
    url = github_file_url()
    resp = requests.get(url, headers=github_api_headers())
    if resp.status_code == 200:
        data = resp.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        sha = data["sha"]
        return content, sha
    elif resp.status_code == 404:
        return None, None
    else:
        raise RuntimeError(f"GitHub GET failed: {resp.status_code} {resp.text}")


def github_put_file(content: str, message: str):
    """Create or update file on GitHub."""
    import requests
    existing_content, sha = github_get_file()
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
        "branch": GITHUB_BRANCH
    }
    if sha:
        payload["sha"] = sha

    url = github_file_url()
    resp = requests.put(url, headers=github_api_headers(), data=json.dumps(payload))
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"GitHub PUT failed: {resp.status_code} {resp.text}")


# ============================================================
#  HTML TEMPLATE (BLOCK 2 + BLOCK 3 MERGED)
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

    <!-- JAVASCRIPT (BLOCK 3 INSERTED BELOW) -->
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
    }).then(function() {
        closeModal()
        refreshData()
    })
}

function setGhStatus(msg, ok) {
    if (ok === undefined) {
        ok = true
    }
    var el = document.getElementById('ghStatus')
    el.textContent = msg
    el.style.color = ok ? '#4ade80' : '#f97373'
    if (msg) {
        setTimeout(function() { el.textContent = ''; }, 6000)
    }
}

function pushToGitHub() {
    setGhStatus('Uploading log to GitHub...', true)
    fetch('/push_to_github', { method: 'POST' })
        .then(function(r) { return r.json(); })
        .then(function(res) {
            if (res.success) {
                setGhStatus('Log uploaded to GitHub.', true)
            } else {
                setGhStatus('Upload failed: ' + (res.error || 'Unknown error'), false)
            }
        })
        .catch(function(err) {
            console.error(err)
            setGhStatus('Upload failed: network error', false)
        })
}

function pullFromGitHub() {
    setGhStatus('Pulling log from GitHub...', true)
    fetch('/pull_from_github', { method: 'POST' })
        .then(function(r) { return r.json(); })
        .then(function(res) {
            if (res.success) {
                setGhStatus('Log pulled from GitHub.', true)
                refreshData()
            } else {
                setGhStatus('Pull failed: ' + (res.error || 'Unknown error'), false)
            }
        })
        .catch(function(err) {
            console.error(err)
            setGhStatus('Pull failed: network error', false)
        })
}

var tempOffset = 0.0

function loadTemperatureOffset() {
    fetch('/get_temperature_offset')
        .then(function(r) { return r.json(); })
        .then(function(res) {
            tempOffset = parseFloat(res.temperature_offset || 0)
            document.getElementById('tempOffsetDisplay').textContent =
                tempOffset.toFixed(1)
        })
        .catch(function() {
            tempOffset = 0.0
        })
}

function adjustOffset(delta) {
    tempOffset = parseFloat(tempOffset) + delta
    document.getElementById('tempOffsetDisplay').textContent =
        tempOffset.toFixed(1)
}

function saveTemperatureOffset() {
    var fd = new FormData()
    fd.append("temperature_offset", tempOffset)

    fetch('/set_temperature_offset', {
        method: 'POST',
        body: fd
    })
    .then(function(r) { return r.json(); })
    .then(function(res) {
        if (res.success) {
            setGhStatus('Temperature offset saved.', true)
            refreshData()
        } else {
            setGhStatus('Failed to save offset.', false)
        }
    })
    .catch(function() {
        setGhStatus('Network error saving offset.', false)
    })
}

function refreshData() {
    var status = document.getElementById('status')
    status.innerHTML = 'Loading...'

    fetch('/latest')
        .then(function(r) { return r.json(); })
        .then(function(result) {
            var d = result.data || {}

            document.getElementById('temp').innerHTML =
                (d.temperature || '--') + '<span class="unit">°C</span>'

            if (d.gravity_corrected) {
                document.getElementById('gravity').textContent =
                    parseFloat(d.gravity_corrected).toFixed(4)
            } else {
                document.getElementById('gravity').textContent = '1.----'
            }

            document.getElementById('abv').innerHTML =
                (d.abv || '--') + '<span class="unit">%</span>'

            document.getElementById('battery').innerHTML =
                Math.round(d.battery || 0) + '<span class="unit">%</span>'

            document.getElementById('session').textContent =
                d.session_length || '--'

            document.getElementById('raw').textContent =
                JSON.stringify(d, null, 2)

            status.innerHTML = 'Last updated: ' + (result.timestamp || 'Unknown')
        })
        .catch(function(err) {
            console.error(err)
            status.innerHTML = 'Error loading data'
        })
}

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
"""


# ============================================================
#  ROUTES
# ============================================================

@app.route("/")
def index():
    ensure_log_exists()
    return render_template_string(
        HTML_TEMPLATE,
        profile_name=PROFILE_NAME,
        original_gravity=ORIGINAL_GRAVITY
    )


@app.route("/view_log")
def view_log():
    ensure_log_exists()
    with open(LOG_FILE, "r") as f:
        content = f.read()
    return "<pre>" + content.replace("<", "&lt;") + "</pre>"


@app.route("/latest")
def latest():
    ensure_log_exists()
    row = read_last_row()
    offset = get_temperature_offset()

    if row is None:
        data = {
            "temperature": None,
            "gravity": None,
            "gravity_corrected": None,
            "abv": None,
            "battery": None,
            "session_length": None
        }
        ts = None
    else:
        try:
            temp_raw = float(row.get("temperature") or 0.0)
            temp_corrected = temp_raw + offset
        except Exception:
            temp_corrected = None

        data = {
            "temperature": round(temp_corrected, 1) if temp_corrected is not None else None,
            "gravity": row.get("gravity"),
            "gravity_corrected": row.get("gravity_corrected"),
            "abv": row.get("abv"),
            "battery": row.get("battery"),
            "session_length": row.get("session_length")
        }
        ts = row.get("timestamp")

    return jsonify({
        "data": data,
        "timestamp": ts
    })


@app.route("/start_brew", methods=["POST"])
def start_brew():
    # For now, just ensure log exists and reset nothing.
    # You can extend this to create a new file per brew if desired.
    ensure_log_exists()
    return ("", 204)


@app.route("/get_temperature_offset")
def route_get_temperature_offset():
    offset = get_temperature_offset()
    return jsonify({"temperature_offset": offset})


@app.route("/set_temperature_offset", methods=["POST"])
def route_set_temperature_offset():
    try:
        val = float(request.form.get("temperature_offset", "0"))
        set_temperature_offset(val)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/push_to_github", methods=["POST"])
def push_to_github():
    if not GITHUB_TOKEN:
        return jsonify({"success": False, "error": "GITHUB_TOKEN not set"})

    ensure_log_exists()
    try:
        with open(LOG_FILE, "r") as f:
            content = f.read()
        msg = f"Update log {LOG_FILE} at {datetime.utcnow().isoformat()}Z"
        github_put_file(content, msg)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/pull_from_github", methods=["POST"])
def pull_from_github():
    if not GITHUB_TOKEN:
        return jsonify({"success": False, "error": "GITHUB_TOKEN not set"})

    try:
        content, sha = github_get_file()
        if content is None:
            return jsonify({"success": False, "error": "File not found on GitHub"}), 404

        with open(LOG_FILE, "w") as f:
            f.write(content)

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
#  ENTRY POINT
# ============================================================

if __name__ == "__main__":
    # For local testing; Render will use gunicorn.
    app.run(host="0.0.0.0", port=5000, debug=True)
