#Block 1

from flask import Flask, jsonify, render_template_string, request, send_file, Response
from flask_httpauth import HTTPBasicAuth
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
auth = HTTPBasicAuth()

@auth.verify_password
def verify_password(username, password):
    valid_user = os.environ.get("DASH_USER", "admin")
    valid_pass = os.environ.get("DASH_PASSWORD", "changeme")
    if username == valid_user and password == valid_pass:
        return username
    return None

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

    <!-- BREW INFO GROUP -->
    <div class="max-w-5xl mx-auto mb-4 rounded-3xl border border-yellow-600 overflow-hidden">
        <div id="brewStarted" class="p-5 bg-zinc-900 text-lg font-medium text-zinc-400">
            🍺 Brew started: --
        </div>
        <div id="yeastInfo" class="p-5 bg-zinc-900 text-lg font-medium text-zinc-400 border-t border-zinc-700">
            🧫 Yeast: -- | 🌡️ Fermentation Temp: --
        </div>
    </div>

    <!-- DIACETYL GROUP -->
    <div class="max-w-5xl mx-auto mb-4 rounded-3xl border border-red-800 overflow-hidden">
        <div id="diacetylStatus" class="p-5 bg-zinc-900 text-lg font-medium text-zinc-600">
            🧪 Diacetyl Rest: --
        </div>
        <div id="diacetylTimer" class="p-5 bg-zinc-900 text-lg font-medium border-t border-zinc-700 hidden">
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
    <div id="modal" class="hidden fixed inset-0 modal-bg flex items-center justify-center p-4">
        <div class="bg-zinc-900 p-8 rounded-3xl w-full max-w-lg max-h-screen overflow-y-auto">

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
                    <span class="text-zinc-300">Gravity Calibration Offset</span>
                    <input name="calibration_offset" type="number" step="0.0001"
                        value="0.0000"
                        class="w-full mt-1 p-3 rounded-xl bg-zinc-800 text-white" />
                </label>

                <label class="block mb-3">
                    <span class="text-zinc-300">Yeast Name</span>
                    <input id="yeastSearch" type="text" placeholder="Type to search..."
                        autocomplete="off"
                        class="w-full mt-1 p-3 rounded-xl bg-zinc-800 text-white"
                        oninput="filterYeasts(this.value)" />
                    <input type="hidden" name="yeast_name" id="yeastNameHidden" />
                    <div id="yeastDropdown" class="hidden mt-1 max-h-48 overflow-y-auto rounded-xl bg-zinc-700 border border-zinc-600 z-50"></div>
                    <p id="yeastSelected" class="mt-1 text-sm text-yellow-400 hidden"></p>
                </label>

                <!-- YEAST INFO CARD -->
                <div id="yeastInfoCard" class="hidden mb-6 p-4 rounded-xl bg-zinc-800 border border-zinc-600 text-sm text-zinc-300 space-y-2">
                    <p class="text-base font-semibold text-yellow-400" id="yic_name"></p>
                    <div class="grid grid-cols-2 gap-x-4 gap-y-1">
                        <p>🍺 Type: <span class="text-white" id="yic_type"></span></p>
                        <p>📦 Form: <span class="text-white" id="yic_form"></span></p>
                        <p>📉 Attenuation: <span class="text-white" id="yic_attenuation"></span></p>
                        <p>❄️ Flocculation: <span class="text-white" id="yic_flocculation"></span></p>
                    </div>
                    <p>🌡️ Temp Range: <span class="text-white" id="yic_temp"></span></p>
                    <div id="yic_best_for_row" class="hidden">
                        <p class="text-zinc-400 mt-1">🏆 Best used for:</p>
                        <p class="text-zinc-200 text-xs leading-relaxed" id="yic_best_for"></p>
                    </div>
                    <div id="yic_notes_row" class="hidden">
                        <p class="text-zinc-400 mt-2">📝 Notes:</p>
                        <p class="text-zinc-200 text-xs leading-relaxed" id="yic_notes"></p>
                    </div>
                </div>

                <label class="block mb-6">
                    <span class="text-zinc-300">Fermentation Temperature (°C)</span>
                    <input name="fermentation_temp" type="number" step="0.1"
                        class="w-full mt-1 p-3 rounded-xl bg-zinc-800 text-white" />
                </label>

                <label class="flex items-center gap-3 mb-3 cursor-pointer">
                    <input name="diacetyl_rest" id="diacetylCheck" type="checkbox"
                        class="w-5 h-5 rounded accent-yellow-400"
                        onchange="toggleDiacetylFields(this.checked)" />
                    <span class="text-zinc-300">Diacetyl Rest planned for this brew?</span>
                </label>

                <div id="diacetylFields" class="hidden mb-6 pl-4 border-l-2 border-yellow-600 space-y-3">
                    <label class="block">
                        <span class="text-zinc-300">Temp increase above fermentation temp (°C)</span>
                        <input name="diacetyl_temp_increase" type="number" step="0.5" value="3"
                            class="w-full mt-1 p-3 rounded-xl bg-zinc-800 text-white" />
                    </label>
                    <label class="block">
                        <span class="text-zinc-300">Diacetyl Rest duration (days)</span>
                        <input name="diacetyl_days" type="number" step="1" min="1" value="2"
                            class="w-full mt-1 p-3 rounded-xl bg-zinc-800 text-white" />
                    </label>
                </div>

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
function toggleDiacetylFields(checked) {
    document.getElementById('diacetylFields').classList.toggle('hidden', !checked)
}

