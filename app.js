'use strict';

// ── Helpers ──────────────────────────────────────────────────────────────────

const fmt = {
  price: v => v != null ? `$${Number(v).toFixed(2)}` : '—',
  pct: v => v != null ? `${v >= 0 ? '+' : ''}${Number(v).toFixed(1)}%` : '—',
  score: v => v != null ? Math.round(v) : '—',
  pe: v => v != null && v > 0 ? `${Number(v).toFixed(1)}x` : '—',
  rsi: v => v != null ? Number(v).toFixed(1) : '—',
};

function scoreColor(score) {
  if (score >= 75) return 'var(--green)';
  if (score >= 60) return '#86efac';
  if (score >= 40) return 'var(--amber)';
  if (score >= 25) return '#fca5a5';
  return 'var(--red)';
}

function scoreChipClass(score) {
  if (score >= 75) return 'high';
  if (score >= 50) return 'mid';
  return 'low';
}

function signalLabel(score) {
  if (score >= 75) return ['Strong Buy', 'chip-sbuy'];
  if (score >= 60) return ['Buy', 'chip-buy'];
  if (score >= 40) return ['Hold', 'chip-hold'];
  if (score >= 25) return ['Sell', 'chip-sell'];
  return ['Strong Sell', 'chip-ssell'];
}

function trendArrow(rank, prevRank) {
  const delta = prevRank - rank;
  if (delta > 0) return `<span class="trend-up">▲${delta}</span>`;
  if (delta < 0) return `<span class="trend-dn">▼${Math.abs(delta)}</span>`;
  return `<span class="trend-flat">—</span>`;
}

function sectorColor(score) {
  const s = Math.round(score);
  if (s >= 70) return { bg: '#14532d', text: '#dcfce7' };
  if (s >= 60) return { bg: '#052e16', text: '#86efac' };
  if (s >= 50) return { bg: '#1c1917', text: '#fde68a' };
  if (s >= 40) return { bg: '#292524', text: '#fdba74' };
  return { bg: '#450a0a', text: '#fca5a5' };
}

function relTime(isoStr) {
  const diff = Math.floor((Date.now() - new Date(isoStr)) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

// ── Score ring (Chart.js doughnut) ───────────────────────────────────────────

let ringChart = null;

function drawScoreRing(score) {
  if (typeof Chart === 'undefined') return;
  const ctx = document.getElementById('score-ring').getContext('2d');
  const color = scoreColor(score);
  if (ringChart) {
    ringChart.data.datasets[0].data = [score, 100 - score];
    ringChart.data.datasets[0].backgroundColor = [color, 'rgba(255,255,255,0.05)'];
    ringChart.update();
    return;
  }
  ringChart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      datasets: [{
        data: [score, 100 - score],
        backgroundColor: [color, 'rgba(255,255,255,0.05)'],
        borderWidth: 0,
        borderRadius: 4,
      }],
    },
    options: {
      cutout: '72%',
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      animation: { duration: 600 },
    },
  });
}

// ── Tab switching ─────────────────────────────────────────────────────────────

document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(s => s.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add('active');
  });
});

// ── Market status badge ───────────────────────────────────────────────────────

function setMarketBadge(status) {
  const badge = document.getElementById('market-badge');
  badge.className = 'badge';
  if (status === 'open') { badge.classList.add('badge-open'); badge.textContent = 'OPEN'; }
  else if (status === 'pre-market') { badge.classList.add('badge-pre'); badge.textContent = 'PRE'; }
  else if (status === 'after-hours') { badge.classList.add('badge-after'); badge.textContent = 'AH'; }
  else { badge.classList.add('badge-closed'); badge.textContent = 'CLOSED'; }
}

// ── Top Pick tab ──────────────────────────────────────────────────────────────

