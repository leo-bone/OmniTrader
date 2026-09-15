/* OmniTrader web console — vanilla JS, zero CDN dependencies. */
'use strict';

const $ = (id) => document.getElementById(id);
const state = { token: localStorage.getItem('ot_token') || '', user: null, space: null, strategies: [] };

/* ============================ helpers ============================ */
function fmt(n, d = 2) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—';
  return Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}
function pct(v, d = 2) { return fmt(v, d) + '%'; }
function cls(v) { return Number(v) > 0 ? 'pos' : (Number(v) < 0 ? 'neg' : ''); }
function shortHash(s) { return String(s).slice(0, 8); }

async function api(path, opts = {}) {
  const headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
  if (state.token) headers['Authorization'] = 'Bearer ' + state.token;
  const res = await fetch(path, Object.assign({}, opts, {
    headers, body: opts.body ? JSON.stringify(opts.body) : undefined,
  }));
  let data = null;
  try { data = await res.json(); } catch (_) { /* non-json */ }
  if (res.status === 401) { logout(false); throw new Error('登录已过期'); }
  if (!res.ok) throw new Error((data && data.error) || ('HTTP ' + res.status));
  return data;
}

/* ============================ auth ============================ */
async function login(u, p) {
  const data = await fetch('/api/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: u, password: p }),
  }).then(r => r.json().catch(() => ({})));

  if (!data.token) {
    throw new Error(data.error || '登录失败');
  }
  state.token = data.token;
  state.user = { username: data.username, role: data.role };
  localStorage.setItem('ot_token', data.token);
  $('login-view').classList.add('hidden');
  $('app-view').classList.remove('hidden');
  $('me-name').textContent = data.username + (data.role === 'admin' ? ' · 管理员' : '');
  await boot();
}

function logout(clearRemote = true) {
  const token = state.token;
  state.token = ''; state.user = null;
  localStorage.removeItem('ot_token');
  if (clearRemote && token) fetch('/api/auth/logout', {
    method: 'POST', headers: { 'Authorization': 'Bearer ' + token },
  }).catch(() => {});
  $('app-view').classList.add('hidden');
  $('login-view').classList.remove('hidden');
  $('login-pass').value = '';
  $('login-error').textContent = '';
}

$('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  $('login-btn').disabled = true;
  $('login-error').textContent = '';
  try {
    await login($('login-user').value.trim(), $('login-pass').value);
  } catch (err) {
    $('login-error').textContent = err.message;
  } finally { $('login-btn').disabled = false; }
});
$('logout-btn').addEventListener('click', () => logout());

/* ============================ tabs ============================ */
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    ['backtest', 'evolve', 'about'].forEach(name => {
      $('tab-' + name).classList.toggle('hidden', name !== tab.dataset.tab);
    });
  });
});

/* ============================ canvas charting ============================ */
function setupCanvas(cv) {
  const dpr = window.devicePixelRatio || 1;
  const rect = cv.getBoundingClientRect();
  const w = Math.max(320, rect.width || 800);
  const h = parseInt(cv.getAttribute('height'), 10) || 240;
  cv.width = w * dpr; cv.height = h * dpr;
  cv.style.height = h + 'px';
  const ctx = cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return { ctx, w, h };
}

function niceTicks(min, max, count) {
  const span = max - min || 1;
  const raw = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw) || mag * 10;
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 0.001; v += step) out.push(v);
  return out;
}