function openModal() {
    document.getElementById('modal').classList.remove('hidden')
    document.getElementById('yeastSearch').value = ''
    document.getElementById('yeastNameHidden').value = ''
    document.getElementById('yeastDropdown').classList.add('hidden')
    document.getElementById('yeastSelected').classList.add('hidden')
    document.getElementById('yeastInfoCard').classList.add('hidden')
    document.getElementById('diacetylCheck').checked = false
    document.getElementById('diacetylFields').classList.add('hidden')
    loadYeasts()
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

let allYeasts = []
let yeastsLoaded = false
let yeastsLoading = false

function loadYeasts(callback) {
    if (yeastsLoaded) { if (callback) callback(); return }
    if (yeastsLoading) {
        // Poll until loaded
        const wait = setInterval(() => {
            if (yeastsLoaded) { clearInterval(wait); if (callback) callback() }
        }, 100)
        return
    }
    yeastsLoading = true
    fetch('/yeast_list')
        .then(r => r.json())
        .then(data => {
            allYeasts = data.yeasts || []
            yeastsLoaded = true
            yeastsLoading = false
            if (callback) callback()
        })
        .catch(() => {
            yeastsLoading = false
            console.log('Could not load yeast list')
        })
}

function filterYeasts(query) {
    const dropdown = document.getElementById('yeastDropdown')
    const hidden = document.getElementById('yeastNameHidden')
    const selected = document.getElementById('yeastSelected')
    hidden.value = ''
    selected.classList.add('hidden')
    document.getElementById('yeastInfoCard').classList.add('hidden')

    if (!query || query.length < 2) {
        dropdown.classList.add('hidden')
        dropdown.innerHTML = ''
        return
    }

    const q = query.toLowerCase()
    const matches = allYeasts.filter(y => y.name.toLowerCase().includes(q)).slice(0, 50)

    if (matches.length === 0) {
        dropdown.classList.add('hidden')
        dropdown.innerHTML = ''
        return
    }

    dropdown.innerHTML = matches.map((y, i) =>
        `<div class="px-4 py-2 cursor-pointer hover:bg-zinc-600 text-white text-sm"
            onclick="selectYeast(${allYeasts.indexOf(y)})">${y.name}</div>`
    ).join('')
    dropdown.classList.remove('hidden')
}

function selectYeast(idx) {
    const y = allYeasts[idx]
    document.getElementById('yeastSearch').value = y.name
    document.getElementById('yeastNameHidden').value = y.name
    document.getElementById('yeastDropdown').classList.add('hidden')
    const sel = document.getElementById('yeastSelected')
    sel.textContent = '✅ Selected: ' + y.name
    sel.classList.remove('hidden')

    // Populate info card
    document.getElementById('yic_name').textContent = y.name
    document.getElementById('yic_type').textContent = y.type || '--'
    document.getElementById('yic_form').textContent = y.form || '--'
    document.getElementById('yic_temp').textContent = (y.min_temp && y.max_temp) ? `${y.min_temp}°C – ${y.max_temp}°C` : '--'
    document.getElementById('yic_attenuation').textContent = y.attenuation ? `${y.attenuation}%` : '--'
    document.getElementById('yic_flocculation').textContent = y.flocculation || '--'

    const bestForRow = document.getElementById('yic_best_for_row')
    if (y.best_for) {
        document.getElementById('yic_best_for').textContent = y.best_for
        bestForRow.classList.remove('hidden')
    } else {
        bestForRow.classList.add('hidden')
    }

    const notesRow = document.getElementById('yic_notes_row')
    if (y.notes) {
        document.getElementById('yic_notes').textContent = y.notes
        notesRow.classList.remove('hidden')
    } else {
        notesRow.classList.add('hidden')
    }

    document.getElementById('yeastInfoCard').classList.remove('hidden')
}

function showYeastInfo(name) {
    const modal = document.getElementById('yeastInfoModal')
    const loading = document.getElementById('yim_loading')
    modal.classList.remove('hidden')
    loading.classList.remove('hidden')

    ;['yim_name','yim_type','yim_form','yim_attenuation','yim_flocculation','yim_temp','yim_best_for','yim_notes'].forEach(id => {
        document.getElementById(id).textContent = ''
    })
    document.getElementById('yim_best_for_row').classList.add('hidden')
    document.getElementById('yim_notes_row').classList.add('hidden')

    function populate() {
        loading.classList.add('hidden')
        const y = allYeasts.find(y => y.name === name)
        if (!y) { document.getElementById('yim_name').textContent = name + ' — not found in database'; return }
        document.getElementById('yim_name').textContent = y.name
        document.getElementById('yim_type').textContent = y.type || '--'
        document.getElementById('yim_form').textContent = y.form || '--'
        document.getElementById('yim_attenuation').textContent = y.attenuation ? `${y.attenuation}%` : '--'
        document.getElementById('yim_flocculation').textContent = y.flocculation || '--'
        document.getElementById('yim_temp').textContent = (y.min_temp && y.max_temp) ? `${y.min_temp}°C – ${y.max_temp}°C` : '--'
        if (y.best_for) {
            document.getElementById('yim_best_for').textContent = y.best_for
            document.getElementById('yim_best_for_row').classList.remove('hidden')
        }
        if (y.notes) {
            document.getElementById('yim_notes').textContent = y.notes
            document.getElementById('yim_notes_row').classList.remove('hidden')
        }
    }

    loadYeasts(populate)
}

function saveDiacetylStart() {
    const val = document.getElementById('diacetylDateInput').value
    if (!val) { alert('Please select a date and time first.'); return }
    const form = new FormData()
    form.append('diacetyl_start', val)
    fetch('/set_diacetyl_start', { method: 'POST', body: form })
        .then(r => r.json())
        .then(res => {
            if (res.success) refreshData()
            else alert('Failed to save: ' + (res.error || 'Unknown error'))
        })
        .catch(() => alert('Error saving Diacetyl Rest start time'))
}

let diacetylStartTime = null
let diacetylTimerInterval = null

function startDiacetylTimer(startISO, endISO, durationDays) {
    diacetylStartTime = new Date(startISO)
    const threshold = durationDays != null ? durationDays : 2
    const timerEl = document.getElementById('diacetylTimer')
    timerEl.classList.remove('hidden')

    if (diacetylTimerInterval) clearInterval(diacetylTimerInterval)

    // If already ended, show finished state and stop
    if (endISO) {
        const endTime = new Date(endISO)
        const diffMs = endTime - diacetylStartTime
        const totalHours = Math.floor(diffMs / (1000 * 60 * 60))
        const days = Math.floor(totalHours / 24)
        const hours = totalHours % 24
        const over = days >= threshold
        const colour = over ? 'text-red-400' : 'text-green-400'
        const endFormatted = endTime.toLocaleString('en-GB', {day:'2-digit', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit', hour12:false})
        timerEl.innerHTML = `✅ Diacetyl Rest complete: <span class="font-bold ${colour}">${days} days ${hours} hours</span> &nbsp;|&nbsp; Finished: <span class="font-bold text-zinc-300">${endFormatted}</span>`
        return
    }

    function tick() {
        const now = new Date()
        const diffMs = now - diacetylStartTime
        if (diffMs < 0) {
            timerEl.innerHTML = `⏱️ Diacetyl Rest timer: <span class="font-bold text-zinc-400">Not started yet</span>
                <button onclick="stopDiacetylRestNow()" class="ml-4 px-3 py-1 rounded-xl bg-red-700 hover:bg-red-600 text-white text-sm font-semibold">Stop & Finish</button>`
            return
        }
        const totalHours = Math.floor(diffMs / (1000 * 60 * 60))
        const days = Math.floor(totalHours / 24)
        const hours = totalHours % 24
        const over = days >= threshold
        const colour = over ? 'text-red-400' : 'text-green-400'
        const label = over ? '⚠️ Diacetyl Rest exceeded:' : '⏱️ Diacetyl Rest timer:'
        timerEl.innerHTML = `${label} <span class="font-bold ${colour}">${days} days ${hours} hours</span>
            <button onclick="stopDiacetylRestNow()" class="ml-4 px-3 py-1 rounded-xl bg-red-700 hover:bg-red-600 text-white text-sm font-semibold">Stop & Finish</button>`
    }

    tick()
    diacetylTimerInterval = setInterval(tick, 60000)
}

function stopDiacetylRestNow() {
    if (!confirm('Stop the Diacetyl Rest timer and record the finish time now?')) return
    fetch('/set_diacetyl_end', { method: 'POST' })
        .then(r => r.json())
        .then(res => {
            if (res.success) refreshData()
            else alert('Failed to stop timer: ' + (res.error || 'Unknown error'))
        })
        .catch(() => alert('Error stopping Diacetyl Rest timer'))
}

function stopDiacetylTimer() {
    if (diacetylTimerInterval) { clearInterval(diacetylTimerInterval); diacetylTimerInterval = null }
    const timerEl = document.getElementById('diacetylTimer')
    timerEl.classList.add('hidden')
    timerEl.innerHTML = ''
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

            if (result.session_start) {
                const sd = new Date(result.session_start)
                const formatted = sd.toLocaleString('en-GB', {
                    day: '2-digit', month: 'short', year: 'numeric',
                    hour: '2-digit', minute: '2-digit', hour12: false
                })
                const name = result.profile_name || 'Brew'
                document.getElementById('brewStarted').innerHTML = `🍺 <span class="font-bold text-yellow-400">${name}</span> brew started on: ${formatted}`
            }

            const yeastEl = document.getElementById('yeastInfo')
            const yeast = result.yeast_name || '--'
            const temp = result.fermentation_temp != null ? `${result.fermentation_temp}°C` : '--'
            const infoBtn = yeast !== '--'
                ? ` &nbsp;<button onclick="showYeastInfo('${yeast.replace(/'/g, "\\'")}')" title="Yeast info"
                    class="mr-2 px-2 py-0.5 rounded-lg bg-zinc-700 hover:bg-zinc-600 text-zinc-300 text-sm font-semibold align-middle">ℹ️</button>&nbsp;|&nbsp;`
                : ' &nbsp;|&nbsp;'
            yeastEl.innerHTML = `🧫 Yeast: <span class="font-bold text-zinc-200">${yeast}</span>${infoBtn} 🌡️ Fermentation Temp: <span class="font-bold text-zinc-200">${temp}</span>`

            const diacetylEl = document.getElementById('diacetylStatus')
            const diacetylTempIncrease = result.diacetyl_temp_increase != null ? result.diacetyl_temp_increase : 3
            const diacetylDays = result.diacetyl_days != null ? result.diacetyl_days : 2
            const diacetylTemp = result.fermentation_temp != null ? `${(result.fermentation_temp + diacetylTempIncrease).toFixed(1)}°C` : '--'
            if (result.diacetyl_rest) {
                const savedStart = result.diacetyl_start || ''
                const savedLabel = savedStart
                    ? ` &nbsp;|&nbsp; 📅 <span class="font-bold text-green-400">${new Date(savedStart).toLocaleString('en-GB', {day:'2-digit', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit', hour12:false})}</span>`
                    : ''
                diacetylEl.innerHTML = `<div class="flex items-center gap-3 flex-wrap">
                        🧪 Diacetyl Rest: <span class="font-bold text-green-400">Yes</span>
                        &nbsp;|&nbsp; 🌡️ Rest Temp: <span class="font-bold text-green-400">${diacetylTemp}</span>
                        ${savedLabel}
                        &nbsp;|&nbsp;
                        <input id="diacetylDateInput" type="datetime-local"
                            class="p-2 rounded-xl bg-zinc-800 text-white text-sm border border-zinc-700"
                            value="${savedStart}" />
                        <button onclick="saveDiacetylStart()"
                            class="px-4 py-2 rounded-xl bg-green-600 hover:bg-green-500 text-white text-sm font-semibold">
                            Set Rest Start
                        </button>
                    </div>`
                diacetylEl.className = diacetylEl.className.replace('text-zinc-600', 'text-zinc-400')

                if (savedStart) {
                    startDiacetylTimer(savedStart, result.diacetyl_end || null, diacetylDays)
                } else {
                    stopDiacetylTimer()
                }
            } else {
                diacetylEl.innerHTML = `🧪 Diacetyl Rest: <span class="text-zinc-600">No</span>`
                diacetylEl.className = diacetylEl.className.replace('text-zinc-400', 'text-zinc-600')
                stopDiacetylTimer()
            }

            status.innerHTML = `✅ Last updated: ${result.timestamp || 'Unknown'}`
        })
        .catch(err => {
            console.error(err)
            status.innerHTML = '❌ Error loading data'
        })
}

window.onload = function() {
    refreshData()
    loadYeasts()
}

setInterval(function() {
    refreshData()
}, 30000)
</script>

<!-- YEAST INFO MODAL (main display) -->
<div id="yeastInfoModal" class="hidden fixed inset-0 bg-black bg-opacity-70 flex items-center justify-center z-50 p-4">
    <div class="bg-zinc-900 rounded-3xl p-6 max-w-lg w-full border border-zinc-600 relative">
        <button onclick="document.getElementById('yeastInfoModal').classList.add('hidden')"
            class="absolute top-4 right-4 text-zinc-400 hover:text-white text-xl font-bold">✕</button>
        <p class="text-base font-semibold text-yellow-400 mb-4" id="yim_name">--</p>
        <div class="grid grid-cols-2 gap-x-4 gap-y-2 text-sm text-zinc-300 mb-3">
            <p>🍺 Type: <span class="text-white" id="yim_type"></span></p>
            <p>📦 Form: <span class="text-white" id="yim_form"></span></p>
            <p>📉 Attenuation: <span class="text-white" id="yim_attenuation"></span></p>
            <p>❄️ Flocculation: <span class="text-white" id="yim_flocculation"></span></p>
        </div>
        <p class="text-sm text-zinc-300 mb-3">🌡️ Temp Range: <span class="text-white" id="yim_temp"></span></p>
        <div id="yim_best_for_row" class="hidden mb-3">
            <p class="text-sm text-zinc-400">🏆 Best used for:</p>
            <p class="text-sm text-zinc-200 leading-relaxed" id="yim_best_for"></p>
        </div>
        <div id="yim_notes_row" class="hidden">
            <p class="text-sm text-zinc-400">📝 Notes:</p>
            <p class="text-xs text-zinc-200 leading-relaxed" id="yim_notes"></p>
        </div>
        <p id="yim_loading" class="text-zinc-400 text-sm hidden">Loading...</p>
    </div>
</div>

</body>
</html>
"""




#Block 3


# ============================================================
#  DASHBOARD ROUTE
# ============================================================
@app.route("/")
@auth.login_required
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
@auth.login_required
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

                    return jsonify({"data": fallback, "timestamp": last["timestamp"], "session_start": cfg.get("session_start", ""), "profile_name": cfg.get("profile_name", ""), "diacetyl_rest": cfg.get("diacetyl_rest", False), "diacetyl_temp_increase": cfg.get("diacetyl_temp_increase", 3), "diacetyl_days": cfg.get("diacetyl_days", 2), "yeast_name": cfg.get("yeast_name", ""), "fermentation_temp": cfg.get("fermentation_temp"), "diacetyl_start": cfg.get("diacetyl_start", ""), "diacetyl_end": cfg.get("diacetyl_end", "")})

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
    return jsonify({"data": data_to_send, "timestamp": ts, "session_start": cfg.get("session_start", ""), "profile_name": cfg.get("profile_name", ""), "diacetyl_rest": cfg.get("diacetyl_rest", False), "diacetyl_temp_increase": cfg.get("diacetyl_temp_increase", 3), "diacetyl_days": cfg.get("diacetyl_days", 2), "yeast_name": cfg.get("yeast_name", ""), "fermentation_temp": cfg.get("fermentation_temp"), "diacetyl_start": cfg.get("diacetyl_start", ""), "diacetyl_end": cfg.get("diacetyl_end", "")})


# ============================================================
#  WEBHOOK — LOGS TO CSV + AUTO‑PUSH TO GITHUB
# ============================================================
@app.route("/webhook/<token>", methods=["POST"])
def webhook(token):
    if token != os.environ.get("WEBHOOK_SECRET", ""):
        return jsonify({"error": "Unauthorized"}), 403

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
@auth.login_required
def start_brew():
    profile_name = request.form.get("profile_name", "").strip() or "Unnamed_Brew"
    og = request.form.get("original_gravity", "").strip()
    start_date = request.form.get("start_date", "")
    start_time = request.form.get("start_time", "")
    offset = request.form.get("calibration_offset", "").strip()
    diacetyl_rest = request.form.get("diacetyl_rest") == "on"
    yeast_name = request.form.get("yeast_name", "").strip()
    fermentation_temp = request.form.get("fermentation_temp", "").strip()
    diacetyl_temp_increase = request.form.get("diacetyl_temp_increase", "3").strip()
    diacetyl_days = request.form.get("diacetyl_days", "2").strip()

    if start_date and start_time:
        session_start = f"{start_date}T{start_time}"
    else:
        session_start = datetime.now(uk).isoformat(timespec="minutes")

    new_config = {
        "profile_name": profile_name,
        "original_gravity": float(og) if og else 1.050,
        "session_start": session_start,
        "calibration_offset": float(offset) if offset else 0.0000,
        "diacetyl_rest": diacetyl_rest,
        "diacetyl_temp_increase": float(diacetyl_temp_increase) if diacetyl_temp_increase else 3.0,
        "diacetyl_days": int(diacetyl_days) if diacetyl_days else 2,
        "yeast_name": yeast_name,
        "fermentation_temp": float(fermentation_temp) if fermentation_temp else None,
    }

    with open(CONFIG_FILE, "w") as f:
        json.dump(new_config, f, indent=4)

    # Push updated config to GitHub so it survives Render restarts
    threading.Thread(target=push_config_to_github, daemon=True).start()

    return jsonify({"success": True})


# ============================================================
#  SET DIACETYL REST START DATE/TIME
# ============================================================
@app.route("/set_diacetyl_start", methods=["POST"])
@auth.login_required
def set_diacetyl_start():
    try:
        diacetyl_start = request.form.get("diacetyl_start", "").strip()
        if not diacetyl_start:
            return jsonify({"success": False, "error": "No date/time provided"}), 400

        cfg = load_config()
        cfg["diacetyl_start"] = diacetyl_start

        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=4)

        threading.Thread(target=push_config_to_github, daemon=True).start()

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


# ============================================================
#  SET DIACETYL REST END DATE/TIME
# ============================================================
@app.route("/set_diacetyl_end", methods=["POST"])
@auth.login_required
def set_diacetyl_end():
    try:
        cfg = load_config()
        cfg["diacetyl_end"] = datetime.now(uk).isoformat(timespec="minutes")

        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=4)

        threading.Thread(target=push_config_to_github, daemon=True).start()

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/yeast_list")
@auth.login_required
def yeast_list():
    headers = _github_headers()
    if headers is None:
        return jsonify({"error": "No GitHub token"}), 500

    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/master_yeast_database.csv"
    try:
        existing = _github_get_file(headers, url)
        if not existing:
            return jsonify({"error": "CSV not found on GitHub"}), 404

        content_bytes = base64.b64decode(existing.get("content", ""))
        lines = content_bytes.decode("utf-8-sig").splitlines()
        reader = csv.DictReader(lines)
        yeasts = []
        seen = set()
        for row in reader:
            name = row.get("Name", "").strip()
            if name and name not in seen:
                seen.add(name)
                yeasts.append({
                    "name":        name,
                    "type":        row.get("Yeast Type", "").strip(),
                    "min_temp":    row.get("MinTemp", "").strip(),
                    "max_temp":    row.get("MaxTemp", "").strip(),
                    "flocculation":row.get("Flocculation", "").strip(),
                    "attenuation": row.get("Attenuation", "").strip(),
                    "form":        row.get("Form", "").strip(),
                    "best_for":    row.get("Best used For", "").strip(),
                    "notes":       row.get("Yeast Notes", "").strip(),
                })
        yeasts.sort(key=lambda y: y["name"])
        return jsonify({"yeasts": yeasts})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
        writer.writerow(
            [
                timestamp.astimezone(uk).isoformat(),
                raw_sg,
                temp_c,
            ]
        )


# ============================================================
#  DOWNLOAD CSV
# ============================================================
@app.route("/download_csv")
@auth.login_required
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
        mimetype="text/csv",
    )


# ============================================================
#  DOWNLOAD JSON (GENERATED FROM CSV)
# ============================================================
@app.route("/download_json")
@auth.login_required
def download_json():
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
            data.append(
                {
                    "timestamp": row["timestamp"],
                    "gravity": float(row["gravity"]),
                    "temperature": float(row["temperature"]),
                }
            )

    json_name = os.path.basename(csv_file).replace(".csv", ".json")
    return Response(
        json.dumps(data, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename={json_name}"},
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
        "Accept": "application/vnd.github+json",
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
#  PUSH CONFIG TO GITHUB
# ============================================================
def push_config_to_github():
    headers = _github_headers()
    if headers is None:
        print("Config push skipped: GITHUB_TOKEN not set")
        return

    base_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/config.json"

    try:
        with open(CONFIG_FILE, "rb") as f:
            content_b64 = base64.b64encode(f.read()).decode("utf-8")

        existing = _github_get_file(headers, base_url)
        sha = existing.get("sha") if existing else None

        payload = {
            "message": "Update config.json via Start New Brew",
            "content": content_b64,
            "branch": GITHUB_BRANCH,
        }
        if sha:
            payload["sha"] = sha

        resp = _github_put_file(headers, base_url, payload)
        if resp and resp.status_code in (200, 201):
            print("Config pushed to GitHub OK")
        else:
            print("Config push failed:", resp.status_code if resp else "No response")

    except Exception as e:
        print("Config push exception:", e)


# ============================================================
#  MANUAL PUSH TO GITHUB
# ============================================================
@app.route("/push_to_github", methods=["POST"])
@auth.login_required
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
            "branch": GITHUB_BRANCH,
        }
        if sha:
            payload["sha"] = sha

        resp = _github_put_file(headers, base_url, payload)
        if resp and resp.status_code in (200, 201):
            return jsonify({"success": True}), 200

        return jsonify(
            {
                "success": False,
                "error": f"GitHub error: {resp.status_code if resp else 'No response'}",
            }
        ), 500

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
#  MANUAL PULL FROM GITHUB
# ============================================================
@app.route("/pull_from_github", methods=["POST"])
@auth.login_required
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
            return jsonify(
                {"success": False, "error": "GitHub file too small — refusing to overwrite"}
            ), 500

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
            "branch": GITHUB_BRANCH,
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
    daemon=True,
).start()


# ============================================================
#  AUTO‑RESTORE CONFIG ON STARTUP
# ============================================================
def restore_config_from_github_on_startup():
    """Restores config.json from GitHub after a Render redeploy."""
    headers = _github_headers()
    if headers is None:
        print("Config restore skipped: no GITHUB_TOKEN")
        return

    base_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/config.json"

    try:
        existing = _github_get_file(headers, base_url)
        if not existing:
            print("Config restore: no config.json on GitHub")
            return

        content_bytes = base64.b64decode(existing.get("content", ""))
        if len(content_bytes) < 5:
            print("Config restore: file too small, skipping")
            return

        with open(CONFIG_FILE, "wb") as f:
            f.write(content_bytes)

        print("Config restore: config.json restored from GitHub")

    except Exception as e:
        print("Config restore failed:", e)


threading.Thread(target=restore_config_from_github_on_startup, daemon=True).start()



#Block 5B

# ============================================================
#  BLOCK 5B — LOG VIEWER HTML + JAVASCRIPT (FINAL VERSION)
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

    <div class="max-w-5xl mx-auto mb-6">

        <!-- Top bar with glossy green Close button -->
        <div class="flex justify-between items-center mb-4">
            <h1 class="text-3xl font-semibold">Fermentation Log Viewer</h1>

            <button onclick="window.close()"
                class="px-6 py-2 rounded-full font-bold text-white
                       bg-gradient-to-b from-green-400 to-green-600
                       shadow-lg shadow-green-900/40
                       hover:from-green-300 hover:to-green-500
                       active:scale-95 transition">
                CLOSE
            </button>
        </div>

        <p class="text-zinc-400 mb-6">View and explore your fermentation history.</p>

        <!-- Download buttons -->
        <div class="flex gap-3 mb-6">
            <a href="/download_json" class="bg-blue-500 hover:bg-blue-400 text-white px-4 py-2 rounded-lg">Download JSON</a>
            <a href="/download_csv" class="bg-emerald-500 hover:bg-emerald-400 text-white px-4 py-2 rounded-lg">Download CSV</a>
        </div>

        <!-- Time filter buttons -->
        <div class="flex gap-3 mb-4">
            <button id="btn1"  class="filter-btn px-4 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700">Last 1h</button>
            <button id="btn6"  class="filter-btn px-4 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700">Last 6h</button>
            <button id="btn12" class="filter-btn px-4 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700">Last 12h</button>
            <button id="btn24" class="filter-btn px-4 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700">Last 24h</button>
            <button id="btn48" class="filter-btn px-4 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700">Last 48h</button>
            <button id="btnAll" class="filter-btn px-4 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700">Show All</button>
        </div>

        <!-- Datetime range -->
        <div class="flex gap-3 mb-6">
            <input type="datetime-local" id="startDateTime"
                   class="bg-zinc-800 text-white px-3 py-2 rounded-lg">

            <input type="datetime-local" id="endDateTime"
                   class="bg-zinc-800 text-white px-3 py-2 rounded-lg">

            <button onclick="applyDateRange()"
                    class="bg-amber-500 hover:bg-amber-400 text-black px-4 py-2 rounded-lg">
                Apply
            </button>
        </div>

        <!-- GitHub buttons -->
        <div class="flex gap-3 mb-10">
            <button onclick="pullCSV()" class="bg-zinc-800 hover:bg-zinc-700 text-amber-300 px-4 py-2 rounded-lg">⬇ Pull CSV from GitHub</button>
            <button onclick="pushCSV()" class="bg-zinc-800 hover:bg-zinc-700 text-emerald-400 px-4 py-2 rounded-lg">⬆ Upload CSV to GitHub</button>
        </div>

        <!-- Gravity Graph -->
        <h2 class="text-xl font-semibold mb-2">Gravity</h2>
        <canvas id="gravityChart" class="mb-10"></canvas>

        <!-- Temperature Graph -->
        <h2 class="text-xl font-semibold mb-2">Temperature</h2>
        <canvas id="tempChart"></canvas>
    </div>

<script>

// ===============================
// GITHUB SYNC BUTTONS + WARNINGS
// ===============================
function pushCSV() {

    // First warning
    const warn1 = confirm(
        "⚠ WARNING ⚠\\n\\n" +
        "You are about to REPLACE the CSV file on GitHub.\\n" +
        "If the GitHub file has more data than this log file, you may LOSE DATA.\\n\\n" +
        "Recommended: If unsure, take a backup of the CSV on GitHub first.\\n\\n" +
        "Do you want to continue?"
    );

    if (!warn1) {
        alert("Upload cancelled.");
        return;
    }

    // Second confirmation
    const warn2 = confirm(
        "Are you absolutely sure you want to overwrite the CSV on GitHub?"
    );

    if (!warn2) {
        alert("Upload cancelled.");
        return;
    }

    // Proceed with upload
    alert("Uploading CSV to GitHub…");

    fetch('/push_to_github', { method: 'POST' })
        .then(r => r.json())
        .then(res => {
            if (res.success) {
                alert("✅ CSV uploaded to GitHub.");
            } else {
                alert("❌ Upload failed: " + (res.error || "Unknown error"));
            }
        })
        .catch(err => {
            console.error(err);
            alert("❌ Upload failed: network error");
        });
}

function pullCSV() {
    alert("Pulling CSV from GitHub…");

    fetch('/pull_from_github', { method: 'POST' })
        .then(r => r.json())
        .then(res => {
            if (res.success) {
                alert("✅ CSV pulled from GitHub.");
                location.reload();
            } else {
                alert("❌ Pull failed: " + (res.error || "Unknown error"));
            }
        })
        .catch(err => {
            console.error(err);
            alert("❌ Pull failed: network error");
        });
}

// ===============================
// BUTTON HIGHLIGHT HANDLING
// ===============================
function setActiveButton(activeId) {
    const buttons = ["btn1", "btn6", "btn12", "btn24", "btn48", "btnAll"];
    buttons.forEach(id => {
        const btn = document.getElementById(id);
        if (id === activeId) {
            btn.classList.add("bg-blue-500", "text-white");
            btn.classList.remove("bg-zinc-800");
        } else {
            btn.classList.remove("bg-blue-500", "text-white");
            btn.classList.add("bg-zinc-800");
        }
    });
}

document.getElementById("btn1").onclick = () => {
    setActiveButton("btn1");
    applyFilter(1);
};

document.getElementById("btn6").onclick = () => {
    setActiveButton("btn6");
    applyFilter(6);
};

document.getElementById("btn12").onclick = () => {
    setActiveButton("btn12");
    applyFilter(12);
};

document.getElementById("btn24").onclick = () => {
    setActiveButton("btn24");
    applyFilter(24);
};

document.getElementById("btn48").onclick = () => {
    setActiveButton("btn48");
    applyFilter(48);
};

document.getElementById("btnAll").onclick = () => {
    setActiveButton("btnAll");
    applyFilter("all");
};

// Default active button = 12 hours
setActiveButton("btn12");

// ===============================
// DATA + CHART LOGIC
// ===============================
let logData = {{ log_json | safe }};

function applyFilter(hours) {
    let cutoff = null;

    if (hours !== "all") {
        cutoff = new Date(Date.now() - hours * 60 * 60 * 1000);
    }

    const filtered = logData.filter(entry => {
        const t = new Date(entry.timestamp);
        return cutoff ? t >= cutoff : true;
    });

    updateCharts(filtered);
}

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

// ===============================
// CHART RENDERING + DROP DETECTION
// ===============================
let gravityChart, tempChart;

function updateCharts(data) {
    const labels = data.map(e => e.timestamp);
    const gravity = data.map(e => e.gravity_corrected);
    const temp = data.map(e => e.temperature);

    // Detect sudden drops
    const gravityDrops = [];
    const tempDrops = [];

    for (let i = 1; i < data.length; i++) {
        const gDiff = data[i - 1].gravity_corrected - data[i].gravity_corrected;
        const tDiff = data[i - 1].temperature - data[i].temperature;

        if (gDiff > 0.004) {
            gravityDrops.push({ index: i, value: data[i].gravity_corrected });
        }

        if (tDiff > 2) {
            tempDrops.push({ index: i, value: data[i].temperature });
        }
    }

    if (gravityChart) gravityChart.destroy();
    if (tempChart) tempChart.destroy();

    // Gravity chart
    gravityChart = new Chart(document.getElementById("gravityChart"), {
        type: "line",
        data: {
            labels,
            datasets: [
                {
                    label: "Gravity",
                    data: gravity,
                    borderColor: "#22c55e",
                    tension: 0.3
                },
                {
                    label: "Sudden Gravity Drop",
                    data: gravityDrops.map(d => ({ x: labels[d.index], y: d.value })),
                    pointRadius: 6,
                    pointBackgroundColor: "#ef4444",
                    showLine: false
                }
            ]
        }
    });

    // Temperature chart
    tempChart = new Chart(document.getElementById("tempChart"), {
        type: "line",
        data: {
            labels,
            datasets: [
                {
                    label: "Temperature (°C)",
                    data: temp,
                    borderColor: "#facc15",
                    tension: 0.3
                },
                {
                    label: "Sudden Temp Drop",
                    data: tempDrops.map(d => ({ x: labels[d.index], y: d.value })),
                    pointRadius: 6,
                    pointBackgroundColor: "#f87171",
                    showLine: false
                }
            ]
        }
    });
}

// Render initial 12h view
applyFilter(12);

</script>

</body>
</html>
"""



#Block 5C

# ============================================================
#  BLOCK 5C — VIEW LOG ROUTE (SERVES BLOCK 5B HTML)
# ============================================================
@app.route("/view_log")
@auth.login_required
def view_log_page():
    csv_file = get_current_brew_log_csv_filename()

    # Ensure CSV exists
    if not os.path.exists(csv_file):
        os.makedirs(os.path.dirname(csv_file), exist_ok=True)
        with open(csv_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "gravity", "temperature"])

    # Load CSV data
    data = []
    with open(csv_file, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                data.append({
                    "timestamp": row["timestamp"],
                    "gravity_corrected": float(row["gravity"]),
                    "temperature": float(row["temperature"])
                })
            except:
                continue

    # Render HTML with embedded JSON
    return render_template_string(LOG_VIEWER_HTML, log_json=json.dumps(data))