function renderTopPick(pick) {
  document.getElementById('hero-ticker').textContent = pick.ticker;
  document.getElementById('hero-name').textContent = pick.name;
  document.getElementById('hero-sector').textContent = pick.sector;
  document.getElementById('hero-score').textContent = fmt.score(pick.compositeScore);
  document.getElementById('hero-score').style.color = scoreColor(pick.compositeScore);
  document.getElementById('hero-price').textContent = fmt.price(pick.price);

  const change = pick.changePct;
  const chEl = document.getElementById('hero-change');
  chEl.textContent = fmt.pct(change);
  chEl.className = `price-change ${change > 0 ? 'pos' : change < 0 ? 'neg' : 'neu'}`;

  document.getElementById('hero-thesis').textContent = pick.thesis || 'Analysis unavailable.';

  drawScoreRing(pick.compositeScore);

  const bars = [
    { id: 'tech', val: pick.technicalScore },
    { id: 'fund', val: pick.fundamentalScore },
    { id: 'mom', val: pick.momentumScore },
    { id: 'sec', val: pick.sectorScore },
  ];
  bars.forEach(({ id, val }) => {
    document.getElementById(`bar-${id}`).style.width = `${Math.min(100, val || 0)}%`;
    document.getElementById(`val-${id}`).textContent = fmt.score(val);
    document.getElementById(`val-${id}`).style.color = scoreColor(val);
  });

  const sig = pick.signals || {};
  const signals = [
    { label: 'RSI', value: fmt.rsi(sig.rsi), sub: sig.rsi < 30 ? 'Oversold' : sig.rsi > 70 ? 'Overbought' : 'Neutral', color: sig.rsi < 40 ? 'var(--green)' : sig.rsi > 65 ? 'var(--red)' : 'var(--text)' },
    { label: 'MACD', value: sig.macd === 'bullish' ? '▲ Bull' : '▼ Bear', sub: 'Signal', color: sig.macd === 'bullish' ? 'var(--green)' : 'var(--red)' },
    { label: 'P/E', value: fmt.pe(sig.pe), sub: sig.sectorPe ? `Sect: ${fmt.pe(sig.sectorPe)}` : 'vs Sector', color: sig.pe && sig.sectorPe && sig.pe < sig.sectorPe ? 'var(--green)' : 'var(--text)' },
    { label: '3M Mom', value: fmt.pct(sig.momentum3m), sub: '3-Month', color: (sig.momentum3m || 0) >= 0 ? 'var(--green)' : 'var(--red)' },
    { label: 'EPS Growth', value: sig.epsGrowth != null ? fmt.pct(sig.epsGrowth * 100) : '—', sub: 'YoY', color: (sig.epsGrowth || 0) >= 0 ? 'var(--green)' : 'var(--red)' },
    { label: 'Vol Ratio', value: sig.volRatio != null ? `${Number(sig.volRatio).toFixed(2)}x` : '—', sub: 'vs 20D avg', color: (sig.volRatio || 0) > 1 ? 'var(--cyan)' : 'var(--muted)' },
  ];

  const grid = document.getElementById('signal-grid');
  grid.innerHTML = signals.map(s => `
    <div class="signal-card">
      <div class="signal-label">${s.label}</div>
      <div class="signal-value" style="color:${s.color}">${s.value}</div>
      <div class="signal-sub">${s.sub}</div>
    </div>
  `).join('');
}

function renderRunnerUps(stocks) {
  const top5 = stocks.slice(1, 5);
  const container = document.getElementById('runner-up-list');
  container.innerHTML = top5.map(s => {
    const [label, cls] = signalLabel(s.compositeScore);
    return `
      <div class="runner-up-card">
        <div class="ru-left">
          <div class="ru-ticker">${s.ticker}</div>
          <div class="ru-name">${s.name}</div>
        </div>
        <div class="ru-right">
          <div class="score-chip ${scoreChipClass(s.compositeScore)}">${fmt.score(s.compositeScore)}/100</div>
          <span class="chip ${cls}">${label}</span>
        </div>
      </div>
    `;
  }).join('');
}

