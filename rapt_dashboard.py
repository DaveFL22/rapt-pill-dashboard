# ============================================================
# VIEW LOG (FULL ORIGINAL LAYOUT + temperature offset support)
# ============================================================
@app.route("/view_log")
def view_log():
    csv_file = get_current_brew_log_csv_filename()
    if not os.path.exists(csv_file):
        os.makedirs(os.path.dirname(csv_file), exist_ok=True)
        with open(csv_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "gravity", "temperature_raw"])

    cfg = get_config()
    temp_offset = cfg.get("temperature_offset", 0.0)

    data = []
    with open(csv_file, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                raw_temp = float(row.get("temperature_raw", row.get("temperature", 0)))
                corrected_temp = round(raw_temp + temp_offset, 2)
                data.append({
                    "timestamp": row["timestamp"],
                    "gravity": float(row["gravity"]),
                    "temperature": corrected_temp,
                    "temperature_raw": raw_temp
                })
            except Exception:
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
        :root {{ color-scheme: dark; }}
        body {{ background: #020617; color: #e5e7eb; font-family: system-ui, sans-serif; margin: 0; padding: 24px; }}
        .container {{ max-width: 1100px; margin: 0 auto; }}
        h1 {{ font-size: 1.8rem; font-weight: 600; margin-bottom: 4px; }}
        .sub {{ color: #9ca3af; font-size: 0.9rem; margin-bottom: 16px; }}
        a {{ color: #fbbf24; text-decoration: none; font-size: 0.9rem; margin-right: 12px; }}
        .card {{ background: #020617; border-radius: 18px; border: 1px solid #1f2937; padding: 18px 20px; margin-top: 16px; }}
        .filters {{ display: flex; gap: 8px; flex-wrap: wrap; }}
        .btn {{ border-radius: 999px; border: 1px solid #374151; background: #020617; color: #e5e7eb; font-size: 0.8rem; padding: 6px 12px; cursor: pointer; }}
        .btn-primary {{ background: #2563eb; border-color: #2563eb; }}
        .range-row {{ display: flex; gap: 10px; margin-top: 10px; flex-wrap: wrap; }}
        input[type="datetime-local"] {{ background: #020617; border-radius: 999px; border: 1px solid #374151; color: #e5e7eb; padding: 6px 10px; font-size: 0.8rem; }}
        .chart-container {{ height: 300px; }}
        pre {{ background: #020617; border-radius: 18px; border: 1px solid #1f2937; padding: 16px; font-size: 0.75rem; overflow: auto; max-height: 320px; }}
        .chart-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }}
        .chart-title {{ font-size: 0.95rem; font-weight: 500; }}
        #ghStatusLog {{ font-size: 0.8rem; margin-top: 6px; color: #9ca3af; }}
    </style>
</head>
<body>
<div class="container">
    <h1>Fermentation Log Viewer</h1>
    <p class="sub">Visualise and explore your fermentation history (Temperature shown with offset applied)</p>
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
        const threshold = 0.0005;
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
        document.getElementById("jsonOutput").textContent = JSON.stringify(data, null, 2);
        gravityChart.data.labels = ts;
        gravityChart.data.datasets[0].data = gs;
        gravityChart.data.datasets[1].data = dropPoints;
        gravityChart.update();
        tempChart.data.labels = ts;
        tempChart.data.datasets[0].data = ts2;
        tempChart.update();
    }}
    function resetZoom(which) {{
        if (which === 'gravity') gravityChart.resetZoom();
        else if (which === 'temp') tempChart.resetZoom();
    }}
    function setGhStatusLog(msg, ok=true) {{
        const el = document.getElementById('ghStatusLog');
        el.textContent = msg;
        el.style.color = ok ? '#4ade80' : '#f97373';
        if (msg) setTimeout(() => {{ el.textContent = ''; }}, 6000);
    }}
    function pushToGitHubLog() {{
        setGhStatusLog('Uploading log to GitHub...', true);
        fetch('/push_to_github', {{ method: 'POST' }})
            .then(r => r.json())
            .then(res => {{
                if (res.success) setGhStatusLog('✅ Log uploaded to GitHub.');
                else setGhStatusLog('❌ Upload failed: ' + (res.error || 'Unknown error'), false);
            }})
            .catch(() => setGhStatusLog('❌ Upload failed: network error', false));
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
            .catch(() => setGhStatusLog('❌ Pull failed: network error', false));
    }}
    const gravityChart = new Chart(document.getElementById('gravityChart').getContext('2d'), {{
        type: 'line',
        data: {{
            labels: {timestamps},
            datasets: [
                {{ label: 'Gravity', data: {gravities}, borderColor: '#22c55e', tension: 0.25 }},
                {{ label: 'Rapid Drop', data: [], pointRadius: 6, pointBackgroundColor: 'red', showLine: false, type: 'scatter' }}
            ]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            scales: {{ 
                x: {{ ticks: {{ color: '#9ca3af' }}, grid: {{ color: '#111827' }} }}, 
                y: {{ ticks: {{ color: '#9ca3af' }}, grid: {{ color: '#111827' }} }} 
            }},
            plugins: {{
                legend: {{ labels: {{ color: '#e5e7eb' }} }},
                zoom: {{ 
                    zoom: {{ wheel: {{ enabled: true }}, pinch: {{ enabled: true }}, mode: 'x' }}, 
                    pan: {{ enabled: true, mode: 'x' }} 
                }}
            }}
        }}
    }});
    const tempChart = new Chart(document.getElementById('tempChart').getContext('2d'), {{
        type: 'line',
        data: {{
            labels: {timestamps},
            datasets: [{{ label: 'Temperature (°C)', data: {temps}, borderColor: '#fbbf24', tension: 0.25 }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            scales: {{ 
                x: {{ ticks: {{ color: '#9ca3af' }}, grid: {{ color: '#111827' }} }}, 
                y: {{ ticks: {{ color: '#9ca3af' }}, grid: {{ color: '#111827' }} }} 
            }},
            plugins: {{
                legend: {{ labels: {{ color: '#e5e7eb' }} }},
                zoom: {{ 
                    zoom: {{ wheel: {{ enabled: true }}, pinch: {{ enabled: true }}, mode: 'x' }}, 
                    pan: {{ enabled: true, mode: 'x' }} 
                }}
            }}
        }}
    }});
    render(fullData);
</script>
</body>
</html>
"""