function drawSeries(cv, series, opts = {}) {
  const { ctx, w, h } = setupCanvas(cv);
  const padL = 58, padR = 14, padT = 12, padB = 24;
  const iw = w - padL - padR, ih = h - padT - padB;
  const all = series.flatMap(s => s.points);
  if (!all.length) return;
  let min = Math.min(...all), max = Math.max(...all);
  if (min === max) { min -= 1; max += 1; }
  const pad = (max - min) * 0.08; min -= pad; max += pad;
  const len = Math.max(...series.map(s => s.points.length));
  const X = (i) => padL + (len <= 1 ? iw / 2 : (i / (len - 1)) * iw);
  const Y = (v) => padT + (1 - (v - min) / (max - min)) * ih;

  // grid
  ctx.strokeStyle = '#243043'; ctx.lineWidth = 1;
  ctx.fillStyle = '#5e7085'; ctx.font = '11px -apple-system,system-ui,sans-serif';
  ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
  niceTicks(min, max, 5).forEach(v => {
    const y = Y(v);
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
    ctx.fillText(opts.fmtY ? opts.fmtY(v) : fmt(v, 0), padL - 8, y);
  });

  // baseline at the initial value when provided
  if (opts.baseline !== undefined && opts.baseline >= min && opts.baseline <= max) {
    const y = Y(opts.baseline);
    ctx.strokeStyle = '#3a4a63'; ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
    ctx.setLineDash([]);
  }

  // x labels
  ctx.textAlign = 'center'; ctx.textBaseline = 'top';
  const marks = Math.min(6, len);
  for (let k = 0; k < marks; k++) {
    const i = Math.round((k / (marks - 1 || 1)) * (len - 1));
    ctx.fillText(String(i), X(i), h - padB + 5);
  }

  // lines
  series.forEach(s => {
    if (!s.points.length) return;
    ctx.strokeStyle = s.color; ctx.lineWidth = s.width || 1.8;
    ctx.beginPath();
    s.points.forEach((v, i) => { const x = X(i), y = Y(v); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.stroke();
    if (opts.fill && s.points.length > 1) {
      ctx.lineTo(X(s.points.length - 1), padT + ih);
      ctx.lineTo(X(0), padT + ih);
      ctx.closePath();
      const grad = ctx.createLinearGradient(0, padT, 0, padT + ih);
      grad.addColorStop(0, s.color + '33'); grad.addColorStop(1, s.color + '00');
      ctx.fillStyle = grad; ctx.fill();
    }
  });
}

/* ============================ forms from gene space ============================ */
function buildControls(container, specs, defaults) {
  container.innerHTML = '';
  specs.forEach(g => {
    const wrap = document.createElement('label');
    wrap.className = 'field';
    const val = (defaults && defaults[g.name] !== undefined) ? defaults[g.name] : null;
    if (g.kind === 'bool') {
      wrap.innerHTML = `<span>${g.name}${g.help ? ' · ' + g.help : ''}</span>
        <select data-gene="${g.name}" data-kind="bool">
          <option value="true"${val === true ? ' selected' : ''}>true</option>
          <option value="false"${val !== true ? ' selected' : ''}>false</option>
        </select>`;
    } else {
      const step = g.kind === 'int' ? 1 : (g.log ? 0.0001 : 0.01);
      wrap.innerHTML = `<span>${g.name}${g.help ? ' · ' + g.help : ''}</span>
        <input type="number" data-gene="${g.name}" data-kind="${g.kind}"
               value="${val === null ? '' : val}" min="${g.low ?? ''}" max="${g.high ?? ''}" step="${step}">`;
    }
    container.appendChild(wrap);
  });
}

function readControls(container) {
  const out = {};
  container.querySelectorAll('[data-gene]').forEach(el => {
    const name = el.dataset.gene;
    if (el.dataset.kind === 'bool') { out[name] = el.value === 'true'; return; }
    if (el.value === '') return;
    const v = Number(el.value);
    out[name] = el.dataset.kind === 'int' ? Math.round(v) : v;
  });
  return out;
}

function centerOf(g) {
  if (g.kind === 'bool') return true;
  if (g.kind === 'choice') return g.values[0];
  if (g.log) return Math.exp((Math.log(g.low) + Math.log(g.high)) / 2);
  const v = (g.low + g.high) / 2;
  return g.kind === 'int' ? Math.round(v) : Number(v.toFixed(4));
}
function defaultsFor(specs) { const o = {}; specs.forEach(g => o[g.name] = centerOf(g)); return o; }

/* ============================ backtest ============================ */
let btSpace = null;

function buildBacktestForms() {
  const sel = $('bt-strategy');
  sel.innerHTML = '';
  state.strategies.forEach(s => {
    const o = document.createElement('option');
    o.value = s; o.textContent = s; sel.appendChild(o);
  });
  sel.addEventListener('change', syncStrategyForm);
  syncStrategyForm();
  buildControls($('bt-risk-params'), btSpace.risk, defaultsFor(btSpace.risk));
}

function syncStrategyForm() {
  const name = $('bt-strategy').value;
  const specs = btSpace.strategies[name] || [];
  $('bt-strategy-help').textContent =
    specs.map(g => `${g.name}[${g.low}..${g.high}]`).join('  ·  ');
  buildControls($('bt-strategy-params'), specs, defaultsFor(specs));
}

$('bt-source').addEventListener('change', () => {
  $('bt-inline-wrap').classList.toggle('hidden', $('bt-source').value !== 'inline');
});

function buildSource(selId, barsId, seedId) {
  const kind = $(selId).value;
  if (kind === 'inline') {
    let bars = [];
    try { bars = JSON.parse($('bt-inline').value || '[]'); }
    catch (e) { throw new Error('inline K 线 JSON 解析失败：' + e.message); }
    return { kind: 'inline', bars };
  }
  return { kind, n: Number($(barsId).value) || 3000, seed: Number($(seedId).value) || 7 };
}

async function runBacktest() {
  const btn = $('bt-run');
  btn.disabled = true;
  $('bt-status').textContent = '运行中…';
  try {
    const body = {
      source: buildSource('bt-source', 'bt-bars', 'bt-seed'),
      strategy: $('bt-strategy').value,
      params: readControls($('bt-strategy-params')),
      risk: readControls($('bt-risk-params')),
      capital: Number($('bt-capital').value),
      fee: Number($('bt-fee').value),
      slippage: Number($('bt-slip').value),
    };
    const t0 = performance.now();
    const r = await api('/api/backtest', { method: 'POST', body });
    const ms = Math.round(performance.now() - t0);
    $('bt-status').textContent = `完成 · ${r.bars} 根 K 线 · ${r.num_trades} 笔交易 · 后端耗时 ${ms}ms`;
    renderBacktest(r);
  } catch (err) {
    $('bt-status').textContent = '失败：' + err.message;
  } finally { btn.disabled = false; }
}
$('bt-run').addEventListener('click', runBacktest);

function renderBacktest(r) {
  const m = (k, v, sub, c) => `<div class="metric"><div class="k">${k}</div>
      <div class="v ${c || ''}">${v}</div><div class="s">${sub || ''}</div></div>`;
  $('bt-metrics').innerHTML = [
    m('总收益', pct(r.total_return_pct), `期末 ${fmt(r.final_equity)}`, cls(r.total_return_pct)),
    m('年化 CAGR', pct(r.cagr_pct), '', cls(r.cagr_pct)),
    m('夏普 (年化)', fmt(r.sharpe), r.sharpe > 1 ? '可用' : '偏低', cls(r.sharpe)),
    m('索提诺', fmt(r.sortino), '', cls(r.sortino)),
    m('最大回撤', pct(r.max_drawdown_pct), r.max_drawdown_pct > 35 ? '超阈值' : '', 'neg'),
    m('胜率', pct(r.win_rate_pct, 1), `${r.num_trades} 笔`),
    m('盈亏比', isFinite(r.profit_factor) ? fmt(r.profit_factor) : '∞', ''),
    m('状态', r.halted ? '已熔断' : '正常', r.halt_reason || '风控未触发',
      r.halted ? 'neg' : 'pos'),
  ].join('');

  $('bt-curve-label').textContent = `${r.symbol} · ${r.timeframe} · 初始 ${fmt(r.initial_capital)} → 期末 ${fmt(r.final_equity)}`;
  drawSeries($('bt-chart'),
    [{ points: r.equity_curve, color: '#4da3ff', width: 2 }],
    { baseline: r.initial_capital, fill: true, fmtY: (v) => fmt(v, 0) });

  const rows = r.trades.slice(0, 200).map(t => `<tr>
      <td>${t.reason || '—'}</td>
      <td style="color:${t.side === 'long' ? '#ff5a5a' : '#2ecc8f'}">${t.side === 'long' ? '多' : '空'}</td>
      <td>${fmt(t.entry)}</td><td>${fmt(t.exit)}</td><td>${fmt(t.qty, 4)}</td>
      <td class="${cls(t.pnl)}">${fmt(t.pnl)}</td></tr>`).join('');
  $('bt-trades-label').textContent = r.trades.length >= 200 ? '仅显示前 200 笔' : '';
  $('bt-trades').innerHTML =
    `<thead><tr><th>原因</th><th>方向</th><th>开仓</th><th>平仓</th><th>数量</th><th>盈亏</th></tr></thead>
     <tbody>${rows || '<tr><td colspan="6" style="color:#5e7085">无成交 — 该参数组合在当前数据上没有触发任何交易。试着放宽 ADX 阈值 / RSI 阈值，或换一段数据。</td></tr>'}</tbody>`;
}

/* ============================ evolution ============================ */
let evStream = null, evHistory = [];

function buildEvolveForms() {
  const box = $('ev-strategies');
  box.innerHTML = '';
  state.strategies.forEach(s => {
    const id = 'evs-' + s;
    box.insertAdjacentHTML('beforeend',
      `<label><input type="checkbox" id="${id}" value="${s}" checked><span>${s}</span></label>`);
  });
}

async function startEvolution() {
  const btn = $('ev-start');
  const chosen = [...document.querySelectorAll('#ev-strategies input:checked')].map(i => i.value);
  if (!chosen.length) { $('ev-status').textContent = '至少选择一个策略'; return; }
  btn.disabled = true; $('ev-cancel').disabled = false;
  evHistory = [];
  $('ev-table').innerHTML = ''; $('ev-log').innerHTML = '';
  $('ev-champion').classList.add('hidden');
  $('ev-progress').classList.remove('hidden');
  $('ev-status').textContent = '已提交，正在进化…';

  try {
    const body = {
      source: buildSource('ev-source', 'ev-bars', 'ev-seed'),
      config: {
        strategies: chosen,
        population_size: Number($('ev-pop').value),
        generations: Number($('ev-gen').value),
        elite_count: Number($('ev-elite').value),
        tournament_size: Number($('ev-tour').value),
        mutation_rate: Number($('ev-mut').value),
        patience: Number($('ev-patience').value),
        train_frac: Number($('ev-train').value),
        val_frac: Number($('ev-val').value),
        seed: Number($('ev-seed').value),
      },
    };
    const ack = await api('/api/evolution/start', { method: 'POST', body });
    openStream(ack.job_id);
  } catch (err) {
    $('ev-status').textContent = '失败：' + err.message;
    btn.disabled = false; $('ev-cancel').disabled = true;
  }
}
$('ev-start').addEventListener('click', startEvolution);
$('ev-cancel').addEventListener('click', async () => {
  if (!state.currentJob) return;
  await api('/api/evolution/cancel', { method: 'POST', body: { id: state.currentJob } }).catch(() => {});
  $('ev-status').textContent = '正在请求终止…';
});

function openStream(jobId) {
  state.currentJob = jobId;
  if (evStream) evStream.close();
  evStream = new EventSource('/api/evolution/stream?id=' + jobId + '&token=' + encodeURIComponent(state.token));
  evStream.addEventListener('generation', (e) => {
    const rec = JSON.parse(e.data);
    evHistory.push(rec);
    renderEvoChart();
    renderEvoTable();
    logLine(`gen ${String(rec.generation).padStart(2)} · best ${rec.best_fitness.toFixed(3)}` +
            ` · mean ${rec.mean_fitness.toFixed(3)} · ${JSON.stringify(rec.species)}`, 'l-gen');
  });
  evStream.addEventListener('progress', (e) => {
    const p = JSON.parse(e.data);
    const ratio = p.total ? Math.round(p.done / p.total * 100) : 0;
    $('ev-bar').style.width = ratio + '%';
    $('ev-progress-text').textContent = `${p.done}/${p.total} · ${p.message}`;
  });
  evStream.addEventListener('done', (e) => {
    const d = JSON.parse(e.data);
    logLine('进化结束：' + d.stop_reason, 'l-done');
  });
  evStream.addEventListener('error', (e) => {
    if (e.data) {
      const d = JSON.parse(e.data);
      logLine('错误：' + d.message, 'l-error');
    }
  });
  evStream.addEventListener('final', (e) => {
    const d = JSON.parse(e.data);
    evStream.close();
    $('ev-start').disabled = false; $('ev-cancel').disabled = true;
    $('ev-status').textContent = d.status === 'error' ? ('失败：' + d.error) : '完成';
    if (d.result) renderChampion(d.result);
  });
  evStream.onerror = () => {
    $('ev-start').disabled = false; $('ev-cancel').disabled = true;
    $('ev-status').textContent = '连接中断';
  };
}

function logLine(text, clsName) {
  const el = document.createElement('div');
  el.className = clsName || '';
  el.textContent = new Date().toLocaleTimeString('zh-CN') + '  ' + text;
  const box = $('ev-log');
  box.appendChild(el);
  box.scrollTop = box.scrollHeight;
}

function renderEvoChart() {
  if (!evHistory.length) return;
  drawSeries($('ev-chart'), [
    { points: evHistory.map(h => h.best_fitness), color: '#4da3ff', width: 2.2 },
    { points: evHistory.map(h => h.mean_fitness), color: '#7c5cff', width: 1.6 },
  ], { fmtY: (v) => fmt(v, 1) });
}

function renderEvoTable() {
  const rows = evHistory.map(h => `<tr>
      <td>第 ${h.generation} 代</td>
      <td class="${cls(h.best_fitness)}">${fmt(h.best_fitness, 3)}</td>
      <td>${fmt(h.mean_fitness, 3)}</td>
      <td>${fmt(h.std_fitness, 3)}</td>
      <td>${h.best_genome.strategy}</td>
      <td>${Object.entries(h.species).map(([k, v]) => `${k}:${v}`).join(' ')}</td>
      <td>${Math.round((h.best.is_metrics.sharpe || 0) * 100) / 100} / ${Math.round((h.best.oos_metrics.sharpe || 0) * 100) / 100}</td>
    </tr>`).join('');
  $('ev-table').innerHTML =
    `<thead><tr><th>世代</th><th>最优适应度</th><th>平均</th><th>标准差</th>
      <th>领先物种</th><th>种群构成</th><th>IS/OOS 夏普</th></tr></thead><tbody>${rows}</tbody>`;
}

function segBox(title, m, sealed) {
  if (!m) return '';
  const row = (k, v, c) => `<div class="row"><span class="muted">${k}</span><span class="${c || ''}">${v}</span></div>`;
  return `<div class="box ${sealed ? 'sealed' : ''}">
      <h4>${title}</h4>
      ${row('收益', pct(m.total_return_pct), cls(m.total_return_pct))}
      ${row('夏普', fmt(m.sharpe), cls(m.sharpe))}
      ${row('最大回撤', pct(m.max_drawdown_pct), 'neg')}
      ${row('交易笔数', m.num_trades)}
      ${row('胜率', pct(m.win_rate_pct, 1))}
    </div>`;
}

function renderChampion(r) {
  const c = r.champion;
  const genes = Object.entries(c.params)
    .map(([k, v]) => `<div><div class="gk">${k}</div><div class="gv">${typeof v === 'number' ? fmt(v, 4) : v}</div></div>`)
    .concat(Object.entries(c.risk).map(([k, v]) =>
      `<div><div class="gk">${k}</div><div class="gv">${typeof v === 'number' ? fmt(v, 4) : v}</div></div>`))
    .join('');
  $('ev-champion-body').innerHTML = `
    <div class="row" style="display:flex;gap:10px;flex-wrap:wrap;align-items:baseline">
      <b style="font-size:16px">${c.strategy}</b>
      <span class="muted">适应度 ${fmt(r.champion_fitness, 3)}</span>
      <span class="muted">来源 ${c.origin} · 第 ${c.generation} 代</span>
      <span class="muted">id ${shortHash(c.id)}</span>
    </div>
    ${r.degraded_selection
      ? `<div class="warn" style="margin-top:8px">
           <b>降级结果，不要用于实盘。</b>
           没有任何基因组通过资格门槛（在自己训练过的数据段上盈利），
           当前冠军只是原始适应度最高的一个，它的样本外表现更可能是运气。
         </div>`
      : ''}
    <div class="genome">${genes}</div>
    <div class="seg">
      ${segBox('训练集 IS（选择依据）', r.champion_is)}
      ${segBox('验证集 OOS（泛化惩罚）', r.champion_oos)}
      ${segBox('测试集 TEST（全程密封 · 唯一可对外报的数字）', r.champion_test, true)}
    </div>
    ${r.lineage && r.lineage.length > 1
      ? `<p class="muted small" style="margin-top:10px">谱系：${r.lineage.map(l => `${l.strategy}(${l.origin})`).join(' → ')}</p>`
      : ''}`;
  $('ev-champion').classList.remove('hidden');
  $('ev-stop').textContent = r.stop_reason + ' · 共 ' + r.total_evaluations + ' 次评估';
  $('ev-bar').style.width = '100%';
  $('ev-progress-text').textContent = `${r.generations_run} 代 · ${r.elapsed_sec.toFixed(1)}s`;
}

/* ============================ boot ============================ */
async function boot() {
  const meta = await api('/api/strategies');
  state.strategies = meta.strategies;
  btSpace = meta.space;
  buildBacktestForms();
  buildEvolveForms();
  try { await api('/api/health'); $('conn-dot').className = 'dot ok'; }
  catch (_) { $('conn-dot').className = 'dot bad'; }
}

window.addEventListener('resize', () => {
  if (evHistory.length) renderEvoChart();
});

// restore session if we already have a token
(async () => {
  if (!state.token) return;
  try {
    const me = await api('/api/auth/me');
    state.user = me;
    $('login-view').classList.add('hidden');
    $('app-view').classList.remove('hidden');
    $('me-name').textContent = me.username + (me.role === 'admin' ? ' · 管理员' : '');
    await boot();
  } catch (_) { logout(false); }
})();