// ── Leaderboard tab ───────────────────────────────────────────────────────────

function renderLeaderboard(stocks) {
  const top20 = stocks.slice(0, 20);
  const tbody = document.getElementById('leaderboard-body');
  tbody.innerHTML = top20.map(s => {
    const [label, cls] = signalLabel(s.compositeScore);
    return `
      <tr>
        <td>${s.rank}</td>
        <td class="ticker-cell">${s.ticker}</td>
        <td>${s.sector.replace('Consumer ', 'Con. ').replace('Communication Services', 'Comms')}</td>
        <td>${fmt.price(s.price)}</td>
        <td class="score-cell" style="color:${scoreColor(s.compositeScore)}">${fmt.score(s.compositeScore)}</td>
        <td>${fmt.score(s.technicalScore)}</td>
        <td>${fmt.score(s.fundamentalScore)}</td>
        <td>${fmt.score(s.momentumScore)}</td>
        <td>${trendArrow(s.rank, s.prevRank)}</td>
      </tr>
    `;
  }).join('');
}

// ── Screener tab ──────────────────────────────────────────────────────────────

let screenerData = [];
let screenerPage = 1;
const PAGE_SIZE = 30;

function applyFilters() {
  const sector = document.getElementById('filter-sector').value;
  const minScore = parseFloat(document.getElementById('filter-signal').value) || 0;
  const search = document.getElementById('filter-search').value.trim().toUpperCase();
  const sortKey = document.getElementById('sort-col').value;

  let filtered = screenerData.filter(s => {
    if (sector && s.sector !== sector) return false;
    if (s.compositeScore < minScore) return false;
    if (search && !s.ticker.includes(search) && !s.name.toUpperCase().includes(search)) return false;
    return true;
  });

  const sortMap = {
    rank: (a, b) => a.rank - b.rank,
    ticker: (a, b) => a.ticker.localeCompare(b.ticker),
    rsi: (a, b) => (a.signals.rsi || 99) - (b.signals.rsi || 99),
    momentum3m: (a, b) => (b.signals.momentum3m || 0) - (a.signals.momentum3m || 0),
    pe: (a, b) => ((a.signals.pe || 9999) - (b.signals.pe || 9999)),
  };
  filtered.sort(sortMap[sortKey] || sortMap.rank);

  screenerPage = 1;
  renderScreenerPage(filtered);
}

function renderScreenerPage(filtered) {
  const total = filtered.length;
  const pages = Math.ceil(total / PAGE_SIZE);
  const pageData = filtered.slice((screenerPage - 1) * PAGE_SIZE, screenerPage * PAGE_SIZE);

  const tbody = document.getElementById('screener-body');
  tbody.innerHTML = pageData.map(s => {
    const [label, cls] = signalLabel(s.compositeScore);
    const sig = s.signals || {};
    return `
      <tr>
        <td>${s.rank}</td>
        <td class="ticker-cell">${s.ticker}</td>
        <td style="max-width:140px;overflow:hidden;text-overflow:ellipsis">${s.name}</td>
        <td>${(s.sector || '').split(' ').slice(0, 2).join(' ')}</td>
        <td>${fmt.price(s.price)}</td>
        <td class="score-cell" style="color:${scoreColor(s.compositeScore)}">${fmt.score(s.compositeScore)}</td>
        <td>${fmt.rsi(sig.rsi)}</td>
        <td><span class="chip ${sig.macd === 'bullish' ? 'chip-bull' : 'chip-bear'}">${sig.macd === 'bullish' ? '▲' : '▼'}</span></td>
        <td>${fmt.pe(sig.pe)}</td>
        <td style="color:${(sig.momentum3m || 0) >= 0 ? 'var(--green)' : 'var(--red)'}">${fmt.pct(sig.momentum3m)}</td>
        <td><span class="chip ${cls}">${label}</span></td>
      </tr>
    `;
  }).join('');

  // Pagination
  const pg = document.getElementById('screener-pagination');
  pg.innerHTML = '';
  for (let i = 1; i <= pages; i++) {
    const btn = document.createElement('button');
    btn.className = `page-btn${i === screenerPage ? ' active' : ''}`;
    btn.textContent = i;
    btn.addEventListener('click', () => {
      screenerPage = i;
      renderScreenerPage(filtered);
    });
    pg.appendChild(btn);
  }
}

