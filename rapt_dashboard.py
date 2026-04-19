from flask import Flask, jsonify, render_template_string, request
from datetime import datetime, date, timedelta
import json
import os

app = Flask(__name__)

latest_data = {}
last_received_time = None

CONFIG_FILE = "config.json"
FERMENT_LOG = "fermentation_log.json"
BREW_LOG_DIR = "brew_logs"


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
#  FERMENTATION LOGGING
# ============================================================
def ensure_log_file():
    if not os.path.exists(FERMENT_LOG):
        with open(FERMENT_LOG, "w") as f:
            json.dump([], f)


def append_log_entry(timestamp, raw_sg, temp_c):
    ensure_log_file()
    try:
        with open(FERMENT_LOG, "r") as f:
            data = json.load(f)
    except Exception:
        data = []

    data.append({
        "timestamp": timestamp.isoformat(),
        "gravity": raw_sg,
        "temperature": temp_c
    })

    with open(FERMENT_LOG, "w") as f:
        json.dump(data, f, indent=2)


def load_log():
    if not os.path.exists(FERMENT_LOG):
        return []
    try:
        with open(FERMENT_LOG, "r") as f:
            return json.load(f)
    except Exception:
        return []


def archive_current_log():
    if not os.path.exists(FERMENT_LOG):
        return

    try:
        with open(FERMENT_LOG, "r") as f:
            data = json.load(f)
        if not data:
            return
    except Exception:
        return

    cfg = get_config()
    profile = cfg.get("profile_name", "Unknown Brew")
    start = cfg.get("session_start", "2026-01-01T00:00:00")
    start_date = start[:10]

    os.makedirs(BREW_LOG_DIR, exist_ok=True)
    safe_profile = "".join(c for c in profile if c not in r'\/:*?"<>|')
    filename = f"{safe_profile} - {start_date}.json"
    path = os.path.join(BREW_LOG_DIR, filename)

    with open(path, "w") as f:
        json.dump(data, f, indent=2)

    with open(FERMENT_LOG, "w") as f:
        json.dump([], f)


# ============================================================
#  HTML TEMPLATE WITH GRAPH
# ============================================================
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ profile_name }} - RAPT Pill Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>

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
    <div class="max-w-5xl mx-auto flex justify-between items-center mb-6">
        <div>
            <h1 class="text-4xl font-semibold">{{ profile_name }} - RAPT Pill Dashboard</h1>
            <p class="text-zinc-400">Live Fermentation Monitor • OG: {{ original_gravity }}</p>
        </div>

        <button onclick="openModal()"
            class="bg-amber-400 hover:bg-amber-300 text-black font-semibold px-6 py-3 rounded-2xl">
            + Start New Brew & Fermentation Profile
        </button>
    </div>

    <!-- STATUS -->
    <div id="status" class="max-w-5xl mx-auto mb-8 p-5 rounded-3xl bg-zinc-900 text-lg font-medium">
        Waiting for data...
    </div>

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

    <!-- GRAPH SECTION -->
    <div class="max-w-5xl mx-auto mt-10 bg-zinc-900 p-6 rounded-3xl">
        <div class="flex items-center justify-between mb-4">
            <h2 class="text-xl font-semibold">Fermentation Graph</h2>
            <div class="space-x-2">
                <button id="btnFull" class="px-4 py-2 rounded-xl bg-amber-400 text-black text-sm font-semibold">
                    Full Brew
                </button>
                <button id="btn24h" class="px-4 py-2 rounded-xl bg-zinc-700 text-white text-sm font-semibold">
                    Last 24 Hours
                </button>
            </div>
        </div>
        <canvas id="fermentChart" height="120"></canvas>
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
let chart;
let chartMode = 'full'; // 'full' or '24h'

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
        loadGraphData()
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

            status.innerHTML = `✅ Last updated: ${new Date().toLocaleTimeString()}`
        })
        .catch(err => {
            console.error(err)
            status.innerHTML = '❌ Error loading data'
        })
}

function setupChart() {
    const ctx = document.getElementById('fermentChart').getContext('2d')
    chart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'SG (corrected)',
                    data: [],
                    borderColor: '#38bdf8',
                    backgroundColor: 'rgba(56,189,248,0.15)',
                    tension: 0.3,
                    yAxisID: 'y'
                },
                {
                    label: 'Temperature (°C)',
                    data: [],
                    borderColor: '#f97316',
                    backgroundColor: 'rgba(249,115,22,0.15)',
                    tension: 0.3,
                    yAxisID: 'y1'
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: {
                    ticks: { color: '#a1a1aa' }
                },
                y: {
                    position: 'left',
                    ticks: { color: '#38bdf8' }
                },
                y1: {
                    position: 'right',
                    grid: { drawOnChartArea: false },
                    ticks: { color: '#f97316' }
                }
            },
            plugins: {
                legend: {
                    labels: { color: '#e5e5e5' }
                }
            }
        }
    })
}

