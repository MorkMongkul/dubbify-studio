"""The Key Monitor page (GET /gateway): one self-contained HTML string, from the Gemini Key-Pool Gateway's dashboard.
It shows keys by their alias and state only — /gateway/stats never carries a key — and escapes everything it
builds with innerHTML. A Python module rather than a data file so the packaged backend always has it."""

DASHBOARD_HTML = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gemini Key-Pool Gateway</title>
<style>
  :root {
    --bg-page: #08090d;
    --bg-sidebar: #0d0f15;
    --bg-card: #12151d;
    --bg-card-sub: #171b26;
    --bg-hover: #1c2230;
    --border: #1c212c;
    --border-subtle: #242c3c;
    --border-hover: #333f54;
    --text-primary: #f0f6fc;
    --text-secondary: #8b949e;
    --text-dim: #5c6675;
    --accent: #38bdf8;
    --accent-glow: rgba(56, 189, 248, 0.12);
    --green: #34d399;
    --green-bg: rgba(52, 211, 153, 0.1);
    --amber: #fbbf24;
    --amber-bg: rgba(251, 191, 36, 0.1);
    --red: #f87171;
    --red-bg: rgba(248, 113, 113, 0.1);
    --blue: #60a5fa;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg-page);
    color: var(--text-primary);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
    line-height: 1.5;
    display: flex;
    height: 100vh;
    overflow: hidden;
    -webkit-font-smoothing: antialiased;
  }

  /* SVG Icon Helper */
  .icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 16px;
    height: 16px;
    stroke: currentColor;
    stroke-width: 1.8;
    fill: none;
    stroke-linecap: round;
    stroke-linejoin: round;
    flex-shrink: 0;
  }
  .mono {
    font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  }

  /* Sidebar */
  #sidebar {
    width: 240px;
    background: var(--bg-sidebar);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    flex-shrink: 0;
    user-select: none;
  }
  .sidebar-header {
    padding: 18px 16px 14px 16px;
    border-bottom: 1px solid var(--border);
  }
  .brand-row {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .brand-badge {
    width: 24px;
    height: 24px;
    background: linear-gradient(135deg, #0284c7, #6366f1);
    border-radius: 6px;
    display: flex;
    align-items: center;
    justify-content: center;
    color: #fff;
    font-weight: 700;
    font-size: 12px;
  }
  .brand-name {
    font-size: 13.5px;
    font-weight: 600;
    color: var(--text-primary);
    letter-spacing: -0.2px;
  }
  .status-indicator {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    margin-top: 10px;
    font-size: 11px;
    color: var(--green);
    background: var(--green-bg);
    border: 1px solid rgba(52, 211, 153, 0.25);
    padding: 2px 8px;
    border-radius: 20px;
    font-weight: 500;
  }
  .status-dot {
    width: 6px;
    height: 6px;
    background: var(--green);
    border-radius: 50%;
    box-shadow: 0 0 6px var(--green);
  }

  .nav-group {
    flex: 1;
    padding: 12px 8px;
    list-style: none;
  }
  .nav-btn {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 12px;
    border-radius: 6px;
    cursor: pointer;
    color: var(--text-secondary);
    font-size: 12.5px;
    font-weight: 500;
    margin-bottom: 2px;
    transition: all 0.15s ease;
  }
  .nav-btn:hover {
    background: var(--bg-hover);
    color: var(--text-primary);
  }
  .nav-btn.active {
    background: var(--accent-glow);
    color: var(--accent);
    border: 1px solid rgba(56, 189, 248, 0.25);
    font-weight: 600;
  }

  .sidebar-bottom {
    padding: 14px 16px;
    border-top: 1px solid var(--border);
    background: rgba(0, 0, 0, 0.15);
  }
  .reset-widget {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 9px 11px;
  }
  .reset-label {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-secondary);
    display: flex;
    align-items: center;
    gap: 5px;
  }
  .reset-value {
    color: var(--amber);
    font-size: 12px;
    font-weight: 600;
    margin-top: 3px;
  }

  /* Main Workspace */
  #workspace {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .top-header {
    height: 52px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 24px;
    background: var(--bg-sidebar);
    flex-shrink: 0;
  }
  .view-title {
    font-size: 14px;
    font-weight: 600;
    color: var(--text-primary);
  }
  .top-actions {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .action-btn {
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    color: var(--text-secondary);
    padding: 5px 11px;
    border-radius: 5px;
    font-size: 11.5px;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    cursor: pointer;
    transition: all 0.15s ease;
  }
  .action-btn:hover {
    background: var(--bg-hover);
    color: var(--text-primary);
    border-color: var(--border-hover);
  }
  .btn-primary {
    background: #0284c7;
    border-color: #0284c7;
    color: #fff;
    font-weight: 600;
  }
  .btn-primary:hover {
    background: #0369a1;
    color: #fff;
  }

  .view-content {
    flex: 1;
    overflow-y: auto;
    padding: 22px 26px;
    display: none;
  }
  .view-content.active { display: block; }

  /* Bento Grid System */
  .bento-row {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 14px;
    margin-bottom: 16px;
  }
  .bento-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px;
    transition: border-color 0.15s ease, background-color 0.15s ease;
  }
  .bento-card:hover {
    border-color: var(--border-hover);
  }
  .bento-card-title {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-secondary);
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .bento-card-val {
    font-size: 24px;
    font-weight: 700;
    color: #fff;
    margin-top: 6px;
    letter-spacing: -0.5px;
  }
  .bento-card-desc {
    font-size: 11px;
    color: var(--text-dim);
    margin-top: 4px;
  }

  .bento-section {
    display: grid;
    grid-template-columns: 2fr 1fr;
    gap: 14px;
    margin-bottom: 16px;
  }
  .bento-full {
    grid-column: 1 / -1;
  }

  /* Bento Keys Grid */
  .bento-keys-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 12px;
    margin-top: 12px;
  }
  .bento-key-tile {
    background: var(--bg-card-sub);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 12px 14px;
    transition: all 0.15s ease;
  }
  .bento-key-tile:hover {
    border-color: var(--border-hover);
    background: var(--bg-hover);
  }
  .key-tile-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
  }
  .key-tile-name {
    font-weight: 600;
    color: #fff;
    font-size: 12.5px;
  }

  /* Micro Progress Bars */
  .progress-wrap {
    background: #090c12;
    border-radius: 3px;
    height: 6px;
    overflow: hidden;
    flex: 1;
    margin: 0 8px;
    border: 1px solid rgba(255, 255, 255, 0.04);
  }
  .progress-bar { height: 100%; transition: width 0.3s ease; }
  .bar-green { background: var(--green); }
  .bar-amber { background: var(--amber); }
  .bar-red { background: var(--red); }

  .tile-metric-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-top: 6px;
    font-size: 11px;
    color: var(--text-secondary);
  }

  /* Tables */
  .table-box {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }
  th, td {
    padding: 10px 14px;
    text-align: left;
    border-bottom: 1px solid var(--border);
  }
  th {
    background: #0f121a;
    font-size: 10.5px;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    color: var(--text-secondary);
    font-weight: 600;
  }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: rgba(255, 255, 255, 0.015); }

  /* Badges */
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px 7px;
    border-radius: 4px;
    font-size: 10.5px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.3px;
  }
  .badge-healthy { background: var(--green-bg); color: var(--green); border: 1px solid rgba(52, 211, 153, 0.3); }
  .badge-cooling { background: var(--amber-bg); color: var(--amber); border: 1px solid rgba(251, 191, 36, 0.3); }
  .badge-exhausted { background: var(--red-bg); color: var(--red); border: 1px solid rgba(248, 113, 113, 0.3); }
  .badge-dead { background: rgba(148, 163, 184, 0.1); color: #94a3b8; border: 1px solid rgba(148, 163, 184, 0.2); }
  .badge-done { background: var(--green-bg); color: var(--green); }
  .badge-queued { background: var(--amber-bg); color: var(--amber); }
  .badge-processing { background: rgba(56, 189, 248, 0.15); color: var(--accent); }
  .badge-failed { background: var(--red-bg); color: var(--red); }

  /* Inputs & Playground */
  .form-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 22px;
    max-width: 680px;
  }
  .form-label {
    display: block;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-secondary);
    margin-bottom: 6px;
    font-weight: 600;
  }
  .form-input {
    width: 100%;
    background: #0a0d13;
    border: 1px solid var(--border-subtle);
    border-radius: 5px;
    padding: 8px 12px;
    color: #fff;
    font-family: inherit;
    font-size: 12.5px;
    outline: none;
    transition: border-color 0.15s ease;
  }
  .form-input:focus { border-color: var(--accent); }

  .output-box {
    margin-top: 16px;
    background: #090c12;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 12px 14px;
    font-size: 12px;
    white-space: pre-wrap;
    max-height: 280px;
    overflow-y: auto;
  }