function initScreener(stocks, sectors) {
  screenerData = stocks;
  const sel = document.getElementById('filter-sector');
  [...new Set(stocks.map(s => s.sector))].sort().forEach(sec => {
    const opt = document.createElement('option');
    opt.value = sec;
    opt.textContent = sec;
    sel.appendChild(opt);
  });

  ['filter-sector', 'filter-signal', 'sort-col'].forEach(id =>
    document.getElementById(id).addEventListener('change', applyFilters)
  );
  document.getElementById('filter-search').addEventListener('input', applyFilters);

  applyFilters();
}

// ── Sectors tab ───────────────────────────────────────────────────────────────

function renderSectors(sectors) {
  const grid = document.getElementById('sector-grid');
  const tbody = document.getElementById('sector-body');

  const sorted = Object.entries(sectors).sort((a, b) => b[1].avgScore - a[1].avgScore);

  grid.innerHTML = sorted.map(([name, data]) => {
    const c = sectorColor(data.avgScore);
    return `
      <div class="sector-card" style="background:${c.bg};color:${c.text}">
        <div class="sector-card-name">${name}</div>
        <div class="sector-card-score">${fmt.score(data.avgScore)}</div>
        <div class="sector-card-top">${data.etf} · Top: ${data.topStock}</div>
      </div>
    `;
  }).join('');

  tbody.innerHTML = sorted.map(([name, data]) => `
    <tr>
      <td>${name}</td>
      <td>${data.etf}</td>
      <td style="color:${scoreColor(data.avgScore)}">${fmt.score(data.avgScore)}</td>
      <td style="color:${(data.etfReturn1m || 0) >= 0 ? 'var(--green)' : 'var(--red)'}">${fmt.pct(data.etfReturn1m)}</td>
      <td style="color:${(data.etfReturn3m || 0) >= 0 ? 'var(--green)' : 'var(--red)'}">${fmt.pct(data.etfReturn3m)}</td>
      <td class="ticker-cell">${data.topStock}</td>
    </tr>
  `).join('');
}

// ── EOD tab ───────────────────────────────────────────────────────────────────