function loadGraphData() {
    fetch('/graph_data')
        .then(r => r.json())
        .then(result => {
            const data = chartMode === 'full' ? result.full : result.last24

            chart.data.labels = data.timestamps
            chart.data.datasets[0].data = data.sg
            chart.data.datasets[1].data = data.temp
            chart.update()
        })
        .catch(err => console.error(err))
}

document.getElementById('btnFull').onclick = function() {
    chartMode = 'full'
    document.getElementById('btnFull').classList.add('bg-amber-400', 'text-black')
    document.getElementById('btnFull').classList.remove('bg-zinc-700', 'text-white')
    document.getElementById('btn24h').classList.add('bg-zinc-700', 'text-white')
    document.getElementById('btn24h').classList.remove('bg-amber-400', 'text-black')
    loadGraphData()
}

document.getElementById('btn24h').onclick = function() {
    chartMode = '24h'
    document.getElementById('btn24h').classList.add('bg-amber-400', 'text-black')
    document.getElementById('btn24h').classList.remove('bg-zinc-700', 'text-white')
    document.getElementById('btnFull').classList.add('bg-zinc-700', 'text-white')
    document.getElementById('btnFull').classList.remove('bg-amber-400', 'text-black')
    loadGraphData()
}

window.onload = function() {
    setupChart()
    refreshData()
    loadGraphData()
}

setInterval(function() {
    refreshData()
    loadGraphData()
}, 30000)
</script>

</body>
</html>"""


# ============================================================
#  ROUTES
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


@app.route("/latest")
def get_latest():
    cfg = get_config()
    data_to_send = latest_data.copy()

    if data_to_send:
        try:
            raw_sg = float(data_to_send.get('gravity') or 0)
            temp_c = float(data_to_send.get('temperature') or 20)

            sg_corr = corrected_gravity(raw_sg, temp_c, cfg["calibration_offset"])
            data_to_send['gravity_corrected'] = round(sg_corr, 4)

            abv = calc_abv(cfg["original_gravity"], sg_corr)
            data_to_send['abv'] = round(abv, 3)

            try:
                session_start = datetime.fromisoformat(cfg["session_start"])
                delta = datetime.now() - session_start
                days = delta.days
                hours = delta.seconds // 3600
                data_to_send['session_length'] = f"{days} days {hours} hours"
            except Exception:
                data_to_send['session_length'] = "--"

        except Exception as e:
            print("Error:", e)

    ts = last_received_time.strftime("%H:%M:%S • %d %b") if last_received_time else "Never"
    return jsonify({"data": data_to_send, "timestamp": ts})


@app.route("/webhook", methods=["POST"])
def webhook():
    global latest_data, last_received_time
    try:
        data = request.get_json() if request.is_json else request.form.to_dict()
        last_received_time = datetime.now()

        data.pop("session_start", None)

        latest_data = data

        # Log entry
        try:
            raw_sg = float(data.get('gravity') or 0)
            temp_c = float(data.get('temperature') or 0)
            append_log_entry(last_received_time, raw_sg, temp_c)
        except Exception:
            pass

        return jsonify({"success": True}), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/start_brew", methods=["POST"])
def start_brew():
    profile_name = request.form.get("profile_name", "").strip()
    og = request.form.get("original_gravity", "").strip()
    start_date = request.form.get("start_date", "")
    start_time = request.form.get("start_time", "")
    offset = request.form.get("calibration_offset", "").strip()

    session_start = f"{start_date}T{start_time}"

    # Archive current log before starting new brew
    archive_current_log()

    new_config = {
        "profile_name": profile_name or "Unnamed Brew",
        "original_gravity": float(og) if og else 1.050,
        "session_start": session_start,
        "calibration_offset": float(offset) if offset else 0.0000
    }

    with open(CONFIG_FILE, "w") as f:
        json.dump(new_config, f, indent=4)

    return jsonify({"success": True})


@app.route("/graph_data")
def graph_data():
    cfg = get_config()
    log = load_log()
    if not log:
        return jsonify({"full": {"timestamps": [], "sg": [], "temp": []},
                        "last24": {"timestamps": [], "sg": [], "temp": []}})

    now = datetime.now()
    cutoff_24h = now - timedelta(hours=24)

    full_ts, full_sg, full_temp = [], [], []
    last_ts, last_sg, last_temp = [], [], []

    for entry in log:
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
            raw_sg = float(entry.get("gravity") or 0)
            temp_c = float(entry.get("temperature") or 0)
            sg_corr = corrected_gravity(raw_sg, temp_c, cfg["calibration_offset"])
        except Exception:
            continue

        label = ts.strftime("%d %b %H:%M")

        full_ts.append(label)
        full_sg.append(round(sg_corr, 4))
        full_temp.append(round(temp_c, 2))

        if ts >= cutoff_24h:
            last_ts.append(label)
            last_sg.append(round(sg_corr, 4))
            last_temp.append(round(temp_c, 2))

    return jsonify({
        "full": {
            "timestamps": full_ts,
            "sg": full_sg,
            "temp": full_temp
        },
        "last24": {
            "timestamps": last_ts,
            "sg": last_sg,
            "temp": last_temp
        }
    })


# ============================================================
#  START SERVER
# ============================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
