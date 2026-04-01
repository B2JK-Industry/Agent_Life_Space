"""
Agent Life Space — Operator Dashboard

Self-contained HTML dashboard served by the agent API.
No build tools, no React, no npm — vanilla HTML + JS calling /api/operator/*.

Served at GET /dashboard on the same port as the API (8420).
Requires API key auth via query parameter or session.
"""

from __future__ import annotations

from agent.core.identity import get_agent_identity

_DASHBOARD_VERSION = "1.0.0"


def render_dashboard_html(api_key_hint: str = "") -> str:
    """Render the full operator dashboard HTML.

    The HTML is self-contained: inline CSS + JS, no external dependencies.
    JS fetches data from /api/operator/* endpoints using the stored API key.
    """
    identity = get_agent_identity()
    agent_name = identity.agent_name

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{agent_name} — Operator Dashboard</title>
<style>
:root {{
  --bg: #0f1117;
  --surface: #1a1d27;
  --border: #2a2d37;
  --text: #e1e4eb;
  --text-muted: #8b8fa3;
  --accent: #6c8aff;
  --green: #4ade80;
  --red: #f87171;
  --yellow: #fbbf24;
  --orange: #fb923c;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
  background: var(--bg);
  color: var(--text);
  font-size: 14px;
  line-height: 1.5;
}}
.container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
header {{
  display: flex; justify-content: space-between; align-items: center;
  padding: 16px 0; border-bottom: 1px solid var(--border); margin-bottom: 24px;
}}
header h1 {{ font-size: 18px; font-weight: 600; }}
header .status {{ font-size: 12px; color: var(--green); }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-bottom: 24px; }}
.card {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 16px;
}}
.card h2 {{ font-size: 13px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 12px; }}
.metric {{ font-size: 28px; font-weight: 700; }}
.metric-label {{ font-size: 11px; color: var(--text-muted); margin-top: 2px; }}
.metric-row {{ display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid var(--border); }}
.metric-row:last-child {{ border: none; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
.badge-green {{ background: rgba(74,222,128,0.15); color: var(--green); }}
.badge-red {{ background: rgba(248,113,113,0.15); color: var(--red); }}
.badge-yellow {{ background: rgba(251,191,36,0.15); color: var(--yellow); }}
.badge-blue {{ background: rgba(108,138,255,0.15); color: var(--accent); }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th {{ text-align: left; padding: 8px; color: var(--text-muted); font-size: 11px; text-transform: uppercase; border-bottom: 1px solid var(--border); }}
td {{ padding: 8px; border-bottom: 1px solid var(--border); }}
tr:hover td {{ background: rgba(108,138,255,0.05); }}
.section {{ margin-bottom: 24px; }}
.section-title {{ font-size: 15px; font-weight: 600; margin-bottom: 12px; }}
.auth-form {{ max-width: 400px; margin: 100px auto; text-align: center; }}
.auth-form input {{
  background: var(--surface); border: 1px solid var(--border); color: var(--text);
  padding: 10px 16px; border-radius: 6px; width: 100%; font-size: 14px;
  font-family: inherit; margin: 12px 0;
}}
.auth-form button {{
  background: var(--accent); color: white; border: none; padding: 10px 24px;
  border-radius: 6px; font-size: 14px; cursor: pointer; font-family: inherit;
}}
.refresh {{ font-size: 11px; color: var(--text-muted); }}
#error {{ color: var(--red); font-size: 12px; margin: 8px 0; display: none; }}
.loading {{ color: var(--text-muted); font-style: italic; }}
</style>
</head>
<body>
<div class="container" id="app">
  <div class="auth-form" id="auth">
    <h1>{agent_name}</h1>
    <p style="color: var(--text-muted); margin: 16px 0;">Operator Dashboard</p>
    <input type="password" id="apikey" placeholder="API Key" autocomplete="off">
    <div id="error"></div>
    <button onclick="authenticate()">Connect</button>
  </div>
  <div id="dashboard" style="display:none">
    <header>
      <h1>{agent_name} <span style="color:var(--text-muted);font-weight:400">Operator</span></h1>
      <div><span class="status" id="conn-status">connected</span> <span class="refresh" id="last-refresh"></span></div>
    </header>
    <div class="grid" id="metrics"></div>
    <div class="section" id="jobs-section">
      <div class="section-title">Recent Jobs</div>
      <table id="jobs-table"><thead><tr><th>ID</th><th>Kind</th><th>Status</th><th>Duration</th><th>Cost</th></tr></thead><tbody></tbody></table>
    </div>
    <div class="section" id="settlements-section">
      <div class="section-title">Settlements</div>
      <div class="card" id="settlements-card"><span class="loading">Loading...</span></div>
    </div>
    <div class="grid">
      <div class="section" id="retention-section">
        <div class="section-title">Retention</div>
        <div class="card" id="retention-card"><span class="loading">Loading...</span></div>
      </div>
      <div class="section" id="audit-section">
        <div class="section-title">API Audit</div>
        <div class="card" id="audit-card"><span class="loading">Loading...</span></div>
      </div>
    </div>
  </div>
</div>
<script>
let KEY = localStorage.getItem('als_api_key') || '';
const BASE = window.location.origin;

if (KEY) {{ showDashboard(); }}

function authenticate() {{
  KEY = document.getElementById('apikey').value.trim();
  if (!KEY) return;
  fetch(BASE + '/api/operator/report', {{headers: {{'Authorization': 'Bearer ' + KEY}}}})
    .then(r => {{
      if (r.status === 401) throw new Error('Invalid API key');
      return r.json();
    }})
    .then(() => {{
      localStorage.setItem('als_api_key', KEY);
      showDashboard();
    }})
    .catch(e => {{
      const el = document.getElementById('error');
      el.textContent = e.message;
      el.style.display = 'block';
    }});
}}

function showDashboard() {{
  document.getElementById('auth').style.display = 'none';
  document.getElementById('dashboard').style.display = 'block';
  refreshAll();
  setInterval(refreshAll, 30000);
}}

async function api(path) {{
  const r = await fetch(BASE + '/api/operator/' + path, {{
    headers: {{'Authorization': 'Bearer ' + KEY}}
  }});
  if (r.status === 401) {{
    localStorage.removeItem('als_api_key');
    location.reload();
  }}
  return r.json();
}}

async function refreshAll() {{
  try {{
    const [report, jobs, retention, audit, telemetry, margin, settlements] = await Promise.all([
      api('report'), api('jobs?limit=20'), api('retention'),
      api('audit?limit=10'), api('telemetry'), api('margin'), api('settlements'),
    ]);
    renderMetrics(report, telemetry, margin);
    renderJobs(jobs);
    renderSettlements(settlements);
    renderRetention(retention);
    renderAudit(audit);
    document.getElementById('last-refresh').textContent = 'updated ' + new Date().toLocaleTimeString();
    document.getElementById('conn-status').textContent = 'connected';
    document.getElementById('conn-status').style.color = 'var(--green)';
  }} catch(e) {{
    document.getElementById('conn-status').textContent = 'error';
    document.getElementById('conn-status').style.color = 'var(--red)';
  }}
}}

function renderMetrics(report, telemetry, margin) {{
  const s = report.summary || {{}};
  const m = margin || {{}};
  const t = telemetry || {{}};
  const latest = t.latest || {{}};
  document.getElementById('metrics').innerHTML = `
    <div class="card">
      <h2>Jobs</h2>
      <div class="metric">${{s.total_jobs || 0}}</div>
      <div class="metric-row"><span>Completed</span><span class="badge badge-green">${{s.completed_jobs || 0}}</span></div>
      <div class="metric-row"><span>Failed</span><span class="badge badge-red">${{s.failed_jobs || 0}}</span></div>
      <div class="metric-row"><span>Blocked</span><span class="badge badge-yellow">${{s.blocked_jobs || 0}}</span></div>
    </div>
    <div class="card">
      <h2>Cost & Margin</h2>
      <div class="metric">$${{(s.recorded_cost_usd || s.total_recorded_cost_usd || 0).toFixed(4)}}</div>
      <div class="metric-label">total cost</div>
      <div class="metric-row"><span>Revenue</span><span>$${{(m.total_revenue_usd || 0).toFixed(4)}}</span></div>
      <div class="metric-row"><span>Margin</span><span>$${{(m.total_margin_usd || 0).toFixed(4)}}</span></div>
      <div class="metric-row"><span>Profitable</span><span>${{m.profitable_jobs || 0}}/${{m.total_jobs || 0}}</span></div>
    </div>
    <div class="card">
      <h2>Telemetry</h2>
      <div class="metric">${{t.snapshots || 0}}</div>
      <div class="metric-label">snapshots (24h)</div>
      <div class="metric-row"><span>Avg Duration</span><span>${{(latest.avg_duration_ms || 0).toFixed(0)}}ms</span></div>
      <div class="metric-row"><span>P95 Duration</span><span>${{(latest.p95_duration_ms || 0).toFixed(0)}}ms</span></div>
      <div class="metric-row"><span>Queue</span><span>${{latest.queue_depth || 0}}</span></div>
    </div>
    <div class="card">
      <h2>System</h2>
      <div class="metric-row"><span>Deliveries</span><span>${{s.delivery_records || s.total_deliveries || 0}}</span></div>
      <div class="metric-row"><span>Approvals pending</span><span class="badge badge-yellow">${{s.pending_approvals || 0}}</span></div>
      <div class="metric-row"><span>Artifacts</span><span>${{s.total_artifacts || 0}}</span></div>
      <div class="metric-row"><span>Inbox</span><span>${{(report.inbox || []).length}}</span></div>
    </div>
  `;
}}

function renderJobs(data) {{
  const tbody = document.querySelector('#jobs-table tbody');
  const jobs = data.jobs || [];
  if (!jobs.length) {{ tbody.innerHTML = '<tr><td colspan="5" class="loading">No jobs</td></tr>'; return; }}
  tbody.innerHTML = jobs.map(j => `
    <tr>
      <td style="font-family:monospace;font-size:12px">${{j.job_id || j.id || '?'}}</td>
      <td><span class="badge badge-blue">${{j.job_kind || '?'}}</span></td>
      <td><span class="badge ${{
        j.status === 'completed' ? 'badge-green' :
        j.status === 'failed' ? 'badge-red' :
        j.status === 'blocked' ? 'badge-yellow' : 'badge-blue'
      }}">${{j.status || '?'}}</span></td>
      <td>${{j.duration_ms ? (j.duration_ms / 1000).toFixed(1) + 's' : '-'}}</td>
      <td>${{j.estimated_cost_usd ? '$$' + j.estimated_cost_usd.toFixed(4) : '-'}}</td>
    </tr>
  `).join('');
}}

function renderRetention(data) {{
  const card = document.getElementById('retention-card');
  const bs = data.by_status || {{}};
  const ts = data.table_stats || {{}};
  card.innerHTML = `
    <div class="metric-row"><span>Active</span><span class="badge badge-green">${{bs.active || 0}}</span></div>
    <div class="metric-row"><span>Expired</span><span class="badge badge-yellow">${{bs.expired || 0}}</span></div>
    <div class="metric-row"><span>Pruned</span><span class="badge badge-red">${{bs.pruned || 0}}</span></div>
    <div class="metric-row"><span>Recoverable</span><span>${{data.recoverable_records || 0}}</span></div>
    <hr style="border-color:var(--border);margin:8px 0">
    ${{Object.entries(ts).map(([k,v]) => `<div class="metric-row"><span style="font-size:11px">${{k}}</span><span>${{v}}</span></div>`).join('')}}
  `;
}}

function renderSettlements(data) {{
  const card = document.getElementById('settlements-card');
  const items = data.settlements || [];
  if (!items.length) {{
    card.innerHTML = '<span style="color:var(--text-muted)">No settlement requests.</span>';
    return;
  }}
  card.innerHTML = items.map(s => {{
    const p = s.payment || {{}};
    const actions = s.status === 'pending' ? `
      <button onclick="settlementAction('${{s.settlement_id}}','approve')" style="background:var(--green);color:#000;border:none;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:11px;margin-right:4px">Approve</button>
      <button onclick="settlementAction('${{s.settlement_id}}','deny')" style="background:var(--red);color:#fff;border:none;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:11px">Deny</button>
    ` : (s.status === 'approved' ? `
      <button onclick="settlementAction('${{s.settlement_id}}','execute')" style="background:var(--accent);color:#fff;border:none;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:11px">Execute Topup</button>
    ` : '');
    return `
      <div style="padding:8px 0;border-bottom:1px solid var(--border)">
        <div class="metric-row">
          <span style="font-family:monospace;font-size:12px">${{s.settlement_id}}</span>
          <span class="badge ${{
            s.status === 'pending' ? 'badge-yellow' :
            s.status === 'approved' ? 'badge-blue' :
            s.status === 'executed' ? 'badge-green' :
            s.status === 'denied' ? 'badge-red' : ''
          }}">${{s.status}}</span>
        </div>
        <div style="font-size:12px;color:var(--text-muted);margin:4px 0">
          ${{p.provider_id}} — $${{(p.amount_required || 0).toFixed(4)}} ${{p.currency || ''}}
        </div>
        <div>${{actions}}</div>
      </div>
    `;
  }}).join('');
}}

async function settlementAction(id, action) {{
  try {{
    const r = await fetch(BASE + '/api/operator/settlements/' + id + '/' + action, {{
      method: 'POST',
      headers: {{'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json'}},
      body: JSON.stringify({{}}),
    }});
    const data = await r.json();
    if (data.ok) {{ refreshAll(); }}
    else {{ alert(data.error || 'Action failed'); }}
  }} catch(e) {{ alert('Error: ' + e.message); }}
}}

function renderAudit(data) {{
  const card = document.getElementById('audit-card');
  const s = data.stats || {{}};
  card.innerHTML = `
    <div class="metric-row"><span>Total requests</span><span>${{s.total_requests || 0}}</span></div>
    <div class="metric-row"><span>Errors</span><span class="badge badge-red">${{s.total_errors || 0}}</span></div>
    <div class="metric-row"><span>Rate limited</span><span class="badge badge-yellow">${{s.total_rate_limited || 0}}</span></div>
    <div class="metric-row"><span>Auth failures</span><span class="badge badge-red">${{s.total_auth_failures || 0}}</span></div>
    <hr style="border-color:var(--border);margin:8px 0">
    ${{Object.entries(s.by_sender || {{}}).map(([k,v]) => `<div class="metric-row"><span style="font-size:11px">${{k}}</span><span>${{v}}</span></div>`).join('')}}
  `;
}}
</script>
</body>
</html>"""
