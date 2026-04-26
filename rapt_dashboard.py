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

# ============================================================
# SAFE GLOBAL STATE
# ============================================================
latest_data = {}
last_received_time = None

csv_lock = threading.Lock()
state_lock = threading.Lock()

CONFIG_FILE = "config.json"

GITHUB_OWNER = "DaveFL22"
GITHUB_REPO = "rapt-pill-dashboard"
GITHUB_BRANCH = "main"
GITHUB_LOG_FOLDER = "Recipe_Brew_Logs"


# ============================================================
# CONFIG
# ============================================================
def load_config():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass

    return {
        "profile_name": "Unknown Beer",
        "original_gravity": 1.050,
        "session_start": datetime.now(uk).isoformat(timespec="minutes"),
        "calibration_offset": 0.0,
    }


def get_config():
    return load_config()


# ============================================================
# CORE CALCS (UNCHANGED)
# ============================================================
def sg_to_plato(sg):
    return -616.868 + 1111.14 * sg - 630.272 * (sg ** 2) + 135.997 * (sg ** 3)


def plato_to_sg(plato):
    return 1 + (plato / (258.6 - ((plato / 258.2) * 227.1)))


def corrected_gravity(raw_sg, temp_c, offset):
    plato = sg_to_plato(raw_sg)
    plato_corr = plato + (0.00023 * (temp_c - 20))
    sg_corr = plato_to_sg(plato_corr)
    return sg_corr + offset


def calc_abv(og, fg):
    return (76.08 * (og - fg) / (1.775 - og)) * (fg / 0.794)


# ============================================================
# SAFE SESSION TIME
# ============================================================
def safe_session_length(start_iso):
    try:
        dt = datetime.fromisoformat(start_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=uk)

        delta = datetime.now(uk) - dt
        return f"{delta.days} days {delta.seconds // 3600} hours"
    except Exception:
        return "--"


# ============================================================
# FILE HANDLING (SAFE)
# ============================================================
def get_current_brew_log_base():
    cfg = get_config()

    profile = cfg.get("profile_name", "Unknown")
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
# CSV SAFETY
# ============================================================
def append_log_entry(ts, sg, temp):
    filename = get_current_brew_log_csv_filename()
    os.makedirs(os.path.dirname(filename), exist_ok=True)

    with csv_lock:
        file_exists = os.path.exists(filename)

        with open(filename, "a", newline="") as f:
            writer = csv.writer(f)

            if not file_exists:
                writer.writerow(["timestamp", "gravity", "temperature"])

            writer.writerow([
                ts.astimezone(uk).isoformat(),
                sg,
                temp
            ])


def read_last_row():
    filename = get_current_brew_log_csv_filename()

    try:
        with csv_lock:
            if not os.path.exists(filename):
                return None

            with open(filename, "r") as f:
                rows = list(csv.DictReader(f))
                return rows[-1] if rows else None
    except Exception:
        return None


# ============================================================
# ROUTES (UNCHANGED BEHAVIOUR)
# ============================================================
@app.route("/")
def dashboard():
    cfg = get_config()

    return render_template_string(
        HTML_TEMPLATE,   # 👈 YOUR ORIGINAL HTML IS PRESERVED OUTSIDE THIS RESPONSE
        profile_name=cfg["profile_name"],
        original_gravity=cfg["original_gravity"],
        today=date.today().isoformat(),
        now=datetime.now().strftime("%H:%M"),
    )


@app.route("/latest")
def latest():
    cfg = get_config()

    if not latest_data:
        last = read_last_row()

        if last:
            try:
                sg = float(last["gravity"])
                temp = float(last["temperature"])

                sg_corr = corrected_gravity(sg, temp, cfg["calibration_offset"])
                abv = calc_abv(cfg["original_gravity"], sg_corr)

                return jsonify({
                    "data": {
                        "temperature": temp,
                        "gravity": sg,
                        "gravity_corrected": round(sg_corr, 4),
                        "abv": round(abv, 3),
                        "battery": "--",
                        "session_length": safe_session_length(cfg["session_start"]),
                    },
                    "timestamp": last["timestamp"]
                })
            except Exception:
                pass

        return jsonify({"data": {}, "timestamp": "Never"})

    data = latest_data.copy()

    try:
        sg = float(data.get("gravity") or 0)
        temp = float(data.get("temperature") or 20)

        sg_corr = corrected_gravity(sg, temp, cfg["calibration_offset"])
        data["gravity_corrected"] = round(sg_corr, 4)
        data["abv"] = round(calc_abv(cfg["original_gravity"], sg_corr), 3)
        data["session_length"] = safe_session_length(cfg["session_start"])
    except Exception:
        pass

    ts = (
        last_received_time.astimezone(uk).strftime("%H:%M:%S • %d %b")
        if last_received_time else "Never"
    )

    return jsonify({"data": data, "timestamp": ts})


@app.route("/webhook", methods=["POST"])
def webhook():
    global latest_data, last_received_time

    try:
        data = request.get_json() if request.is_json else request.form.to_dict()

        if "gravity" not in data or "temperature" not in data:
            return jsonify({"success": False}), 400

        with state_lock:
            last_received_time = datetime.now(uk)
            latest_data = data

        append_log_entry(
            last_received_time,
            float(data["gravity"]),
            float(data["temperature"])
        )

        return jsonify({"success": True})

    except Exception:
        return jsonify({"success": False}), 400


# ============================================================
# ENTRYPOINT
# ============================================================
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
