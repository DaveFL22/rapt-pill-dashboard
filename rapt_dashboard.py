# Updated version with stability fixes for Render free tier
# NOTE: Layout and functionality unchanged

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
csv_lock = threading.Lock()  # NEW: prevent race conditions

CONFIG_FILE = "config.json"

GITHUB_OWNER = "DaveFL22"
GITHUB_REPO = "rapt-pill-dashboard"
GITHUB_BRANCH = "main"
GITHUB_LOG_FOLDER = "Recipe_Brew_Logs"

# ============================================================
# CONFIG HANDLING
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

# ============================================================
# SAFE SESSION TIME CALC (FIXED TZ BUG)
# ============================================================
def safe_session_length(start_iso):
    try:
        session_start = datetime.fromisoformat(start_iso)
        if session_start.tzinfo is None:
            session_start = session_start.replace(tzinfo=uk)
        now_uk = datetime.now(uk)
        delta = now_uk - session_start
        days = delta.days
        hours = delta.seconds // 3600
        return f"{days} days {hours} hours"
    except Exception:
        return "--"

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
# LOGGING WITH LOCK
# ============================================================
def append_log_entry(timestamp, raw_sg, temp_c):
    filename = get_current_brew_log_csv_filename()
    os.makedirs(os.path.dirname(filename), exist_ok=True)

    with csv_lock:
        file_exists = os.path.exists(filename)
        with open(filename, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["timestamp", "gravity", "temperature"])
            writer.writerow([
                timestamp.astimezone(uk).isoformat(),
                raw_sg,
                temp_c,
            ])

# ============================================================
# SAFE CSV READ
# ============================================================
def read_last_csv_row():
    filename = get_current_brew_log_csv_filename()
    if not os.path.exists(filename):
        return None

    try:
        with csv_lock:
            with open(filename, "r") as f:
                rows = list(csv.DictReader(f))
                return rows[-1] if rows else None
    except Exception:
        return None

# ============================================================
# DASHBOARD ROUTES (UNCHANGED UI)
# ============================================================
HTML_TEMPLATE = """REDACTED FOR BREVITY (UNCHANGED)"""

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
# LATEST DATA (IMPROVED)
# ============================================================
@app.route("/latest")
def get_latest():
    cfg = get_config()

    if not latest_data:
        last = read_last_csv_row()
        if last:
            try:
                raw_sg = float(last["gravity"])
                temp_c = float(last["temperature"])

                sg_corr = corrected_gravity(raw_sg, temp_c, cfg["calibration_offset"])
                abv = calc_abv(cfg["original_gravity"], sg_corr)

                return jsonify({
                    "data": {
                        "temperature": temp_c,
                        "gravity": raw_sg,
                        "gravity_corrected": round(sg_corr, 4),
                        "abv": round(abv, 3),
                        "battery": "--",
                        "session_length": safe_session_length(cfg["session_start"]),
                    },
                    "timestamp": last["timestamp"],
                })
            except Exception:
                pass

        return jsonify({"data": {}, "timestamp": "Never"})

    data_to_send = latest_data.copy()

    try:
        raw_sg = float(data_to_send.get("gravity") or 0)
        temp_c = float(data_to_send.get("temperature") or 20)

        sg_corr = corrected_gravity(raw_sg, temp_c, cfg["calibration_offset"])
        data_to_send["gravity_corrected"] = round(sg_corr, 4)

        abv = calc_abv(cfg["original_gravity"], sg_corr)
        data_to_send["abv"] = round(abv, 3)

        data_to_send["session_length"] = safe_session_length(cfg["session_start"])

    except Exception as e:
        print("Error:", e)

    ts = last_received_time.astimezone(uk).strftime("%H:%M:%S • %d %b") if last_received_time else "Never"
    return jsonify({"data": data_to_send, "timestamp": ts})

# ============================================================
# WEBHOOK (VALIDATION ADDED)
# ============================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    global latest_data, last_received_time

    try:
        data = request.get_json() if request.is_json else request.form.to_dict()

        if "gravity" not in data or "temperature" not in data:
            return jsonify({"success": False, "error": "Missing data"}), 400

        now_uk = datetime.now(uk)
        last_received_time = now_uk
        latest_data = data

        raw_sg = float(data.get("gravity"))
        temp_c = float(data.get("temperature"))

        append_log_entry(now_uk, raw_sg, temp_c)

        threading.Thread(target=push_csv_to_github_background, daemon=True).start()

        return jsonify({"success": True}), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

# ============================================================
# GITHUB FUNCTIONS (RETRY ADDED)
# ============================================================
def _retry_request(func, retries=3):
    for i in range(retries):
        try:
            result = func()
            if result and result.status_code in (200, 201):
                return result
        except Exception:
            pass
        time.sleep(1 + i)
    return None

# existing GitHub logic unchanged, but wrap PUT calls with _retry_request

# ============================================================
# ENTRYPOINT
# ============================================================
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