function renderEOD() {
  if (typeof EOD_DATA === 'undefined' || !EOD_DATA) {
    document.getElementById('eod-no-data').style.display = 'block';
    return;
  }
  const d = EOD_DATA;
  document.getElementById('eod-date').textContent = `EOD Summary — ${d.date}`;

  // Breadth
  const b = d.breadth || {};
  const total = (b.strongBuy || 0) + (b.buy || 0) + (b.hold || 0) + (b.sell || 0) + (b.strongSell || 0) || 1;
  const bEl = document.getElementById('eod-breadth');
  bEl.innerHTML = `
    <div class="breadth-label">Market Breadth — Avg Score: ${b.avgComposite || '—'}</div>
    <div class="breadth-bar">
      <div class="breadth-seg" style="width:${((b.strongBuy||0)/total*100).toFixed(1)}%;background:#14532d;color:#86efac">${b.strongBuy||0}</div>
      <div class="breadth-seg" style="width:${((b.buy||0)/total*100).toFixed(1)}%;background:#052e16;color:#bbf7d0">${b.buy||0}</div>
      <div class="breadth-seg" style="width:${((b.hold||0)/total*100).toFixed(1)}%;background:#292524;color:#fde68a">${b.hold||0}</div>
      <div class="breadth-seg" style="width:${((b.sell||0)/total*100).toFixed(1)}%;background:#450a0a80;color:#fca5a5">${b.sell||0}</div>
      <div class="breadth-seg" style="width:${((b.strongSell||0)/total*100).toFixed(1)}%;background:#450a0a;color:#f87171">${b.strongSell||0}</div>
    </div>
    <div class="breadth-stats">
      <div class="breadth-stat"><div class="breadth-stat-val" style="color:var(--green)">${b.strongBuy||0}</div><div class="breadth-stat-lbl">Str Buy</div></div>
      <div class="breadth-stat"><div class="breadth-stat-val" style="color:#86efac">${b.buy||0}</div><div class="breadth-stat-lbl">Buy</div></div>
      <div class="breadth-stat"><div class="breadth-stat-val" style="color:var(--amber)">${b.hold||0}</div><div class="breadth-stat-lbl">Hold</div></div>
      <div class="breadth-stat"><div class="breadth-stat-val" style="color:#fca5a5">${b.sell||0}</div><div class="breadth-stat-lbl">Sell</div></div>
      <div class="breadth-stat"><div class="breadth-stat-val" style="color:var(--red)">${b.strongSell||0}</div><div class="breadth-stat-lbl">Str Sell</div></div>
    </div>
  `;

  function moverRow(s, positive) {
    const delta = s.scoreDelta || 0;
    return `
      <div class="eod-mover">
        <div>
          <div class="eod-mover-ticker">${s.ticker}</div>
          <div style="font-size:10px;color:var(--muted)">${s.sector}</div>
        </div>
        <div class="eod-mover-delta" style="color:${positive ? 'var(--green)' : 'var(--red)'}">
          ${delta >= 0 ? '+' : ''}${delta.toFixed(1)} pts
        </div>
      </div>
    `;
  }

  document.getElementById('eod-risers').innerHTML = (d.scoreRisers || []).map(s => moverRow(s, true)).join('');
  document.getElementById('eod-fallers').innerHTML = (d.scoreFallers || []).map(s => moverRow(s, false)).join('');

  const tp = d.topPick || {};
  document.getElementById('eod-top-pick').innerHTML = `
    <div class="eod-top-ticker">${tp.ticker || '—'} <span style="font-size:14px;color:var(--muted)">${tp.name || ''}</span></div>
    <div class="eod-top-score">Score: ${fmt.score(tp.compositeScore)}/100 · ${tp.sector || ''}</div>
    <div class="eod-thesis">${tp.thesis || ''}</div>
  `;
}

// ── Boot ──────────────────────────────────────────────────────────────────────

(function init() {
  if (typeof DATA === 'undefined' || !DATA) {
    document.getElementById('main').innerHTML = `
      <div style="text-align:center;padding:48px;color:var(--muted)">
        <div style="font-size:32px;margin-bottom:12px">📊</div>
        <div>No data yet — run sp500_analyser.py to generate data.js</div>
      </div>
    `;
    return;
  }

  setMarketBadge(DATA.marketStatus);
  document.getElementById('last-updated').textContent = relTime(DATA.lastUpdated);
  setInterval(() => document.getElementById('last-updated').textContent = relTime(DATA.lastUpdated), 30000);

  try { if (DATA.topPick) renderTopPick(DATA.topPick); } catch (e) { console.error('Top pick render failed:', e); }
  try {
    if (DATA.stocks) {
      renderRunnerUps(DATA.stocks);
      renderLeaderboard(DATA.stocks);
      initScreener(DATA.stocks, DATA.sectors || {});
    }
  } catch (e) { console.error('Stocks render failed:', e); }
  try { if (DATA.sectors) renderSectors(DATA.sectors); } catch (e) { console.error('Sectors render failed:', e); }
  try { renderEOD(); } catch (e) { console.error('EOD render failed:', e); }

  // Register service worker
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js').catch(() => {});
  }
})();