</style>
</head>
<body>

  <!-- Minimalist Sidebar -->
  <aside id="sidebar">
    <div class="sidebar-header">
      <div class="brand-row">
        <div class="brand-badge">
          <svg class="icon" viewBox="0 0 24 24"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>
        </div>
        <div class="brand-name">Gemini Gateway</div>
      </div>
      <div class="status-indicator">
        <span class="status-dot"></span>
        <span id="pool-active-label">Pool Active</span>
      </div>
    </div>

    <ul class="nav-group">
      <li class="nav-btn active" onclick="navigate('overview')">
        <svg class="icon" viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="14" y="14" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect></svg>
        <span>Overview</span>
      </li>
      <li class="nav-btn" onclick="navigate('keys')">
        <svg class="icon" viewBox="0 0 24 24"><path d="m21 2-2 2m-6 6 6-6m-6 6a5.5 5.5 0 1 1-7.78 7.78 5.5 5.5 0 0 1 7.78-7.78Zm0 0L19 4l2 2-2 2-2-2"></path></svg>
        <span>Keys &amp; Quotas</span>
      </li>
      <li class="nav-btn" onclick="navigate('jobs')">
        <svg class="icon" viewBox="0 0 24 24"><path d="m16 13 5.223 3.482a.5.5 0 0 0 .777-.416V7.87a.5.5 0 0 0-.752-.432L16 10.5"></path><rect x="2" y="6" width="14" height="12" rx="2"></rect></svg>
        <span>Dubbing Jobs</span>
      </li>
      <li class="nav-btn" onclick="navigate('test')">
        <svg class="icon" viewBox="0 0 24 24"><polyline points="4 17 10 11 4 5"></polyline><line x1="12" y1="19" x2="20" y2="19"></line></svg>
        <span>Live Playground</span>
      </li>
    </ul>

    <div class="sidebar-bottom">
      <div class="reset-widget">
        <div class="reset-label">
          <svg class="icon" viewBox="0 0 24 24" style="width:12px;height:12px;"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
          <span>Midnight PT Reset</span>
        </div>
        <div class="reset-value mono" id="reset-timer">Calculating...</div>
      </div>
    </div>
  </aside>

  <!-- Workspace Area -->
  <main id="workspace">
    <header class="top-header">
      <div class="view-title" id="page-title">Resource Observability</div>
      <div class="top-actions">
        <span class="mono" style="font-size:11px; color:var(--text-dim);" id="last-sync-time">Syncing...</span>
        <button class="action-btn" onclick="fetchData()">
          <svg class="icon" viewBox="0 0 24 24"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"></path><path d="M3 3v5h5"></path><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"></path><path d="M16 21h5v-5"></path></svg>
          <span>Refresh</span>
        </button>
      </div>
    </header>

    <!-- Bento Tab 1: Overview -->
    <div id="pane-overview" class="view-content active">
      <!-- Bento KPI Top Row -->
      <div class="bento-row">
        <div class="bento-card">
          <div class="bento-card-title">
            <svg class="icon" viewBox="0 0 24 24"><path d="m21 2-2 2m-6 6 6-6m-6 6a5.5 5.5 0 1 1-7.78 7.78 5.5 5.5 0 0 1 7.78-7.78Zm0 0L19 4l2 2-2 2-2-2"></path></svg>
            <span>Healthy Keys</span>
          </div>
          <div class="bento-card-val mono" id="kpi-keys">0 / 0</div>
          <div class="bento-card-desc">Active in pool rotation</div>
        </div>

        <div class="bento-card">
          <div class="bento-card-title">
            <svg class="icon" viewBox="0 0 24 24"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>
            <span>Requests Today</span>
          </div>
          <div class="bento-card-val mono" id="kpi-requests">0</div>
          <div class="bento-card-desc">Total upstream Gemini calls</div>
        </div>

        <div class="bento-card">
          <div class="bento-card-title">
            <svg class="icon" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="6" x2="12" y2="12"></line><line x1="12" y1="12" x2="16" y2="14"></line></svg>
            <span>Tokens Today</span>
          </div>
          <div class="bento-card-val mono" id="kpi-tokens">0</div>
          <div class="bento-card-desc">Cumulative prompt + output</div>
        </div>

        <div class="bento-card">
          <div class="bento-card-title">
            <svg class="icon" viewBox="0 0 24 24"><path d="m16 13 5.223 3.482a.5.5 0 0 0 .777-.416V7.87a.5.5 0 0 0-.752-.432L16 10.5"></path><rect x="2" y="6" width="14" height="12" rx="2"></rect></svg>
            <span>Videos Dubbed</span>
          </div>
          <div class="bento-card-val mono" id="kpi-videos">0</div>
          <div class="bento-card-desc">Completed episode workflows</div>
        </div>
      </div>

      <!-- Bento Main Section: Key Headroom Tiles -->
      <div class="bento-card bento-full">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <div class="bento-card-title">
            <svg class="icon" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"></rect><line x1="3" y1="9" x2="21" y2="9"></line><line x1="9" y1="21" x2="9" y2="9"></line></svg>
            <span>Key Pool Headroom &amp; Capacity</span>
          </div>
          <div class="mono" style="font-size:11px; color:var(--text-dim);">Rotates automatically across healthy keys</div>
        </div>

        <div class="bento-keys-grid" id="bento-keys-container">
          <!-- Rendered dynamically -->
        </div>
      </div>
    </div>

    <!-- Bento Tab 2: Keys & Detailed Quotas -->
    <div id="pane-keys" class="view-content">
      <div class="table-box">
        <table>
          <thead>
            <tr>
              <th>Alias</th>
              <th>Status</th>
              <th>RPM Used / Cap</th>
              <th>RPD Used / Cap</th>
              <th>TPD Used / Cap</th>
              <th>Headroom Score</th>
              <th>Latency Ping</th>
            </tr>
          </thead>
          <tbody id="keys-tbody">
            <tr><td colspan="7" class="mono" style="text-align:center; color:var(--text-dim);">Loading key pool...</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Bento Tab 3: Dubbing Jobs Queue -->
    <div id="pane-jobs" class="view-content">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
        <div style="font-size:13px; font-weight:600; color:#fff;">Recent Translation Jobs (Last 20)</div>
        <input type="text" id="jobs-search-input" placeholder="Search by Video ID..." class="form-input" style="width: 200px; padding: 5px 10px; font-size: 11.5px;" oninput="filterJobsList()">
      </div>

      <div class="table-box">
        <table>
          <thead>
            <tr>
              <th>Job ID</th>
              <th>Video ID</th>
              <th>Lang</th>
              <th>Lines</th>
              <th>Status</th>
              <th>Failed Lines</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody id="jobs-tbody">
            <tr><td colspan="7" class="mono" style="text-align:center; color:var(--text-dim);">No dubbing jobs yet.</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Bento Tab 4: Live Playground -->
    <div id="pane-test" class="view-content">
      <div class="form-card">
        <div style="font-size:14px; font-weight:600; color:#fff; margin-bottom:4px;">Live Subtitle Translation Playground</div>
        <div style="font-size:12px; color:var(--text-secondary); margin-bottom:16px;">Test the gateway load balancer and index preservation live.</div>

        <div style="margin-bottom:14px;">
          <label class="form-label">Subtitle Line</label>
          <input type="text" id="play-text" class="form-input" value="Welcome to the dubbing workflow with Gemini key pool.">
        </div>

        <div style="margin-bottom:18px;">
          <label class="form-label">Target Language Code</label>
          <select id="play-lang" class="form-input" style="width: 160px;">
            <option value="de">German (de)</option>
            <option value="km">Khmer (km)</option>
            <option value="es">Spanish (es)</option>
            <option value="fr">French (fr)</option>
            <option value="zh">Chinese (zh)</option>
            <option value="ja">Japanese (ja)</option>
          </select>
        </div>

        <button class="action-btn btn-primary" onclick="executePlaygroundTest()">
          <svg class="icon" viewBox="0 0 24 24"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>
          <span>Translate Segment</span>
        </button>

        <div class="output-box mono" id="play-output" style="display:none;"></div>
      </div>
    </div>

  </main>

  <script>
    let globalStats = null;

    function navigate(tab) {
      document.querySelectorAll('.view-content').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.nav-btn').forEach(el => el.classList.remove('active'));

      const pane = document.getElementById('pane-' + tab);
      if (pane) pane.classList.add('active');

      const titles = {
        'overview': 'Resource Observability',
        'keys': 'API Keys & Headroom Quotas',
        'jobs': 'Dubbing Jobs & Queue',
        'test': 'Translation Playground'
      };
      document.getElementById('page-title').innerText = titles[tab] || 'Gateway';

      const buttons = document.querySelectorAll('.nav-btn');
      if (tab === 'overview') buttons[0].classList.add('active');
      else if (tab === 'keys') buttons[1].classList.add('active');
      else if (tab === 'jobs') buttons[2].classList.add('active');
      else if (tab === 'test') buttons[3].classList.add('active');
    }

    function esc(v) {
      return String(v === null || v === undefined ? '' : v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }

    function renderProgressBar(used, cap) {
      const pct = cap > 0 ? Math.min(100, Math.round((used / cap) * 100)) : 0;
      let barClass = 'bar-green';
      if (pct >= 90) barClass = 'bar-red';
      else if (pct >= 70) barClass = 'bar-amber';

      return `
        <div class="progress-wrap">
          <div class="progress-bar ${barClass}" style="width: ${pct}%"></div>
        </div>
        <span class="mono">${esc(used)} / ${cap > 0 ? esc(cap) : '∞'}</span>
      `;
    }

    async function fetchData() {
      try {
        const res = await fetch('/gateway/stats');
        if (!res.ok) return;
        const data = await res.json();
        globalStats = data;

        document.getElementById('last-sync-time').innerText = 'Synced: ' + new Date().toLocaleTimeString();

        // Midnight Pacific Countdown
        if (data.next_reset_utc) {
          const resetTime = new Date(data.next_reset_utc);
          const diff = resetTime - new Date();
          if (diff > 0) {
            const h = Math.floor(diff / 3600000);
            const m = Math.floor((diff % 3600000) / 60000);
            document.getElementById('reset-timer').innerText = `${h}h ${m}m (${resetTime.toUTCString().slice(17, 22)} UTC)`;
          } else {
            document.getElementById('reset-timer').innerText = 'Resetting...';
          }
        }

        // Top KPIs
        const tot = data.totals || {};
        document.getElementById('kpi-keys').innerText = `${tot.keys_available || 0} / ${tot.total_keys || 0}`;
        document.getElementById('kpi-requests').innerText = (tot.requests_today || 0).toLocaleString();
        document.getElementById('kpi-tokens').innerText = (tot.tokens_today || 0).toLocaleString();
        document.getElementById('kpi-videos').innerText = (tot.videos_translated_today || 0).toLocaleString();

        // Bento Key Tiles (Overview Tab)
        const bentoGrid = document.getElementById('bento-keys-container');
        if (data.keys && data.keys.length > 0) {
          bentoGrid.innerHTML = data.keys.map(k => {
            return `
              <div class="bento-key-tile">
                <div class="key-tile-header">
                  <span class="key-tile-name mono">${esc(k.alias)}</span>
                  <span class="badge badge-${esc(k.state)}">${esc(k.state)}</span>
                </div>
                <div class="tile-metric-row">
                  <span>RPM</span>
                  <div style="display:flex; align-items:center; flex:1;">
                    ${renderProgressBar(k.rpm_used, k.rpm_cap)}
                  </div>
                </div>
                <div class="tile-metric-row">
                  <span>RPD</span>
                  <div style="display:flex; align-items:center; flex:1;">
                    ${renderProgressBar(k.rpd_used, k.rpd_cap)}
                  </div>
                </div>
                <div class="tile-metric-row" style="margin-top:8px; padding-top:6px; border-top:1px solid rgba(255,255,255,0.05);">
                  <span>Headroom</span>
                  <span class="mono" style="color:var(--accent); font-weight:600;">${k.headroom !== undefined ? esc(k.headroom) : '-'}</span>
                </div>
              </div>
            `;
          }).join('');
        } else {
          bentoGrid.innerHTML = '<div class="mono" style="color:var(--text-dim);">No API keys active</div>';
        }

        // Keys Table (Keys Tab)
        const keysTbody = document.getElementById('keys-tbody');
        if (data.keys && data.keys.length > 0) {
          keysTbody.innerHTML = data.keys.map(k => {
            return `
              <tr>
                <td class="mono"><strong>${esc(k.alias)}</strong></td>
                <td><span class="badge badge-${esc(k.state)}">${esc(k.state)}</span></td>
                <td><div style="display:flex; align-items:center;">${renderProgressBar(k.rpm_used, k.rpm_cap)}</div></td>
                <td><div style="display:flex; align-items:center;">${renderProgressBar(k.rpd_used, k.rpd_cap)}</div></td>
                <td><div style="display:flex; align-items:center;">${renderProgressBar(k.tpd_used, k.tpd_cap)}</div></td>
                <td class="mono" style="color:var(--accent); font-weight:600;">${k.headroom !== undefined ? esc(k.headroom) : '-'}</td>
                <td>
                  <button class="action-btn" id="btn-ping-${esc(k.alias)}" style="padding:2px 7px;" onclick="pingKeyTest('${esc(k.alias)}')">
                    <svg class="icon" viewBox="0 0 24 24" style="width:11px;height:11px;"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>
                    <span>Ping</span>
                  </button>
                  <span class="mono" id="ping-res-${esc(k.alias)}" style="margin-left:6px; font-size:11px;"></span>
                </td>
              </tr>
            `;
          }).join('');
        }

        // Jobs Table
        renderJobs(data.recent_jobs || []);

      } catch (e) {
        console.error('Fetch error:', e);
      }
    }

    function renderJobs(jobs) {
      const tbody = document.getElementById('jobs-tbody');
      if (jobs.length > 0) {
        tbody.innerHTML = jobs.map(j => {
          const failed = (j.failed_indices && j.failed_indices.length > 0)
            ? `<span class="mono" style="color:var(--red)">[${esc(j.failed_indices.join(', '))}]</span>`
            : '<span class="mono" style="color:var(--text-dim);">-</span>';
          const time = j.created_at ? new Date(j.created_at * 1000).toLocaleTimeString() : '-';
          return `
            <tr>
              <td class="mono"><code>${esc(j.job_id)}</code></td>
              <td>${esc(j.video_id || '-')}</td>
              <td class="mono">${j.target_lang ? esc(j.target_lang.toUpperCase()) : '-'}</td>
              <td class="mono">${esc(j.line_count || 0)}</td>
              <td><span class="badge badge-${esc(j.status)}">${esc(j.status)}</span></td>
              <td>${failed}</td>
              <td class="mono" style="color:var(--text-dim);">${time}</td>
            </tr>
          `;
        }).join('');
      } else {
        tbody.innerHTML = '<tr><td colspan="7" class="mono" style="text-align:center; color:var(--text-dim);">No dubbing jobs yet.</td></tr>';
      }
    }

    function filterJobsList() {
      const q = (document.getElementById('jobs-search-input').value || '').toLowerCase();
      if (!globalStats || !globalStats.recent_jobs) return;
      const res = globalStats.recent_jobs.filter(j =>
        (j.job_id && j.job_id.toLowerCase().includes(q)) ||
        (j.video_id && j.video_id.toLowerCase().includes(q))
      );
      renderJobs(res);
    }

    async function pingKeyTest(alias) {
      const btn = document.getElementById('btn-ping-' + alias);
      const span = document.getElementById('ping-res-' + alias);
      if (btn) btn.disabled = true;
      if (span) span.innerText = '...';

      try {
        const res = await fetch(`/gateway/keys/${encodeURIComponent(alias)}/test`, { method: 'POST' });
        const d = await res.json();
        if (d.ok) {
          span.innerHTML = `<span style="color:var(--green)">✓ ${esc(d.latency_ms)}ms</span>`;
        } else {
          span.innerHTML = `<span style="color:var(--red)">✗ ${esc(d.message)}</span>`;
        }
      } catch (err) {
        span.innerHTML = `<span style="color:var(--red)">err</span>`;
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    async function executePlaygroundTest() {
      const txt = document.getElementById('play-text').value;
      const lang = document.getElementById('play-lang').value;
      const out = document.getElementById('play-output');
      out.style.display = 'block';
      out.innerText = 'Executing via load-balanced key pool...';

      const t0 = performance.now();
      try {
        const resp = await fetch('/gateway/translate?wait=true', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            video_id: 'bento_playground',
            target_lang: lang,
            source_lang: 'en',
            segments: [{ index: 0, text: txt }]
          })
        });
        const elapsed = Math.round(performance.now() - t0);
        const data = await resp.json();
        out.innerText = `Latency: ${elapsed}ms | Status: ${data.status}\n\n${JSON.stringify(data.segments || data, null, 2)}`;
        fetchData();
      } catch (err) {
        out.innerText = 'Request error: ' + err;
      }
    }

    // Auto-poll every 5 seconds
    fetchData();
    setInterval(fetchData, 5000);
  </script>
</body>
</html>
'''
