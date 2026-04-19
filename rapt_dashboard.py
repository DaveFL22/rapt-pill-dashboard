from flask import Flask, jsonify, render_template_string, request
from datetime import datetime
import json

app = Flask(__name__)

latest_data = {}
last_received_time = None

# --- SETTINGS YOU CAN CHANGE ---
ORIGINAL_GRAVITY = 1.0513
CALIBRATION_OFFSET = 0.0000   # <— Adjust here anytime
# --------------------------------

# --- Convert SG → Plato ---
def sg_to_plato(sg):
    return -616.868 + 1111.14*sg - 630.272*(sg**2) + 135.997*(sg**3)

# --- Convert Plato → SG ---
def plato_to_sg(plato):
    return 1 + (plato / (258.6 - ((plato / 258.2) * 227.1)))

# --- Full RAPT gravity correction pipeline ---
def corrected_gravity(raw_sg, temp_c):
    # 1. SG → Plato
    plato = sg_to_plato(raw_sg)

    # 2. Temperature correction (to 20°C reference)
    plato_corr = plato + (0.00023 * (temp_c - 20))

    # 3. Plato → corrected SG
    sg_corr = plato_to_sg(plato_corr)

    # 4. Apply calibration offset
    sg_corr += CALIBRATION_OFFSET

    return sg_corr

# --- Accurate RAPT / Morey ABV formula ---
def calc_abv(og, fg):
    return (76.08 * (og - fg) / (1.775 - og)) * (fg / 0.794)

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>RAPT Pill Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { font-family: 'Inter', system_ui, sans-serif; }
        .card { transition: all 0.3s ease; }
        .card:hover { transform: translateY(-4px); }
    </style>
</head>
<body class="bg-zinc-950 text-white min-h-screen p-6">
    <div class="max-w-4xl mx-auto">
        <h1 class="text-4xl font-semibold mb-2">RAPT Pill Dashboard</h1>
        <p class="text-zinc-400 mb-6">Live Fermentation Monitor • OG: 1.0513</p>

        <div id="status" class="mb-8 p-5 rounded-3xl bg-zinc-900 text-lg font-medium">Waiting for data...</div>

        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
            <div class="card bg-zinc-900 rounded-3xl p-8">
                <p class="text-zinc-400 text-sm">TEMPERATURE</p>
                <p id="temp" class="text-6xl font-semibold mt-4">–.– °C</p>
            </div>
            <div class="card bg-zinc-900 rounded-3xl p-8">
                <p class="text-zinc-400 text-sm">SPECIFIC GRAVITY</p>
                <p id="gravity" class="text-6xl font-semibold mt-4">1.–––</p>
            </div>
            <div class="card bg-zinc-900 rounded-3xl p-8">
                <p class="text-zinc-400 text-sm">ESTIMATED ABV</p>
                <p id="abv" class="text-6xl font-semibold mt-4">–.– %</p>
            </div>
            <div class="card bg-zinc-900 rounded-3xl p-8">
                <p class="text-zinc-400 text-sm">BATTERY</p>
                <p id="battery" class="text-6xl font-semibold mt-4">–– %</p>
            </div>
        </div>

        <div class="mt-8">
            <button onclick="refreshData()" 
                    class="w-full bg-white text-black hover:bg-amber-400 font-semibold py-4 rounded-3xl text-lg">
                ↻ REFRESH NOW
            </button>
        </div>

        <div class="mt-10">
            <p class="text-zinc-400 mb-2 text-sm">RAW DATA (debug):</p>
            <pre id="raw" class="bg-zinc-900 p-6 rounded-3xl text-xs font-mono overflow-auto max-h-96"></pre>
        </div>
    </div>

    <script>
        function refreshData() {
            const status = document.getElementById('status');
            status.innerHTML = '🔄 Loading...';

            fetch('/latest')
                .then(r => r.json())
                .then(result => {
                    const d = result.data || {};

                    // INLINE UNITS
                    document.getElementById('temp').textContent =
                        `${d.temperature || d.temp || '--'} °C`;

                    document.getElementById('gravity').textContent =
                        `${parseFloat(d.gravity_corrected || 0).toFixed(4)}`;

                    document.getElementById('abv').textContent =
                        `${d.abv || '--'} %`;

                    document.getElementById('battery').textContent =
                        `${Math.round(d.battery || d.batteryLevel || 0)} %`;

                    document.getElementById('raw').textContent =
                        JSON.stringify(d, null, 2);

                    status.innerHTML = `✅ Last updated: ${new Date().toLocaleTimeString()}`;
                })
                .catch(err => {
                    console.error(err);
                    status.innerHTML = '❌ Error loading data - check raw data below';
                });
        }

        window.onload = refreshData;
        setInterval(refreshData, 30000);
    </script>
</body>
</html>"""

@app.route("/")
def dashboard():
    return render_template_string(HTML_TEMPLATE)

@app.route("/latest")
def get_latest():
    data_to_send = latest_data.copy()

    if data_to_send:
        try:
            raw_sg = float(data_to_send.get('gravity') or 0)
            temp_c = float(data_to_send.get('temperature') or 20)

            # Apply full RAPT correction
            sg_corr = corrected_gravity(raw_sg, temp_c)
            data_to_send['gravity_corrected'] = round(sg_corr, 4)

            # ABV using corrected SG
            abv = calc_abv(ORIGINAL_GRAVITY, sg_corr)
            data_to_send['abv'] = round(abv, 3)

        except Exception as e:
            print("Error in correction:", e)

    ts = last_received_time.strftime("%H:%M:%S • %d %b") if last_received_time else "Never"
    return jsonify({"data": data_to_send, "timestamp": ts})

@app.route("/webhook", methods=["POST"])
def webhook():
    global latest_data, last_received_time
    try:
        data = request.get_json() if request.is_json else request.form.to_dict()
        last_received_time = datetime.now()
        
        print("✅ WEBHOOK RECEIVED at", last_received_time.strftime("%H:%M:%S"))
        print(json.dumps(data, indent=2))
        
        latest_data = data
        return jsonify({"success": True}), 200
    except Exception as e:
        print("Webhook error:", str(e))
        return jsonify({"success": False, "error": str(e)}), 400

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
