from flask import Flask, jsonify, render_template_string, request, send_file, Response
from datetime import datetime, date
import json
import os
import csv

app = Flask(__name__)

latest_data = {}
last_received_time = None

CONFIG_FILE = "config.json"
FERMENT_LOG = "fermentation_log.json"


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
#  BASIC FERMENTATION LOGGING
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


# ============================================================
#  AUTO‑ARCHIVE LOGS PER BREW
# ============================================================
def archive_current_log(profile_name):
    ensure_log_file()
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    safe_name = profile_name.replace(" ", "_")
    archive_dir = "brew_logs"

    if not os.path.exists(archive_dir):
        os.makedirs(archive_dir)

    archive_path = os.path.join(archive_dir, f"{safe_name}_{timestamp}.json")
    os.rename(FERMENT_LOG, archive_path)

# ============================================================
#  DASHBOARD HTML TEMPLATE (WITH VIEW LOG BUTTON)
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
    <div class="max-w-5xl mx-auto flex justify-between items-center mb-6">
        <div>
            <h1 class="text-4xl font-semibold">{{ profile_name }} - RAPT Pill Dashboard</h1>
            <p class="text-zinc-400">Live Fermentation Monitor • OG: {{ original_gravity }}</p>
        </div>

        <div class="flex gap-4">
          
           <a href="/view_log" target="_blank" rel="noopener"
              class="flex items-center gap-2 bg-zinc-800 hover:bg-zinc-700 text-white text-xs px-4 py-2 rounded-2xl">
              📄 <span>View Log</span>
           </a>




            <button onclick="openModal()"
                class="bg-amber-400 hover:bg-amber-300 text-black font-semibold px-6 py-3 rounded-2xl">
                + Start New Brew
            </button>
        </div>
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
</html>"""


# ============================================================
#  DASHBOARD ROUTES
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

        # BASIC LOGGING
        try:
            raw_sg = float(data.get("gravity") or 0)
            temp_c = float(data.get("temperature") or 0)
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

    # AUTO‑ARCHIVE CURRENT LOG
    archive_current_log(profile_name)

    # RESET LOG
    ensure_log_file()

    session_start = f"{start_date}T{start_time}"

    new_config = {
        "profile_name": profile_name or "Unnamed Brew",
        "original_gravity": float(og) if og else 1.050,
        "session_start": session_start,
        "calibration_offset": float(offset) if offset else 0.0000
    }

    with open(CONFIG_FILE, "w") as f:
        json.dump(new_config, f, indent=4)

    return jsonify({"success": True})

# ============================================================
#  DOWNLOAD LOG (JSON)
# ============================================================
@app.route("/download_log")
def download_log():
    ensure_log_file()
    return send_file(
        FERMENT_LOG,
        as_attachment=True,
        download_name="fermentation_log.json",
        mimetype="application/json"
    )


# ============================================================
#  CSV EXPORT
# ============================================================
@app.route("/download_csv")
def download_csv():
    ensure_log_file()
    with open(FERMENT_LOG, "r") as f:
        data = json.load(f)

    def generate():
        yield "timestamp,gravity,temperature\n"
        for entry in data:
            yield f"{entry['timestamp']},{entry['gravity']},{entry['temperature']}\n"

    return Response(generate(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=fermentation_log.csv"})


# ============================================================
#  VIEW LOG (WITH TINY GRAPH)
# ============================================================
@app.route("/view_log")
def view_log():
    ensure_log_file()
    try:
        with open(FERMENT_LOG, "r") as f:
            data = json.load(f)
    except Exception:
        data = []

    pretty = json.dumps(data, indent=2)

    timestamps = [d["timestamp"] for d in data]
    gravities = [d["gravity"] for d in data]
    temps = [d["temperature"] for d in data]

    return f"""
    <html>
    <head>
        <title>Fermentation Log Viewer</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            body {{
                background: #0a0a0a;
                color: #e5e5e5;
                font-family: monospace;
                padding: 20px;
            }}
            pre {{
                background: #111;
                padding: 20px;
                border-radius: 12px;
                white-space: pre-wrap;
                overflow-x: auto;
            }}
            a {{
                color: #fbbf24;
                font-size: 18px;
                text-decoration: none;
            }}
            #chart-container {{
                background: #111;
                padding: 20px;
                border-radius: 12px;
                margin-bottom: 20px;
            }}
        </style>
    </head>
    <body>
        <h1>Fermentation Log Viewer</h1>
        <p>
            <a href="/download_log">⬇ Download JSON</a> |
            <a href="/download_csv">⬇ Download CSV</a>
        </p>

        <div id="chart-container">
            <canvas id="logChart"></canvas>
        </div>

        <pre>{pretty}</pre>

        <script>
            const ctx = document.getElementById('logChart').getContext('2d');
            const chart = new Chart(ctx, {{
                type: 'line',
                data: {{
                    labels: {timestamps},
                    datasets: [
                        {{
                            label: 'Gravity',
                            data: {gravities},
                            borderColor: '#22c55e',
                            tension: 0.2
                        }},
                        {{
                            label: 'Temperature (°C)',
                            data: {temps},
                            borderColor: '#fbbf24',
                            tension: 0.2
                        }}
                    ]
                }},
                options: {{
                    scales: {{
                        x: {{ ticks: {{ color: '#aaa' }} }},
                        y: {{ ticks: {{ color: '#aaa' }} }}
                    }},
                    plugins: {{
                        legend: {{ labels: {{ color: '#fff' }} }}
                    }}
                }}
            }});
        </script>
    </body>
    </html>
    """
