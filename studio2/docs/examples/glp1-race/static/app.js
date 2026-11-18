/* The GLP-1 race — front end. Every figure is computed here from /api responses. */
(() => {
'use strict';

// ------------------------------------------------------------ helpers ---
const $ = s => document.querySelector(s);
const esc = t => String(t ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
const fmt = n => (n == null || !isFinite(n)) ? '—' : Math.round(n).toLocaleString('en-US');
const pct = (a, b) => b ? (100 * a / b) : null;
const fmtPct = v => v == null ? '—' : (v < 1 && v > 0 ? v.toFixed(2) : v.toFixed(1)) + '%';
const when = iso => { if (!iso) return 'never'; const d = new Date(iso); return isNaN(d) ? iso : d.toLocaleString(undefined, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short' }); };
const ymd = s => { if (!s) return '—'; const p = String(s); if (/^\d{8}$/.test(p)) return `${p.slice(0, 4)}-${p.slice(4, 6)}-${p.slice(6)}`; return p; };
const parseDate = s => { if (!s) return null; const [y, m, d] = s.split('-').map(Number); if (!y) return null; return new Date(Date.UTC(y, (m || 7) - 1, d || (m ? 15 : 1))); };
const today = new Date();
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const dcol = id => `var(--d-${id})`;
const cssv = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const hasD3 = () => typeof window.d3 !== 'undefined';

const STATUS_GROUP = {
  RECRUITING: 'active', NOT_YET_RECRUITING: 'active', ACTIVE_NOT_RECRUITING: 'active', ENROLLING_BY_INVITATION: 'active',
  COMPLETED: 'done', TERMINATED: 'stopped', WITHDRAWN: 'stopped', SUSPENDED: 'stopped', UNKNOWN: 'unknown',
};
const GROUPS = [
  { id: 'active', name: 'Active', desc: 'Recruiting, not yet recruiting, active not recruiting, enrolling by invitation', col: 'var(--st-active)' },
  { id: 'done', name: 'Completed', desc: 'Completed', col: 'var(--st-done)' },
  { id: 'stopped', name: 'Stopped', desc: 'Terminated, withdrawn or suspended', col: 'var(--st-stopped)' },
  { id: 'unknown', name: 'Unknown', desc: 'Status not verified after its completion date passed', col: 'var(--st-unknown)' },
  { id: 'other', name: 'Other', desc: 'Expanded access and other record types', col: 'var(--st-other)' },
];
const groupOf = s => STATUS_GROUP[s] || 'other';
const STATUS_NAME = s => s ? s.charAt(0) + s.slice(1).toLowerCase().replace(/_/g, ' ') : 'Not given';
const PHASE_ORDER = ['Early phase 1', 'Phase 1', 'Phase 1/2', 'Phase 2', 'Phase 2/3', 'Phase 3', 'Phase 4', 'Not applicable', 'No phase (observational/other)'];
const PHASE_SHORT = { 'Early phase 1': 'Early 1', 'Phase 1': '1', 'Phase 1/2': '1/2', 'Phase 2': '2', 'Phase 2/3': '2/3', 'Phase 3': '3', 'Phase 4': '4', 'Not applicable': 'N/A', 'No phase (observational/other)': 'None' };
const isP3 = t => t.phases.includes('PHASE3');
const isInt = t => t.type === 'INTERVENTIONAL';
const TYPE_NAME = k => ({ INTERVENTIONAL: 'Interventional (clinical trial)', OBSERVATIONAL: 'Observational', EXPANDED_ACCESS: 'Expanded access', NA: 'Not given' }[k] || STATUS_NAME(k));

// ------------------------------------------------------------- state ---
const S = {
  meta: null, trials: [], sites: [], ctSources: [], fda: null, trialsError: null, fdaError: null,
  f: { drugs: new Set(), phases: new Set(), groups: new Set(), types: new Set(), sponsor: '', text: '' },
  sort: { key: 'last_update', dir: -1 }, page: 0,
  spSort: { key: 'trials', dir: -1 }, spShow: 15, spQuery: '',
  tlMode: 'primary', yrScale: 'lin', rxMode: 'count', rx: null, rxTerm: null,
  world: null, worldErr: null,
};
const drugName = id => (S.meta?.drugs.find(d => d.id === id) || {}).name || id;

// --------------------------------------------------------- tooltip ---
const tip = $('#tip');
function showTip(html, ev) {
  tip.innerHTML = html; tip.classList.add('on');
  const x = ev.clientX, y = ev.clientY, w = tip.offsetWidth, h = tip.offsetHeight;
  let left = x + 14, top = y + 14;
  if (left + w > window.innerWidth - 8) left = Math.max(8, x - w - 14);
  if (top + h > window.innerHeight - 8) top = Math.max(8, y - h - 14);
  tip.style.left = left + 'px'; tip.style.top = top + 'px';
}
const hideTip = () => tip.classList.remove('on');
window.addEventListener('scroll', hideTip, { passive: true });
document.addEventListener('pointerdown', e => { if (!e.target.closest('svg')) hideTip(); });

// ------------------------------------------------------ data loading ---
async function getJSON(url, opts) {
  const r = await fetch(url, opts);
  let j = null; try { j = await r.json(); } catch { /* not json */ }
  if (!r.ok) throw new Error((j && (j.detail || j.error)) || `HTTP ${r.status}`);
  return j;
}

async function loadAll() {
  const [m, t, f] = await Promise.allSettled([getJSON('/api/meta'), getJSON('/api/trials'), getJSON('/api/fda')]);
  if (m.status === 'fulfilled') S.meta = m.value; else S.metaError = m.reason.message;
  if (t.status === 'fulfilled') { S.trials = t.value.trials; S.sites = t.value.sites; S.ctSources = t.value.sources; S.trialsAt = t.value.server_time; S.trialsError = null; }
  else S.trialsError = t.reason.message;
  if (f.status === 'fulfilled') { S.fda = f.value; S.fdaError = null; } else S.fdaError = f.reason.message;
  if (!S.meta) { S.meta = { drugs: [], ct: [] }; }
  readHash();
  buildFilters();
  renderAll();
  renderFdaAll();
  renderSources();
  loadLog();
  watchRefresh(S.meta.refreshing || S.fda?.refreshing);
}

// -------------------------------------------------------- banners ---
function renderBanners() {
  const out = [];
  if (S.metaError && S.trialsError) out.push(`<div class="banner bad"><strong>This app's server did not answer.</strong> ${esc(S.trialsError)}. No numbers are shown because none could be loaded.</div>`);
  else if (S.trialsError) out.push(`<div class="banner bad"><strong>Trial data could not be loaded from this app's server:</strong> ${esc(S.trialsError)}. The trial charts are empty for that reason, not because there are no trials.</div>`);
  const failed = S.ctSources.filter(s => !s.ok);
  if (failed.length) {
    out.push(`<div class="banner ${failed.some(s => !s.fetched_at) ? 'bad' : 'warn'}"><strong>ClinicalTrials.gov did not answer for ${failed.length === 1 ? 'one search' : failed.length + ' searches'}.</strong><ul>${failed.map(s => `<li>${esc(s.name)}: ${esc(s.error || 'not fetched')} (tried ${when(s.attempted_at)}). ${s.fetched_at ? `Showing the copy fetched ${when(s.fetched_at)}.` : '<strong>No copy is available, so this drug is missing from every trial number.</strong>'}</li>`).join('')}</ul></div>`);
  }
  const old = S.ctSources.filter(s => s.ok && s.fetched_at && (Date.now() - new Date(s.fetched_at)) > 36 * 3600e3);
  if (old.length) out.push(`<div class="banner warn"><strong>Some trial data is more than 36 hours old</strong>: ${old.map(s => esc(s.name)).join(', ')}.</div>`);
  if (S.fdaError) out.push(`<div class="banner bad"><strong>openFDA data could not be loaded from this app's server:</strong> ${esc(S.fdaError)}.</div>`);
  if (S.fda) {
    const bad = S.fda.drugs.filter(d => d.faers && d.events && d.events.errors && d.events.errors.length);
    const none = S.fda.drugs.filter(d => d.faers && !d.events);
    if (bad.length || none.length) out.push(`<div class="banner warn"><strong>Some openFDA requests failed.</strong><ul>${bad.map(d => `<li>${esc(d.name)}: ${d.events.errors.map(esc).join('; ')}${d.events.stale_fields ? ` — showing earlier values for ${esc(d.events.stale_fields.join(', '))} from ${when(d.events.stale_from)}` : ''}</li>`).join('')}${none.map(d => `<li>${esc(d.name)}: not fetched yet.</li>`).join('')}</ul></div>`);
    const labBad = S.fda.drugs.filter(d => d.labels && d.labels.ok === false);
    if (labBad.length) out.push(`<div class="banner warn"><strong>openFDA label search failed</strong> for ${labBad.map(d => esc(d.name)).join(', ')}: ${esc(labBad[0].labels.error)}.</div>`);
  }
  $('#banners').innerHTML = out.join('');
}

function renderStatus() {
  const ok = S.ctSources.filter(s => s.fetched_at);
  const ctAt = ok.length ? ok.map(s => s.fetched_at).sort()[0] : null;
  const fd = S.fda?.drugs.filter(d => d.events?.fetched_at) || [];
  const fdaAt = fd.length ? fd.map(d => d.events.fetched_at).sort()[0] : null;
  const lu = fd.map(d => d.events.last_updated).filter(Boolean).sort().pop();
  const ctOk = S.ctSources.length && S.ctSources.every(s => s.ok);
  const fdaOk = S.fda && fd.length && fd.every(d => d.events.ok);
  $('#statusbar').innerHTML = `
    <span class="pill"><span class="dot ${S.trialsError ? 'bad' : ctOk ? 'ok' : 'wait'}"></span> ClinicalTrials.gov: ${ctAt ? 'fetched ' + when(ctAt) : 'not available'}</span>
    <span class="pill"><span class="dot ${S.fdaError ? 'bad' : fdaOk ? 'ok' : 'wait'}"></span> openFDA: ${fdaAt ? 'fetched ' + when(fdaAt) : 'not available'}${lu ? ` · FDA dataset updated ${ymd(lu)}` : ''}</span>
    <span id="refreshState"></span>`;
}

// --------------------------------------------------------- filters ---
function readHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  const set = (k, v) => { S.f[k] = new Set((h.get(v) || '').split(',').filter(Boolean)); };
  set('drugs', 'drug'); set('phases', 'phase'); set('groups', 'status'); set('types', 'type');
  S.f.sponsor = h.get('sponsor') || ''; S.f.text = h.get('q') || '';
}
function writeHash() {
  const h = new URLSearchParams();
  if (S.f.drugs.size) h.set('drug', [...S.f.drugs].join(','));
  if (S.f.phases.size) h.set('phase', [...S.f.phases].join(','));
  if (S.f.groups.size) h.set('status', [...S.f.groups].join(','));
  if (S.f.types.size) h.set('type', [...S.f.types].join(','));
  if (S.f.sponsor) h.set('sponsor', S.f.sponsor);
  if (S.f.text) h.set('q', S.f.text);
  const s = h.toString();
  history.replaceState(null, '', s ? '#' + s : location.pathname + location.search);
}

function buildFilters() {
  const cnt = {}; S.trials.forEach(t => t.drugs.forEach(d => cnt[d] = (cnt[d] || 0) + 1));
  $('#fDrug').innerHTML = S.meta.drugs.map(d => `<button class="chip" style="--chip:${dcol(d.id)}" data-v="${d.id}" aria-pressed="${S.f.drugs.has(d.id)}"><span class="dot"></span>${esc(d.name)} <span class="ct">${fmt(cnt[d.id] || 0)}</span></button>`).join('');
  const phases = PHASE_ORDER.filter(p => S.trials.some(t => t.phase === p));
  S.trials.forEach(t => { if (!phases.includes(t.phase)) phases.push(t.phase); });
  $('#fPhase').innerHTML = phases.map(p => `<button class="chip" data-v="${esc(p)}" aria-pressed="${S.f.phases.has(p)}" title="${esc(p)}">${esc(p === 'No phase (observational/other)' ? 'No phase' : p)}</button>`).join('');
  $('#fStatus').innerHTML = GROUPS.filter(g => S.trials.some(t => groupOf(t.status) === g.id)).map(g => `<button class="chip" style="--chip:${g.col}" data-v="${g.id}" aria-pressed="${S.f.groups.has(g.id)}" title="${esc(g.desc)}"><span class="dot"></span>${g.name}</button>`).join('');
  const tc = {}; S.trials.forEach(t => tc[t.type || 'NA'] = (tc[t.type || 'NA'] || 0) + 1);
  $('#fType').innerHTML = Object.keys(tc).sort((a, b) => tc[b] - tc[a]).map(k => `<button class="chip" data-v="${esc(k)}" aria-pressed="${S.f.types.has(k)}">${esc(TYPE_NAME(k))} <span class="ct">${fmt(tc[k])}</span></button>`).join('');
  const sp = {}; S.trials.forEach(t => { if (t.sponsor) sp[t.sponsor] = (sp[t.sponsor] || 0) + 1; });
  const list = Object.entries(sp).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  $('#fSponsor').innerHTML = '<option value="">All lead sponsors</option>' + list.map(([n, c]) => `<option value="${esc(n)}"${n === S.f.sponsor ? ' selected' : ''}>${esc(n)} (${c})</option>`).join('');
  if (S.f.sponsor && !sp[S.f.sponsor]) $('#fSponsor').insertAdjacentHTML('beforeend', `<option value="${esc(S.f.sponsor)}" selected>${esc(S.f.sponsor)} (0)</option>`);
  $('#fText').value = S.f.text;
}

function toggleChip(container, key) {
  $(container).addEventListener('click', e => {
    const b = e.target.closest('.chip'); if (!b) return;
    const v = b.dataset.v, set = S.f[key];
    set.has(v) ? set.delete(v) : set.add(v);
    b.setAttribute('aria-pressed', set.has(v));
    changed();
  });
}
toggleChip('#fType', 'types'); toggleChip('#fDrug', 'drugs'); toggleChip('#fPhase', 'phases'); toggleChip('#fStatus', 'groups');
$('#fSponsor').addEventListener('change', e => { S.f.sponsor = e.target.value; changed(); });
$('#fText').addEventListener('input', debounce(e => { S.f.text = e.target.value.trim(); changed(); }, 250));
$('#filtersToggle').addEventListener('click', () => {
  const f = $('#filters'); const c = f.classList.toggle('collapsed');
  $('#filtersToggle').setAttribute('aria-expanded', !c);
  if (!c) f.scrollIntoView({ block: 'start' });
});

function setFilter(patch) {
  Object.assign(S.f, patch);
  buildFilters(); changed();
}
function resetFilters() { S.f = { drugs: new Set(), phases: new Set(), groups: new Set(), types: new Set(), sponsor: '', text: '' }; buildFilters(); changed(); }
function changed() { S.page = 0; writeHash(); renderAll(); renderFdaAll(); }

function filtered() {
  const f = S.f, q = f.text.toLowerCase();
  return S.trials.filter(t =>
    (!f.drugs.size || t.drugs.some(d => f.drugs.has(d))) &&
    (!f.phases.size || f.phases.has(t.phase)) &&
    (!f.groups.size || f.groups.has(groupOf(t.status))) &&
    (!f.types.size || f.types.has(t.type || 'NA')) &&
    (!f.sponsor || t.sponsor === f.sponsor) &&
    (!q || (t.id + ' ' + t.title + ' ' + (t.acronym || '') + ' ' + t.conditions.join(' ') + ' ' + t.interventions.join(' ') + ' ' + (t.sponsor || '')).toLowerCase().includes(q)));
}
// drugs shown in per-drug charts
const shownDrugs = () => S.meta.drugs.map(d => d.id).filter(id => !S.f.drugs.size || S.f.drugs.has(id));
const drugsOf = t => t.drugs.filter(d => !S.f.drugs.size || S.f.drugs.has(d));

function renderActive(rows) {
  const parts = [];
  if (S.f.drugs.size) parts.push('drug: ' + [...S.f.drugs].map(drugName).join(', '));
  if (S.f.phases.size) parts.push('phase: ' + [...S.f.phases].join(', '));
  if (S.f.groups.size) parts.push('status: ' + [...S.f.groups].map(g => GROUPS.find(x => x.id === g)?.name || g).join(', '));
  if (S.f.types.size) parts.push('type: ' + [...S.f.types].map(TYPE_NAME).join(', '));
  if (S.f.sponsor) parts.push('sponsor: ' + S.f.sponsor);
  if (S.f.text) parts.push(`text: “${S.f.text}”`);
  $('#activeFilters').innerHTML = `<span><strong>${fmt(rows.length)}</strong> of ${fmt(S.trials.length)} trials</span>` +
    (parts.length ? `<span class="muted">· ${esc(parts.join(' · '))}</span> <button class="btn" id="resetBtn">Clear filters</button>` : '<span class="muted">· no filters</span>');
  $('#activeFilters').insertAdjacentHTML('beforeend', '<button class="btn primary filters-done" id="filtersDone">Done</button>');
  $('#filtersDone').onclick = () => { $('#filters').classList.add('collapsed'); $('#filtersToggle').setAttribute('aria-expanded', 'false'); $('#headline').scrollIntoView(); };
  const rb = $('#resetBtn'); if (rb) rb.onclick = resetFilters;
}

// ----------------------------------------------------- provenance ---
function ctProv(extra = '') {
  if (!S.ctSources.length) return 'Source: ClinicalTrials.gov API v2 — not available.';
  const ok = S.ctSources.filter(s => s.fetched_at);
  const times = ok.map(s => s.fetched_at).sort();
  const fails = S.ctSources.filter(s => !s.ok).length;
  return `Source: ClinicalTrials.gov API v2, ${S.ctSources.length} searches (one per drug), ${fmt(S.trials.length)} unique trials, fetched ${times.length ? when(times[0]) + (times[0] !== times[times.length - 1] ? ' to ' + when(times[times.length - 1]) : '') : '—'}${fails ? ` · <strong>${fails} search${fails > 1 ? 'es' : ''} failed</strong>` : ''}. ${extra} <a href="#sources">Queries and fetch log</a>.`;
}

// ------------------------------------------------------- render all ---
function renderAll() {
  renderBanners(); renderStatus();
  const rows = filtered();
  renderActive(rows);
  renderKpis(rows);
  renderHeat(rows);
  if (hasD3()) { renderStatusChart(rows); renderTimeline(rows); renderStarts(rows); renderEnrollment(rows); renderMap(rows); }
  else ['#statusChart', '#tlChart', '#startsChart', '#enrChart', '#mapBox'].forEach(s => $(s).innerHTML = '<div class="empty">The chart library (d3, from cdn.jsdelivr.net) did not load, so charts cannot be drawn. The tables still work.</div>');
  renderSponsors(rows);
  renderTrialTable(rows);
  $('#provKpi').innerHTML = ctProv();
  $('#provPipeline').innerHTML = ctProv('Counts by drug count a trial under each drug it lists.');
  $('#provTimeline').innerHTML = ctProv('Dates as registered by the sponsor.');
  $('#provStarts').innerHTML = ctProv('Start dates and enrollment as registered.');
  $('#provSponsors').innerHTML = ctProv('Lead sponsor as registered; names are not merged.');
  $('#provTrials').innerHTML = ctProv('Detail records are fetched live when you open a trial.');
  $('#provMap').innerHTML = ctProv('Site coordinates from ClinicalTrials.gov; base map: Natural Earth via world-atlas (jsDelivr).');
}

// ------------------------------------------------------------- KPIs ---
function renderKpis(rows) {
  if (S.trialsError && !S.trials.length) { $('#kpis').innerHTML = '<div class="empty" style="grid-column:1/-1">No trial numbers: the data could not be loaded.</div>'; return; }
  const active = rows.filter(t => groupOf(t.status) === 'active');
  const p3 = active.filter(isP3);
  let act = 0, est = 0, unk = 0, obs = 0, nobs = 0;
  rows.forEach(t => { if (!isInt(t)) { if (t.enrollment) { obs += t.enrollment; nobs++; } return; } if (t.enrollment == null) unk++; else if (t.enrollment_type === 'ACTUAL') act += t.enrollment; else est += t.enrollment; });
  const sponsors = new Set(rows.map(t => t.sponsor).filter(Boolean));
  const countries = new Set(rows.flatMap(t => t.countries));
  const cityKey = i => { const x = S.sites[i]; return x ? x[0] + '|' + x[1] : i; };
  const cities = new Set(rows.flatMap(t => t.sites.map(cityKey)));
  const listings = d3sum(rows.map(t => t.n_locations));
  const k = [
    ['Trials', rows.length, 'Unique NCT numbers'],
    ['Active now', active.length, `${fmtPct(pct(active.length, rows.length))} of these trials`],
    ['Phase 3 ongoing', p3.length, 'Phase 3 or 2/3, active status'],
    ['Trial participants', act + est, `Interventional only: ${fmt(act)} actual, ${fmt(est)} estimated${nobs ? `. Observational studies (not counted): ${fmt(obs)}` : ''}`],
    ['Lead sponsors', sponsors.size, 'Distinct lead sponsor names'],
    ['Countries', countries.size, 'With at least one site'],
    ['Cities', cities.size, `Distinct city and country names · ${fmt(listings)} site listings`],
  ];
  $('#kpis').innerHTML = k.map(([l, v, d]) => `<div class="kpi" data-nocheck><div class="v">${fmt(v)}</div><div class="l">${l}</div><div class="d">${esc(d)}</div></div>`).join('');
}
const d3sum = a => a.reduce((s, v) => s + (+v || 0), 0);

// ------------------------------------------------------- heat table ---
function renderHeat(rows) {
  const drugs = shownDrugs();
  const phases = PHASE_ORDER.filter(p => rows.some(t => t.phase === p));
  rows.forEach(t => { if (!phases.includes(t.phase)) phases.push(t.phase); });
  if (!rows.length) { $('#heat').innerHTML = '<div class="empty">No trials match these filters.</div>'; return; }
  const m = {}; let max = 1;
  rows.forEach(t => drugsOf(t).forEach(d => { const k = d + '|' + t.phase; m[k] = (m[k] || 0) + 1; max = Math.max(max, m[k]); }));
  const cell = (v, d, p) => {
    if (!v) return `<td class="zero">·</td>`;
    const s = Math.sqrt(v / max), pc = Math.round(12 + s * 88);
    return `<td style="background:color-mix(in srgb, var(--heat1) ${pc}%, var(--heat0));color:${pc > 55 ? 'var(--on-accent)' : 'var(--fg)'}" data-d="${d}" data-p="${esc(p)}" title="${esc(drugName(d))} · ${esc(p)}: ${v} trials">${fmt(v)}</td>`;
  };
  const rowTot = d => rows.filter(t => drugsOf(t).includes(d)).length;
  $('#heat').innerHTML = `<table class="heat" data-nocheck><thead><tr><th>Drug</th>${phases.map(p => `<th title="${esc(p)}">${esc(PHASE_SHORT[p] || p)}</th>`).join('')}<th>All</th></tr></thead><tbody>` +
    drugs.map(d => `<tr><th class="rowh"><span class="dot" style="background:${dcol(d)}"></span> ${esc(drugName(d))}</th>${phases.map(p => cell(m[d + '|' + p], d, p)).join('')}<td class="zero" style="font-weight:700;color:var(--fg)">${fmt(rowTot(d))}</td></tr>`).join('') +
    `</tbody><tfoot><tr><th class="rowh">Unique trials</th>${phases.map(p => `<td>${fmt(rows.filter(t => t.phase === p).length)}</td>`).join('')}<td>${fmt(rows.length)}</td></tr></tfoot></table>
    <p class="small muted">Phase columns: ${phases.map(p => `${esc(PHASE_SHORT[p] || p)} = ${esc(p)}`).join(' · ')}. The bottom row counts each trial once, so it can be less than the sum of the rows above.</p>`;
  $('#heat').querySelectorAll('td[data-d]').forEach(td => td.onclick = () => setFilter({ drugs: new Set([td.dataset.d]), phases: new Set([td.dataset.p]) }));
}

// ---------------------------------------------- generic svg pieces ---
function svgBase(sel, w, h) {
  const el = typeof sel === 'string' ? $(sel) : sel;
  el.innerHTML = '';
  return d3.select(el).append('svg').attr('viewBox', `0 0 ${w} ${h}`).attr('width', w).attr('height', h).attr('role', 'img');
}
const widthOf = sel => Math.max(280, Math.floor($(sel).clientWidth || 600));
function table(cols, rows, opts = {}) {
  return `<table class="data"${opts.nocheck !== false ? ' data-nocheck' : ''}><thead><tr>${cols.map(c => `<th class="${c.r ? 'r' : ''}">${esc(c.h)}</th>`).join('')}</tr></thead><tbody>${rows.length ? rows.map(r => `<tr>${cols.map(c => `<td class="${c.r ? 'r' : ''}">${c.html ? c.v(r) : esc(c.v(r))}</td>`).join('')}</tr>`).join('') : `<tr><td colspan="${cols.length}" class="muted">Nothing to show.</td></tr>`}</tbody></table>`;
}
function legend(sel, items) { $(sel).innerHTML = items.map(i => `<span><i class="sw" style="background:${i.col};${i.style || ''}"></i>${esc(i.name)}</span>`).join(''); }

// ---------------------------------------------- status by drug chart ---
function renderStatusChart(rows) {
  const drugs = shownDrugs();
  const groups = GROUPS.filter(g => rows.some(t => groupOf(t.status) === g.id));
  legend('#statusLegend', groups.map(g => ({ name: g.name, col: g.col })));
  if (!rows.length) { $('#statusChart').innerHTML = '<div class="empty">No trials match these filters.</div>'; $('#statusTable').innerHTML = ''; return; }
  const data = drugs.map(d => { const o = { d, total: 0 }; groups.forEach(g => o[g.id] = 0); rows.forEach(t => { if (drugsOf(t).includes(d)) { o[groupOf(t.status)] = (o[groupOf(t.status)] || 0) + 1; o.total++; } }); return o; });
  const W = widthOf('#statusChart'), narrow = W < 520, labW = narrow ? 0 : 175, rowH = narrow ? 44 : 30, M = { t: 6, r: 44, b: 40, l: labW };
  const H = M.t + M.b + data.length * rowH;
  const svg = svgBase('#statusChart', W, H);
  const x = d3.scaleLinear().domain([0, d3.max(data, d => d.total) || 1]).nice().range([M.l, W - M.r]);
  svg.append('g').attr('class', 'grid').attr('transform', `translate(0,${H - M.b})`).call(d3.axisBottom(x).ticks(narrow ? 4 : 6).tickSize(-(H - M.t - M.b)).tickFormat(''));
  svg.append('g').attr('class', 'axis').attr('transform', `translate(0,${H - M.b})`).call(d3.axisBottom(x).ticks(narrow ? 4 : 6).tickFormat(d3.format(','))).append('text').attr('x', W - M.r).attr('y', 34).attr('text-anchor', 'end').text('trials');
  const row = svg.selectAll('g.row').data(data).join('g').attr('class', 'row').attr('transform', (d, i) => `translate(0,${M.t + i * rowH})`);
  const barY = narrow ? 18 : 4, barH = narrow ? 18 : rowH - 8;
  row.append('text').attr('class', 'lbl').attr('x', narrow ? 0 : labW - 8).attr('y', narrow ? 12 : rowH / 2 + 4).attr('text-anchor', narrow ? 'start' : 'end').text(d => drugName(d.d));
  row.each(function (d) {
    let x0 = 0; const g = d3.select(this);
    groups.forEach(gr => {
      const v = d[gr.id]; if (!v) return;
      g.append('rect').attr('x', x(x0)).attr('y', barY).attr('width', Math.max(1, x(x0 + v) - x(x0))).attr('height', barH).style('fill', gr.col)
        .on('pointermove pointerdown', ev => showTip(`<b>${esc(drugName(d.d))}</b><br>${gr.name}: ${fmt(v)} of ${fmt(d.total)} trials (${fmtPct(pct(v, d.total))})`, ev)).on('pointerleave', hideTip);
      x0 += v;
    });
    g.append('text').attr('class', 'lbl-strong').attr('x', x(d.total) + 5).attr('y', barY + barH / 2 + 4).text(fmt(d.total));
  });
  $('#statusTable').innerHTML = table([{ h: 'Drug', v: r => drugName(r.d) }, ...groups.map(g => ({ h: g.name, r: 1, v: r => fmt(r[g.id]) })), { h: 'Total', r: 1, v: r => fmt(r.total) }], data);
}

// ----------------------------------------------------- timeline ---
function renderTimeline(rows) {
  const key = S.tlMode === 'primary' ? 'primary_completion' : 'completion';
  const drugs = shownDrugs();
  const pts = [];
  rows.filter(t => isP3(t) && groupOf(t.status) === 'active').forEach(t => {
    const dt = parseDate(t[key]);
    drugsOf(t).forEach(d => pts.push({ t, d, dt }));
  });
  const withDate = pts.filter(p => p.dt), noDate = new Set(pts.filter(p => !p.dt).map(p => p.t.id));
  const uniq = [...new Map(withDate.map(p => [p.t.id, p.t])).values()].sort((a, b) => (a[key] || '').localeCompare(b[key] || ''));
  const lanes = drugs.filter(d => withDate.some(p => p.d === d));
  if (!withDate.length) {
    $('#tlChart').innerHTML = '<div class="empty">No ongoing phase 3 trials match these filters.</div>';
    $('#tlTable').innerHTML = ''; return;
  }
  const W = widthOf('#tlChart'), narrow = W < 600, laneH = narrow ? 54 : 46, M = { t: 26, r: 16, b: 30, l: narrow ? 8 : 180 };
  const H = M.t + M.b + lanes.length * laneH;
  const svg = svgBase('#tlChart', W, H);
  const ext = d3.extent(withDate, p => p.dt);
  const lo = d3.min([ext[0], today]), hi = d3.max([ext[1], today]);
  const x = d3.scaleUtc().domain([d3.utcMonth.offset(lo, -2), d3.utcMonth.offset(hi, 2)]).range([M.l, W - M.r]);
  const r = d3.scaleSqrt().domain([0, d3.max(withDate, p => p.t.enrollment || 0) || 1]).range([2.5, narrow ? 10 : 14]);
  svg.append('g').attr('class', 'grid').attr('transform', `translate(0,${H - M.b})`).call(d3.axisBottom(x).ticks(narrow ? 4 : 8).tickSize(-(H - M.t - M.b)).tickFormat(''));
  svg.append('g').attr('class', 'axis').attr('transform', `translate(0,${H - M.b})`).call(d3.axisBottom(x).ticks(narrow ? 4 : 8));
  const tx = x(today);
  const tg = svg.append('g').attr('class', 'today');
  tg.append('line').attr('x1', tx).attr('x2', tx).attr('y1', M.t - 6).attr('y2', H - M.b);
  tg.append('text').attr('x', tx).attr('y', M.t - 10).attr('text-anchor', tx > W - 80 ? 'end' : 'middle').text('Today');
  lanes.forEach((d, i) => {
    const y0 = M.t + i * laneH;
    svg.append('line').attr('x1', narrow ? 0 : 0).attr('x2', W - M.r).attr('y1', y0 + laneH).attr('y2', y0 + laneH).style('stroke', 'var(--rule)');
    svg.append('text').attr('class', 'lbl-strong').attr('x', narrow ? 2 : M.l - 10).attr('y', narrow ? y0 + 12 : y0 + laneH / 2 + 4).attr('text-anchor', narrow ? 'start' : 'end').style('fill', dcol(d)).text(drugName(d) + ` (${withDate.filter(p => p.d === d).length})`);
  });
  const hash = s => { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) >>> 0; return h / 4294967295; };
  svg.append('g').selectAll('circle').data(withDate).join('circle')
    .attr('cx', p => x(p.dt))
    .attr('cy', p => { const i = lanes.indexOf(p.d), y0 = M.t + i * laneH, top = narrow ? 20 : 8; return y0 + top + hash(p.t.id) * (laneH - top - 8); })
    .attr('r', p => r(p.t.enrollment || 0)).style('fill', p => dcol(p.d)).style('fill-opacity', 0.55).style('stroke', p => dcol(p.d)).style('stroke-width', 1.2).style('cursor', 'pointer')
    .on('pointermove', (ev, p) => showTip(tlTip(p.t, key), ev)).on('pointerleave', hideTip)
    .on('click', (ev, p) => openTrial(p.t.id));
  const past = uniq.filter(t => parseDate(t[key]) < today).length;
  $('#tlTable').innerHTML = `<p class="small muted">${fmt(uniq.length)} trials with a date${noDate.size ? `; ${fmt(noDate.size)} more give no ${S.tlMode === 'primary' ? 'primary' : 'study'} completion date and are not shown` : ''}${past ? `; ${fmt(past)} are still listed as ongoing although their date has passed` : ''}.</p>` +
    trialMini(uniq, key);
  bindTrialLinks('#tlTable');
}
const tlTip = (t, key) => `<b>${esc(t.acronym ? t.acronym + ' · ' : '')}${esc(t.id)}</b><br>${esc(t.title)}<br>${key === 'completion' ? 'Study' : 'Primary'} completion: ${esc(t[key])}${key === 'primary_completion' && t.primary_completion_type ? ' (' + esc(t.primary_completion_type.toLowerCase()) + ')' : ''}<br>Enrollment: ${fmt(t.enrollment)} · ${esc(t.sponsor)}<br><span style="opacity:.8">Tap to open</span>`;
function trialMini(list, key) {
  return table([
    { h: 'Trial', html: 1, v: t => `<button class="linkish" data-nct="${esc(t.id)}">${esc(t.id)}</button>${t.acronym ? ' ' + esc(t.acronym) : ''}` },
    { h: 'Drugs', html: 1, v: t => drugDots(t) },
    { h: key === 'completion' ? 'Study completion' : 'Primary completion', v: t => (t[key] || '—') + (key === 'primary_completion' && t.primary_completion_type === 'ESTIMATED' ? ' (est.)' : '') },
    { h: 'Enrollment', r: 1, v: t => fmt(t.enrollment) },
    { h: 'Sponsor', v: t => t.sponsor || '—' },
  ], list);
}
const drugDots = t => `<span class="drugdots" title="${esc(t.drugs.map(drugName).join(', '))}">${t.drugs.map(d => `<span class="dot" style="background:${dcol(d)}"></span>`).join('')}</span>`;
function bindTrialLinks(sel) { $(sel).querySelectorAll('[data-nct]').forEach(b => b.onclick = e => { e.stopPropagation(); openTrial(b.dataset.nct); }); }
$('#tlMode').addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; S.tlMode = b.dataset.v; $('#tlMode').querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', x === b)); renderTimeline(filtered()); });

// ------------------------------------------------ starts per year ---
function renderStarts(rows) {
  const drugs = shownDrugs();
  const yrs = {}; let lo = 9999, hi = 0;
  rows.forEach(t => { const y = t.start ? +t.start.slice(0, 4) : null; if (!y) return; lo = Math.min(lo, y); hi = Math.max(hi, y); drugsOf(t).forEach(d => { const k = d + '|' + y; yrs[k] = (yrs[k] || 0) + 1; }); });
  const active = drugs.filter(d => Object.keys(yrs).some(k => k.startsWith(d + '|')));
  legend('#startsLegend', active.map(d => ({ name: drugName(d), col: dcol(d) })));
  if (!active.length) { $('#startsChart').innerHTML = '<div class="empty">No trials with a start date match these filters.</div>'; $('#startsTable').innerHTML = ''; return; }
  const years = d3.range(lo, hi + 1);
  const W = widthOf('#startsChart'), H = Math.round(Math.min(360, Math.max(240, W * 0.55))), M = { t: 10, r: 12, b: 28, l: 40 };
  const svg = svgBase('#startsChart', W, H);
  const x = d3.scaleLinear().domain([lo, hi]).range([M.l, W - M.r]);
  const ymax = d3.max(active.flatMap(d => years.map(y => yrs[d + '|' + y] || 0))) || 1;
  const y = d3.scaleLinear().domain([0, ymax]).nice().range([H - M.b, M.t]);
  const cy = today.getUTCFullYear();
  if (hi >= cy) svg.append('rect').attr('x', x(Math.max(lo, cy))).attr('y', M.t).attr('width', x(hi) - x(Math.max(lo, cy)) + 2).attr('height', H - M.t - M.b).style('fill', 'var(--surface)');
  if (hi >= cy) { const lx = x(Math.max(lo, cy)), right = lx > W - 150; svg.append('text').attr('x', right ? W - M.r : lx + 4).attr('y', M.t + 12).attr('text-anchor', right ? 'end' : 'start').text(`${cy} so far + planned →`); }
  svg.append('g').attr('class', 'grid').attr('transform', `translate(${M.l},0)`).call(d3.axisLeft(y).ticks(5).tickSize(-(W - M.l - M.r)).tickFormat(''));
  svg.append('g').attr('class', 'axis').attr('transform', `translate(0,${H - M.b})`).call(d3.axisBottom(x).ticks(Math.min(years.length, W < 500 ? 5 : 10)).tickFormat(d3.format('d')));
  svg.append('g').attr('class', 'axis').attr('transform', `translate(${M.l},0)`).call(d3.axisLeft(y).ticks(5)).append('text').attr('x', -M.l + 2).attr('y', M.t - 2).attr('text-anchor', 'start').text('');
  svg.append('text').attr('x', M.l + 4).attr('y', M.t + 12).attr('class', 'lbl').text('trials started');
  const line = d3.line().x(d => x(d[0])).y(d => y(d[1]));
  active.forEach(d => {
    const series = years.map(yy => [yy, yrs[d + '|' + yy] || 0]);
    svg.append('path').datum(series).attr('d', line).style('fill', 'none').style('stroke', dcol(d)).style('stroke-width', 2);
    svg.append('g').selectAll('circle').data(series.filter(s => s[1])).join('circle').attr('cx', s => x(s[0])).attr('cy', s => y(s[1])).attr('r', 3).style('fill', dcol(d))
      .on('pointermove pointerdown', (ev, s) => showTip(`<b>${esc(drugName(d))}</b><br>${s[0]}: ${fmt(s[1])} trials started${s[0] > cy ? ' (planned)' : ''}`, ev)).on('pointerleave', hideTip);
  });
  $('#startsTable').innerHTML = table([{ h: 'Year', v: r => r > cy ? `${r} (planned)` : r === cy ? `${r} (to date + planned)` : r }, ...active.map(d => ({ h: drugName(d), r: 1, v: r => fmt(yrs[d + '|' + r] || 0) }))], years.slice().reverse());
}

// ------------------------------------------------------ enrollment ---
function renderEnrollment(rows) {
  const drugs = shownDrugs();
  const data = drugs.map(d => { const o = { d, act: 0, est: 0, n: 0, none: 0 }; rows.forEach(t => { if (!drugsOf(t).includes(d) || !isInt(t)) return; o.n++; if (t.enrollment == null) o.none++; else if (t.enrollment_type === 'ACTUAL') o.act += t.enrollment; else o.est += t.enrollment; }); o.tot = o.act + o.est; return o; }).filter(o => o.n);
  legend('#enrLegend', [{ name: 'Actual', col: 'var(--fg)' }, { name: 'Estimated (planned target; final number not reported)', col: 'var(--fg)', style: 'opacity:.35' }]);
  if (!data.length) { $('#enrChart').innerHTML = '<div class="empty">No trials match these filters.</div>'; $('#enrTable').innerHTML = ''; $('#enrTop').innerHTML = ''; return; }
  const W = widthOf('#enrChart'), narrow = W < 520, labW = narrow ? 0 : 175, rowH = narrow ? 44 : 30, M = { t: 6, r: 70, b: 40, l: labW };
  const H = M.t + M.b + data.length * rowH;
  const svg = svgBase('#enrChart', W, H);
  const x = d3.scaleLinear().domain([0, d3.max(data, d => d.tot) || 1]).nice().range([M.l, W - M.r]);
  const tf = d3.format('~s');
  svg.append('g').attr('class', 'grid').attr('transform', `translate(0,${H - M.b})`).call(d3.axisBottom(x).ticks(narrow ? 3 : 5).tickSize(-(H - M.t - M.b)).tickFormat(''));
  svg.append('g').attr('class', 'axis').attr('transform', `translate(0,${H - M.b})`).call(d3.axisBottom(x).ticks(narrow ? 3 : 5).tickFormat(tf)).append('text').attr('x', W - M.r).attr('y', 34).attr('text-anchor', 'end').text('participants');
  const barY = narrow ? 18 : 4, barH = narrow ? 18 : rowH - 8;
  data.forEach((o, i) => {
    const g = svg.append('g').attr('transform', `translate(0,${M.t + i * rowH})`);
    g.append('text').attr('class', 'lbl').attr('x', narrow ? 0 : labW - 8).attr('y', narrow ? 12 : rowH / 2 + 4).attr('text-anchor', narrow ? 'start' : 'end').text(drugName(o.d));
    const tipf = ev => showTip(`<b>${esc(drugName(o.d))}</b> · ${fmt(o.n)} trials<br>Actual: ${fmt(o.act)}<br>Estimated: ${fmt(o.est)}${o.none ? `<br>${fmt(o.none)} trials give no number` : ''}`, ev);
    g.append('rect').attr('x', x(0)).attr('y', barY).attr('width', Math.max(0, x(o.act) - x(0))).attr('height', barH).style('fill', dcol(o.d)).on('pointermove pointerdown', tipf).on('pointerleave', hideTip);
    g.append('rect').attr('x', x(o.act)).attr('y', barY).attr('width', Math.max(0, x(o.tot) - x(o.act))).attr('height', barH).style('fill', dcol(o.d)).style('opacity', 0.35).on('pointermove pointerdown', tipf).on('pointerleave', hideTip);
    g.append('text').attr('class', 'lbl-strong').attr('x', x(o.tot) + 5).attr('y', barY + barH / 2 + 4).text(fmt(o.tot));
  });
  $('#enrTable').innerHTML = table([{ h: 'Drug', v: r => drugName(r.d) }, { h: 'Trials', r: 1, v: r => fmt(r.n) }, { h: 'Actual', r: 1, v: r => fmt(r.act) }, { h: 'Estimated', r: 1, v: r => fmt(r.est) }, { h: 'Total', r: 1, v: r => fmt(r.tot) }, { h: 'No number given', r: 1, v: r => fmt(r.none) }], data);
  const nObs = rows.filter(t => !isInt(t)).length;
  $('#enrNote').textContent = nObs ? `Interventional trials only. ${fmt(nObs)} observational or expanded-access records in this selection are left out: many are health-record database studies listing hundreds of thousands to millions of people, which would swamp the trial numbers.` : 'Interventional trials only.';
  const top = rows.filter(t => t.enrollment != null && isInt(t)).sort((a, b) => b.enrollment - a.enrollment).slice(0, 10);
  $('#enrTop').innerHTML = table([
    { h: 'Trial', html: 1, v: t => `<button class="linkish" data-nct="${esc(t.id)}">${esc(t.id)}</button>${t.acronym ? ' ' + esc(t.acronym) : ''}` },
    { h: 'Drugs', html: 1, v: drugDots }, { h: 'Phase', v: t => t.phase }, { h: 'Status', v: t => STATUS_NAME(t.status) },
    { h: 'Enrollment', r: 1, v: t => fmt(t.enrollment) + (t.enrollment_type === 'ESTIMATED' ? ' (est.)' : '') },
  ], top);
  bindTrialLinks('#enrTop');
}

// -------------------------------------------------------- sponsors ---
function renderSponsors(rows) {
  const m = new Map();
  rows.forEach(t => {
    const k = t.sponsor || '(not given)';
    if (!m.has(k)) m.set(k, { name: k, cls: t.sponsor_class, trials: 0, active: 0, p3: 0, ppl: 0, drugs: new Set(), countries: new Set() });
    const o = m.get(k); o.trials++; if (groupOf(t.status) === 'active') o.active++; if (isP3(t)) o.p3++; if (isInt(t)) o.ppl += t.enrollment || 0;
    t.drugs.forEach(d => o.drugs.add(d)); t.countries.forEach(c => o.countries.add(c));
  });
  let list = [...m.values()];
  const { key, dir } = S.spSort;
  list.sort((a, b) => { const va = key === 'drugs' ? a.drugs.size : a[key], vb = key === 'drugs' ? b.drugs.size : b[key]; return (typeof va === 'string' ? va.localeCompare(vb) : (va - vb)) * dir || b.trials - a.trials || a.name.localeCompare(b.name); });
  list.forEach((o, i) => o.rank = i + 1);
  const q = S.spQuery.toLowerCase();
  const shown = q ? list.filter(o => o.name.toLowerCase().includes(q)) : list;
  const cls = { INDUSTRY: 'Industry', OTHER: 'Academic/other', NIH: 'NIH', FED: 'US federal', OTHER_GOV: 'Government', NETWORK: 'Network', INDIV: 'Individual', AMBIG: 'Unclear', UNKNOWN: 'Unknown' };
  const cols = [['rank', '#', 1], ['name', 'Lead sponsor'], ['cls', 'Type'], ['trials', 'Trials', 1], ['active', 'Active', 1], ['p3', 'Phase 3 (any status)', 1], ['ppl', 'Participants', 1], ['drugs', 'Drugs']];
  if (!rows.length) { $('#sponsorTable').innerHTML = '<div class="empty">No trials match these filters.</div>'; $('#sponsorPager').innerHTML = ''; return; }
  $('#sponsorTable').innerHTML = `<table class="data" data-nocheck><thead><tr>${cols.map(([k, h, r]) => `<th class="sortable ${r ? 'r' : ''}" data-k="${k}" ${S.spSort.key === k ? `aria-sort="${dir > 0 ? 'ascending' : 'descending'}"` : ''}>${h}</th>`).join('')}</tr></thead><tbody>` +
    shown.slice(0, S.spShow).map(o => `<tr><td class="r rank">${o.rank}</td><td><button class="linkish" data-sp="${esc(o.name)}">${esc(o.name)}</button></td><td class="small">${esc(cls[o.cls] || o.cls || '—')}</td><td class="r">${fmt(o.trials)}</td><td class="r">${fmt(o.active)}</td><td class="r">${fmt(o.p3)}</td><td class="r">${fmt(o.ppl)}</td><td><span class="drugdots" title="${esc([...o.drugs].map(drugName).join(', '))}">${[...o.drugs].map(d => `<span class="dot" style="background:${dcol(d)}"></span>`).join('')}</span></td></tr>`).join('') +
    (shown.length ? '' : `<tr><td colspan="8" class="muted">No sponsor matches “${esc(S.spQuery)}”.</td></tr>`) + '</tbody></table>';
  $('#sponsorPager').innerHTML = `<span class="muted">Showing ${fmt(Math.min(S.spShow, shown.length))} of ${fmt(shown.length)} sponsors. “Participants” sums actual and estimated enrollment of interventional trials only; “Drugs” counts all drugs each trial lists.</span>` +
    (shown.length > S.spShow ? `<span><button class="btn" id="spMore">Show ${fmt(Math.min(25, shown.length - S.spShow))} more</button> <button class="btn" id="spAll">Show all</button></span>` : (S.spShow > 15 ? '<button class="btn" id="spLess">Show fewer</button>' : ''));
  $('#sponsorTable').querySelectorAll('th.sortable').forEach(th => th.onclick = () => { const k = th.dataset.k; S.spSort = { key: k, dir: S.spSort.key === k ? -S.spSort.dir : (k === 'name' || k === 'cls' || k === 'rank' ? 1 : -1) }; if (k === 'rank') S.spSort = { key: 'trials', dir: -1 }; renderSponsors(filtered()); });
  $('#sponsorTable').querySelectorAll('[data-sp]').forEach(b => b.onclick = () => { setFilter({ sponsor: b.dataset.sp === '(not given)' ? '' : b.dataset.sp }); $('#headline').scrollIntoView(); });
  const more = $('#spMore'); if (more) more.onclick = () => { S.spShow += 25; renderSponsors(filtered()); };
  const all = $('#spAll'); if (all) all.onclick = () => { S.spShow = 1e6; renderSponsors(filtered()); };
  const less = $('#spLess'); if (less) less.onclick = () => { S.spShow = 15; renderSponsors(filtered()); };
}
$('#sponsorSearch').addEventListener('input', debounce(e => { S.spQuery = e.target.value.trim(); renderSponsors(filtered()); }, 200));

// ------------------------------------------------------------- map ---
const ALIAS = {
  'United States': 'United States of America', 'Korea, Republic of': 'South Korea', 'Russian Federation': 'Russia',
  'Czech Republic': 'Czechia', 'Czechia': 'Czechia', 'Iran, Islamic Republic of': 'Iran', 'Taiwan': 'Taiwan',
  'Bosnia and Herzegovina': 'Bosnia and Herz.', 'Dominican Republic': 'Dominican Rep.', 'Türkiye': 'Turkey', 'Turkey (Türkiye)': 'Turkey',
  'Moldova, Republic of': 'Moldova', 'North Macedonia': 'Macedonia', 'Macedonia, The Former Yugoslav Republic of': 'Macedonia',
  'Syrian Arab Republic': 'Syria', 'Viet Nam': 'Vietnam', "Lao People's Democratic Republic": 'Laos', 'Tanzania': 'Tanzania',
  'Côte D\'Ivoire': "Côte d'Ivoire", "Cote D'Ivoire": "Côte d'Ivoire", 'Congo, The Democratic Republic of the': 'Dem. Rep. Congo',
  'Central African Republic': 'Central African Rep.', 'South Sudan': 'S. Sudan', 'Equatorial Guinea': 'Eq. Guinea',
  'Libyan Arab Jamahiriya': 'Libya', 'Palestinian Territory, occupied': 'Palestine', 'Puerto Rico': 'Puerto Rico',
  'Brunei Darussalam': 'Brunei', 'Swaziland': 'eSwatini', 'Eswatini': 'eSwatini', 'Solomon Islands': 'Solomon Is.', 'Falkland Islands (Malvinas)': 'Falkland Is.',
};
async function loadWorld() {
  if (S.world || S.worldErr) return;
  try {
    const topo = await getJSON('https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-110m.json');
    S.world = topojson.feature(topo, topo.objects.countries).features;
  } catch (e) { S.worldErr = e.message; }
}
let mapZoom = null;
async function renderMap(rows) {
  const box = $('#mapBox');
  const cnt = {}, cities = {};
  rows.forEach(t => {
    t.countries.forEach(c => { cnt[c] = cnt[c] || { trials: 0, cities: new Set() }; cnt[c].trials++; });
    const seen = new Set();
    t.sites.forEach(i => { const s = S.sites[i]; if (!s) return; const k = s[0] + '|' + s[1]; if (cnt[s[1]]) cnt[s[1]].cities.add(k); if (seen.has(k)) return; seen.add(k); if (!cities[k]) cities[k] = { s, n: 0 }; cities[k].n++; });
  });
  const ctry = Object.entries(cnt).map(([c, o]) => ({ c, trials: o.trials, cities: o.cities.size })).sort((a, b) => b.trials - a.trials || a.c.localeCompare(b.c));
  $('#countryTable').innerHTML = ctry.length ? table([{ h: '#', r: 1, v: r => ctry.indexOf(r) + 1 }, { h: 'Country', v: r => r.c }, { h: 'Trials', r: 1, v: r => fmt(r.trials) }, { h: 'Cities', r: 1, v: r => fmt(r.cities) }], ctry) : '<div class="empty">No sites listed for these trials.</div>';
  if (typeof topojson === 'undefined') { box.innerHTML = '<div class="empty">The map library did not load (cdn.jsdelivr.net), so the map cannot be drawn. The country table still works.</div>'; return; }
  await loadWorld();
  if (S.worldErr) { box.innerHTML = `<div class="empty">The base map could not be loaded (${esc(S.worldErr)}). The country table still works.</div>`; return; }
  const W = widthOf('#mapBox'), H = Math.round(W * 0.52);
  box.innerHTML = '';
  const svg = d3.select(box).append('svg').attr('viewBox', `0 0 ${W} ${H}`).attr('role', 'img').attr('aria-label', 'World map of trial sites');
  const proj = d3.geoNaturalEarth1().fitExtent([[4, 4], [W - 4, H - 4]], { type: 'Sphere' });
  const path = d3.geoPath(proj);
  const g = svg.append('g');
  const byName = {}; ctry.forEach(c => byName[ALIAS[c.c] || c.c] = c);
  const max = d3.max(ctry, c => c.trials) || 1;
  const c0 = cssv('--heat0'), c1 = cssv('--heat1'), land = cssv('--land');
  const col = d3.scaleSequentialLog(d3.interpolateRgb(c0, c1)).domain([1, Math.max(2, max)]);
  g.append('path').datum({ type: 'Sphere' }).attr('d', path).style('fill', 'var(--card)').style('stroke', 'var(--rule)');
  g.selectAll('path.country').data(S.world).join('path').attr('class', 'country').attr('d', path)
    .style('fill', f => byName[f.properties.name] ? col(byName[f.properties.name].trials) : land)
    .on('pointermove', (ev, f) => { const c = byName[f.properties.name]; showTip(`<b>${esc(f.properties.name)}</b><br>${c ? `${fmt(c.trials)} trials · ${fmt(c.cities)} cities` : 'No matching trials'}`, ev); })
    .on('pointerleave', hideTip);
  const pts = Object.values(cities);
  const rmax = d3.max(pts, p => p.n) || 1;
  const r = d3.scaleSqrt().domain([1, rmax]).range([1.2, W < 600 ? 7 : 11]);
  const pg = g.append('g');
  const dots = pg.selectAll('circle').data(pts.sort((a, b) => b.n - a.n)).join('circle').attr('class', 'site')
    .attr('transform', p => { const xy = proj([p.s[3], p.s[2]]); return xy ? `translate(${xy[0]},${xy[1]})` : 'translate(-99,-99)'; })
    .attr('r', p => r(p.n)).style('fill', 'var(--d-tirzepatide)')
    .on('pointermove', (ev, p) => showTip(`<b>${esc(p.s[0] || 'Unnamed city')}, ${esc(p.s[1])}</b><br>${fmt(p.n)} trial${p.n > 1 ? 's' : ''} with a site here`, ev)).on('pointerleave', hideTip);
  mapZoom = d3.zoom().scaleExtent([1, 14]).translateExtent([[0, 0], [W, H]]).on('zoom', ev => { g.attr('transform', ev.transform); dots.attr('r', p => r(p.n) / Math.sqrt(ev.transform.k)).style('stroke-width', 0.6 / ev.transform.k); g.selectAll('path.country').style('stroke-width', 0.5 / ev.transform.k); });
  svg.call(mapZoom);
  const z = document.createElement('div'); z.className = 'mapzoom';
  z.innerHTML = '<button aria-label="Zoom in">+</button><button aria-label="Zoom out">−</button><button aria-label="Reset zoom" style="font-size:.7rem">⟲</button>';
  box.appendChild(z);
  const [zi, zo, zr] = z.querySelectorAll('button');
  zi.onclick = () => svg.transition().duration(250).call(mapZoom.scaleBy, 1.6);
  zo.onclick = () => svg.transition().duration(250).call(mapZoom.scaleBy, 1 / 1.6);
  zr.onclick = () => svg.transition().duration(250).call(mapZoom.transform, d3.zoomIdentity);
  const unmatched = ctry.filter(c => !S.world.some(f => f.properties.name === (ALIAS[c.c] || c.c)));
  $('#mapKey').innerHTML = `<span>Trials per country</span><span class="ramp" style="background:linear-gradient(90deg, ${c0}, ${c1})"></span><span>1 to ${fmt(max)} (log scale)</span><span>· <i class="dot" style="background:var(--d-tirzepatide)"></i> city with sites (${fmt(pts.length)})</span>` +
    (unmatched.length ? `<span>· Not shaded (too small for this map or name not matched): ${esc(unmatched.map(c => c.c).join(', '))}</span>` : '');
}

// ------------------------------------------------------ trial table ---
const TCOLS = [
  { k: 'id', h: 'Trial' }, { k: 'title', h: 'Title' }, { k: 'drugs', h: 'Drugs' }, { k: 'phase', h: 'Phase' }, { k: 'status', h: 'Status' },
  { k: 'sponsor', h: 'Lead sponsor' }, { k: 'enrollment', h: 'Enrollment', r: 1 }, { k: 'start', h: 'Start' }, { k: 'primary_completion', h: 'Primary completion' },
];
function renderTrialTable(rows) {
  const PER = 25;
  const { key, dir } = S.sort;
  const val = t => key === 'drugs' ? t.drugs.length : key === 'phase' ? PHASE_ORDER.indexOf(t.phase) : t[key];
  const list = rows.slice().sort((a, b) => { const va = val(a), vb = val(b); if (va == null && vb == null) return 0; if (va == null) return 1; if (vb == null) return -1; return (typeof va === 'number' ? va - vb : String(va).localeCompare(String(vb))) * dir; });
  const pages = Math.max(1, Math.ceil(list.length / PER)); S.page = Math.min(S.page, pages - 1);
  const pg = list.slice(S.page * PER, S.page * PER + PER);
  if (!rows.length) { $('#trialTable').innerHTML = '<div class="empty">No trials match these filters. <button class="btn" onclick="this.closest(\'main\').querySelector(\'#resetBtn\')?.click()">Clear filters</button></div>'; $('#trialPager').innerHTML = ''; return; }
  $('#trialTable').innerHTML = `<table class="data" data-nocheck><thead><tr>${TCOLS.map(c => `<th class="sortable ${c.r ? 'r' : ''}" data-k="${c.k}" ${key === c.k ? `aria-sort="${dir > 0 ? 'ascending' : 'descending'}"` : ''}>${c.h}</th>`).join('')}</tr></thead><tbody>` +
    pg.map(t => `<tr class="click" data-nct="${esc(t.id)}"><td class="nowrap"><button class="linkish" data-nct="${esc(t.id)}">${esc(t.id)}</button></td><td style="min-width:16rem">${t.acronym ? `<strong>${esc(t.acronym)}</strong> · ` : ''}${esc(t.title)}</td><td>${drugDots(t)}</td><td class="nowrap">${esc(t.phase === 'No phase (observational/other)' ? 'No phase' : t.phase)}</td><td><span class="badge st-${groupOf(t.status)}">${esc(STATUS_NAME(t.status))}</span></td><td>${esc(t.sponsor || '—')}</td><td class="r">${fmt(t.enrollment)}${t.enrollment_type === 'ESTIMATED' ? '<span class="muted small"> est.</span>' : ''}</td><td class="nowrap">${esc(t.start || '—')}</td><td class="nowrap">${esc(t.primary_completion || '—')}</td></tr>`).join('') + '</tbody></table>';
  $('#trialPager').innerHTML = `<span class="muted">${fmt(S.page * PER + 1)}–${fmt(S.page * PER + pg.length)} of ${fmt(list.length)}${key === 'last_update' ? ', most recently updated record first' : ''}</span><span><button class="btn" id="tPrev" ${S.page ? '' : 'disabled'}>Previous</button> <span class="muted">Page ${S.page + 1} of ${pages}</span> <button class="btn" id="tNext" ${S.page < pages - 1 ? '' : 'disabled'}>Next</button></span>`;
  $('#trialTable').querySelectorAll('th.sortable').forEach(th => th.onclick = () => { const k = th.dataset.k; S.sort = { key: k, dir: S.sort.key === k ? -S.sort.dir : (['enrollment', 'start', 'primary_completion', 'drugs'].includes(k) ? -1 : 1) }; S.page = 0; renderTrialTable(filtered()); });
  $('#trialTable').querySelectorAll('tr.click').forEach(tr => tr.onclick = () => openTrial(tr.dataset.nct));
  bindTrialLinks('#trialTable');
  $('#tPrev').onclick = () => { S.page--; renderTrialTable(filtered()); $('#trials').scrollIntoView(); };
  $('#tNext').onclick = () => { S.page++; renderTrialTable(filtered()); $('#trials').scrollIntoView(); };
}
$('#csvBtn').addEventListener('click', () => {
  const rows = filtered();
  const cols = ['id', 'acronym', 'title', 'drugs', 'phase', 'status', 'type', 'sponsor', 'sponsor_class', 'enrollment', 'enrollment_type', 'start', 'primary_completion', 'primary_completion_type', 'completion', 'countries', 'n_locations', 'last_update'];
  const q = v => { const s = Array.isArray(v) ? v.join('; ') : (v ?? ''); return /[",\n]/.test(s) ? `"${String(s).replace(/"/g, '""')}"` : s; };
  const csv = [cols.join(','), ...rows.map(t => cols.map(c => q(c === 'drugs' ? t.drugs.map(drugName) : t[c])).join(','))].join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob(['﻿' + csv], { type: 'text/csv' }));
  a.download = `glp1-trials-${new Date().toISOString().slice(0, 10)}.csv`; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
});

// ---------------------------------------------------- trial dialog ---
const dlg = $('#trialDlg');
$('#dlgClose').onclick = () => dlg.close();
dlg.addEventListener('click', e => { if (e.target === dlg) dlg.close(); });
async function openTrial(nct) {
  hideTip();
  const t = S.trials.find(x => x.id === nct);
  $('#dlgKicker').textContent = nct;
  $('#dlgTitle').textContent = t ? t.title : nct;
  $('#dlgBody').innerHTML = `<p class="skeleton">Fetching the full record from ClinicalTrials.gov…</p><p><a href="https://clinicaltrials.gov/study/${esc(nct)}" target="_blank" rel="noopener">Open ${esc(nct)} on ClinicalTrials.gov ↗</a></p>`;
  if (!dlg.open) dlg.showModal();
  let r;
  try { r = await getJSON('/api/trial/' + encodeURIComponent(nct)); }
  catch (e) { $('#dlgBody').innerHTML = `<div class="banner bad"><strong>The full record could not be fetched:</strong> ${esc(e.message)}</div>${t ? dlgFromList(t) : ''}<p><a class="btn primary" href="https://clinicaltrials.gov/study/${esc(nct)}" target="_blank" rel="noopener">Open on ClinicalTrials.gov ↗</a></p>`; return; }
  if ($('#dlgKicker').textContent !== nct) return;
  $('#dlgTitle').textContent = r.title || nct;
  $('#dlgKicker').textContent = `${nct}${r.acronym ? ' · ' + r.acronym : ''}`;
  const fact = (l, v) => v ? `<div><dt>${l}</dt><dd>${v}</dd></div>` : '';
  const dt = (d, ty) => d ? esc(d) + (ty === 'ESTIMATED' ? ' <span class="muted">(estimated)</span>' : ty === 'ACTUAL' ? ' <span class="muted">(actual)</span>' : '') : '';
  $('#dlgBody').innerHTML = `
    ${r.stale ? `<div class="banner warn">ClinicalTrials.gov did not answer just now (${esc(r.error)}); showing the copy fetched ${when(r.fetched_at)}.</div>` : ''}
    <p><a class="btn primary" href="${esc(r.url)}" target="_blank" rel="noopener">Open on ClinicalTrials.gov ↗</a> <span class="badge st-${groupOf(r.status)}">${esc(STATUS_NAME(r.status))}</span> ${t ? drugDots(t) + ' <span class="small muted">' + esc(t.drugs.map(drugName).join(', ')) + '</span>' : ''}</p>
    ${r.official_title && r.official_title !== r.title ? `<p class="small muted">${esc(r.official_title)}</p>` : ''}
    <dl class="facts" data-nocheck>
      ${fact('Phase', esc(r.phase))}${fact('Study type', esc(STATUS_NAME(r.type)))}
      ${fact('Enrollment', r.enrollment != null ? fmt(r.enrollment) + (r.enrollment_type ? ` <span class="muted">(${esc(r.enrollment_type.toLowerCase())})</span>` : '') : '—')}
      ${fact('Lead sponsor', esc(r.sponsor))}${fact('Collaborators', esc(r.collaborators.join(', ')))}
      ${fact('Start', dt(r.start, r.start_type))}${fact('Primary completion', dt(r.primary_completion, r.primary_completion_type))}${fact('Study completion', dt(r.completion, r.completion_type))}
      ${fact('Allocation / masking', esc([STATUS_NAME(r.allocation), r.masking && STATUS_NAME(r.masking)].filter(x => x && x !== 'Not given').join(' · ')))}
      ${fact('Sites', `${fmt(r.n_locations)} in ${fmt(r.countries.length)} countr${r.countries.length === 1 ? 'y' : 'ies'}`)}
      ${fact('Results posted', r.has_results ? 'Yes' : 'No')}
      ${fact('First posted / last update', esc(`${r.first_posted || '—'} / ${r.last_update || '—'}`))}
    </dl>
    ${r.why_stopped ? `<div class="banner warn"><strong>Why stopped:</strong> ${esc(r.why_stopped)}</div>` : ''}
    ${r.conditions?.length ? `<h3>Conditions</h3><p>${esc(r.conditions.join(' · '))}</p>` : ''}
    ${r.summary ? `<h3>Summary</h3><p style="white-space:pre-line">${esc(r.summary)}</p>` : ''}
    ${r.arms?.length ? `<h3>Arms</h3><ul>${r.arms.map(a => `<li><strong>${esc(a.label)}</strong>${a.type ? ` <span class="muted small">${esc(STATUS_NAME(a.type))}</span>` : ''}${a.description ? '<br><span class="small">' + esc(a.description) + '</span>' : ''}</li>`).join('')}</ul>` : ''}
    ${r.interventions?.length ? `<h3>Interventions</h3><ul>${r.interventions.map(i => `<li>${esc(i.name)} <span class="muted small">${esc(STATUS_NAME(i.type))}</span></li>`).join('')}</ul>` : ''}
    ${r.primary_outcomes?.length ? `<h3>Primary outcome${r.primary_outcomes.length > 1 ? 's' : ''}</h3><ul>${r.primary_outcomes.map(o => `<li>${esc(o.measure)}${o.timeFrame ? ` <span class="muted small">· ${esc(o.timeFrame)}</span>` : ''}</li>`).join('')}</ul><p class="small muted">Plus ${fmt(r.n_secondary_outcomes)} secondary outcomes.</p>` : ''}
    ${r.countries?.length ? `<h3>Sites by country</h3><p class="small">${r.countries.map(([c, n]) => `${esc(c)} (${fmt(n)})`).join(' · ')}</p>` : ''}
    ${r.eligibility?.criteria ? `<details><summary><strong>Eligibility</strong> · ${esc([r.eligibility.sex && STATUS_NAME(r.eligibility.sex), r.eligibility.min_age && 'from ' + r.eligibility.min_age, r.eligibility.max_age && 'to ' + r.eligibility.max_age].filter(Boolean).join(', '))}</summary><div class="criteria">${esc(r.eligibility.criteria)}</div></details>` : ''}
    <p class="prov">Source: <a href="${esc(r.api_url)}" target="_blank" rel="noopener">ClinicalTrials.gov API v2 study record</a>, fetched ${when(r.fetched_at)}.</p>`;
}
const dlgFromList = t => `<dl class="facts"><div><dt>Phase</dt><dd>${esc(t.phase)}</dd></div><div><dt>Status</dt><dd>${esc(STATUS_NAME(t.status))}</dd></div><div><dt>Sponsor</dt><dd>${esc(t.sponsor)}</dd></div><div><dt>Enrollment</dt><dd>${fmt(t.enrollment)}</dd></div><div><dt>Primary completion</dt><dd>${esc(t.primary_completion || '—')}</dd></div></dl><p class="small muted">From the trial list fetched earlier.</p>`;

// =============================================================== FDA ===
function fdaDrugs() {
  if (!S.fda) return [];
  return S.fda.drugs.filter(d => d.faers && (!S.f.drugs.size || S.f.drugs.has(d.drug)));
}
function renderFdaAll() {
  renderBanners();
  if (S.fdaError && !S.fda) {
    ['#fdaCards', '#yrChart', '#rxChart', '#labelTable'].forEach(s => $(s).innerHTML = `<div class="empty">openFDA data could not be loaded: ${esc(S.fdaError)}</div>`);
    $('#provFda').textContent = 'Source: openFDA — not available.'; return;
  }
  if (!S.fda) return;
  const list = fdaDrugs();
  renderFdaCards(list);
  if (hasD3()) { renderYears(list); renderRx(list); }
  renderLabels();
  const at = S.fda.drugs.filter(d => d.events?.fetched_at).map(d => d.events.fetched_at).sort();
  const lu = S.fda.drugs.map(d => d.events?.last_updated).filter(Boolean).sort().pop();
  $('#provFda').innerHTML = `Source: openFDA drug adverse event API (FAERS), counts by <code>receivedate</code> and <code>patient.reaction.reactionmeddrapt.exact</code>, drug matched on <code>patient.drug.openfda.generic_name</code>. Fetched ${at.length ? when(at[0]) : '—'}; openFDA dataset last updated ${lu ? ymd(lu) : '—'}. <a href="#sources">Exact queries</a>.`;
}

function renderFdaCards(list) {
  const el = $('#fdaCards');
  if (!list.length) {
    const sel = [...S.f.drugs].map(drugName).join(', ');
    el.className = 'fda-grid'; el.innerHTML = `<div class="empty" style="grid-column:1/-1">${sel ? `None of the selected drugs (${esc(sel)}) has a US label on openFDA, so there are no FDA reports to show.` : 'No openFDA report data is available yet.'}</div>`; return;
  }
  el.className = `fda-grid n${Math.min(5, list.length)}`;
  el.innerHTML = list.map(d => {
    const e = d.events;
    if (!e) return `<div class="card"><div class="drug-head"><span class="dot" style="background:${dcol(d.drug)}"></span><h3>${esc(d.name)}</h3></div><div class="empty">Not fetched yet.</div></div>`;
    const brands = (d.labels?.brands || []).map(b => b.brand);
    const top = (e.top || []).slice(0, 10), tmax = top[0]?.count || 1;
    const errs = (e.errors || []);
    return `<div class="card" data-nocheck>
      <div class="drug-head"><span class="dot" style="background:${dcol(d.drug)}"></span><h3>${esc(d.name)}</h3></div>
      <div class="brands">${brands.length ? 'US brands on openFDA: ' + esc([...new Set(brands)].join(', ')) : 'No US label on openFDA'}${d.core ? '' : ' · <strong>newly labelled</strong>'}</div>
      ${errs.length ? `<div class="banner warn small">openFDA failed: ${errs.map(esc).join('; ')}</div>` : ''}
      <div class="fda-stats">
        <div><div class="v">${fmt(e.total)}</div><div class="l">reports naming it</div></div>
        <div><div class="v">${fmt(e.serious)}</div><div class="l">marked serious (${fmtPct(pct(e.serious, e.total))} of its reports)</div></div>
      </div>
      <p class="small muted">Received ${e.by_year?.length ? `${e.by_year[0].year} to ${ymd(e.last_receive_date)}` : '—'}.</p>
      <h3 style="font-size:.9rem;margin-top:.5rem">Most reported reactions</h3>
      ${top.length ? `<ol class="rx-list">${top.map(r => `<li><span class="t" title="${esc(r.term)}">${esc(cap(r.term))}</span><span class="num small">${fmt(r.count)}</span><span class="bar"><i style="width:${(100 * r.count / tmax).toFixed(1)}%;background:${dcol(d.drug)}"></i></span></li>`).join('')}</ol>
      <p class="small muted">Reports listing each term; one report can list several.</p>` : '<div class="empty">No reaction counts.</div>'}
      <p class="prov">openFDA, fetched ${when(e.fetched_at)}. <a href="${esc(e.queries?.top || '#')}" target="_blank" rel="noopener">Query ↗</a></p>
    </div>`;
  }).join('');
}
const cap = s => s ? s.charAt(0) + s.slice(1).toLowerCase() : '';

function renderYears(list) {
  const ds = list.filter(d => d.events?.by_year?.length);
  legend('#yrLegend', ds.map(d => ({ name: drugName(d.drug), col: dcol(d.drug) })));
  if (!ds.length) { $('#yrChart').innerHTML = '<div class="empty">No yearly report counts available.</div>'; $('#yrTable').innerHTML = ''; return; }
  const last = ds.map(d => d.events.last_receive_date).filter(Boolean).sort().pop();
  const lastYear = last ? +last.slice(0, 4) : null;
  const partial = last && last.slice(4) !== '1231';
  const years = d3.range(d3.min(ds, d => d.events.by_year[0].year), d3.max(ds, d => d.events.by_year[d.events.by_year.length - 1].year) + 1);
  const val = (d, y) => (d.events.by_year.find(r => r.year === y) || {}).count || 0;
  lineChart('#yrChart', ds.map(d => ({ id: d.drug, pts: years.map(y => [y, val(d, y)]) })), {
    log: S.yrScale === 'log', ylabel: 'reports received', partialYear: partial ? lastYear : null, partialNote: partial ? `${lastYear}: to ${ymd(last)}` : '',
    tip: (id, y, v) => `<b>${esc(drugName(id))}</b><br>${y}: ${fmt(v)} reports received${partial && y === lastYear ? ` (to ${ymd(last)})` : ''}`,
  });
  $('#yrTable').innerHTML = table([{ h: 'Year', v: y => y + (partial && y === lastYear ? ` (to ${ymd(last)})` : '') }, ...ds.map(d => ({ h: drugName(d.drug), r: 1, v: y => fmt(val(d, y)) }))], years.slice().reverse()) +
    `<p class="small muted">Year totals: ${ds.map(d => `${esc(drugName(d.drug))} ${fmt(d3sum(d.events.by_year.map(r => r.count)))} (total reports ${fmt(d.events.total)})`).join(' · ')}.</p>`;
}
$('#yrScale').addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; S.yrScale = b.dataset.v; $('#yrScale').querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', x === b)); renderYears(fdaDrugs()); });

function lineChart(sel, series, o) {
  const W = widthOf(sel), H = Math.round(Math.min(380, Math.max(240, W * 0.45))), M = { t: 22, r: 14, b: 28, l: 54 };
  const svg = svgBase(sel, W, H);
  const xs = series.flatMap(s => s.pts.map(p => p[0]));
  const x = d3.scaleLinear().domain(d3.extent(xs)).range([M.l, W - M.r]);
  const ymax = d3.max(series.flatMap(s => s.pts.map(p => p[1]))) || 1;
  const y = o.log ? d3.scaleSymlog().constant(1).domain([0, ymax]).range([H - M.b, M.t]) : d3.scaleLinear().domain([0, ymax]).nice().range([H - M.b, M.t]);
  const yt = o.log ? [0, 1, 10, 100, 1e3, 1e4, 1e5, 1e6].filter(v => v <= ymax * 1.05) : y.ticks(5);
  const yf = o.share ? (v => v + '%') : d3.format('~s');
  if (o.partialYear != null) { const px = x(o.partialYear - 0.5 < x.domain()[0] ? x.domain()[0] : o.partialYear - 0.5); svg.append('rect').attr('x', px).attr('y', M.t).attr('width', W - M.r - px).attr('height', H - M.t - M.b).style('fill', 'var(--surface)'); svg.append('text').attr('x', W - M.r).attr('y', M.t - 6).attr('text-anchor', 'end').text(o.partialNote + ' (incomplete)'); }
  svg.append('g').attr('class', 'grid').attr('transform', `translate(${M.l},0)`).call(d3.axisLeft(y).tickValues(yt).tickSize(-(W - M.l - M.r)).tickFormat(''));
  svg.append('g').attr('class', 'axis').attr('transform', `translate(0,${H - M.b})`).call(d3.axisBottom(x).ticks(Math.min(xs.length, W < 500 ? 5 : 10)).tickFormat(d3.format('d')));
  svg.append('g').attr('class', 'axis').attr('transform', `translate(${M.l},0)`).call(d3.axisLeft(y).tickValues(yt).tickFormat(yf));
  svg.append('text').attr('class', 'lbl').attr('x', M.l).attr('y', M.t - 8).text(o.ylabel + (o.log ? ' (log scale)' : ''));
  const line = d3.line().defined(p => p[1] != null).x(p => x(p[0])).y(p => y(p[1]));
  series.forEach(s => {
    svg.append('path').datum(s.pts).attr('d', line).style('fill', 'none').style('stroke', dcol(s.id)).style('stroke-width', 2.2);
    svg.append('g').selectAll('circle').data(s.pts.filter(p => p[1] != null)).join('circle').attr('cx', p => x(p[0])).attr('cy', p => y(p[1])).attr('r', 3.2).style('fill', dcol(s.id))
      .on('pointermove pointerdown', (ev, p) => showTip(o.tip(s.id, p[0], p[1]), ev)).on('pointerleave', hideTip);
  });
}

// ------------------------------------------------- reaction over time ---
function rxTerms() {
  const m = {};
  (S.fda?.drugs || []).filter(d => d.faers).forEach(d => (d.events?.top || []).forEach(r => m[r.term] = (m[r.term] || 0) + r.count));
  return Object.entries(m).sort((a, b) => b[1] - a[1]).map(e => e[0]);
}
function buildRxPick() {
  const terms = rxTerms(); const sel = $('#rxPick');
  if (!terms.length) { sel.innerHTML = '<option>No reactions available</option>'; sel.disabled = true; return; }
  if (!S.rxTerm || !terms.includes(S.rxTerm)) S.rxTerm = terms.includes('NAUSEA') ? 'NAUSEA' : terms[0];
  sel.innerHTML = terms.map(t => `<option value="${esc(t)}"${t === S.rxTerm ? ' selected' : ''}>${esc(cap(t))}</option>`).join('');
}
async function fetchRx() {
  const term = S.rxTerm; if (!term) return;
  $('#rxChart').innerHTML = `<div class="empty">Fetching “${esc(cap(term))}” from openFDA…</div>`;
  try { const r = await getJSON('/api/fda/reaction?term=' + encodeURIComponent(term)); if (S.rxTerm === term) { S.rx = r; renderRx(fdaDrugs()); } }
  catch (e) { if (S.rxTerm === term) { S.rx = null; $('#rxChart').innerHTML = `<div class="empty">openFDA did not answer for “${esc(cap(term))}”: ${esc(e.message)}. No numbers shown.</div>`; $('#rxTable').innerHTML = ''; } }
}
function renderRx(list) {
  if (!$('#rxPick').options.length || $('#rxPick').disabled) buildRxPick();
  if (!S.rx || S.rx.term !== S.rxTerm) { if (S.rxTerm && !S.rxLoading) { S.rxLoading = true; fetchRx().finally(() => S.rxLoading = false); } return; }
  const r = S.rx;
  const ds = list.filter(d => r.drugs[d.drug]);
  legend('#rxLegend', ds.map(d => ({ name: drugName(d.drug), col: dcol(d.drug) })));
  $('#rxNote').innerHTML = (r.errors.length ? `<strong>Some requests failed:</strong> ${r.errors.map(esc).join('; ')}. ` : '') + `Fetched ${when(r.fetched_at)}. ` + (S.rxMode === 'share' ? 'Share = reports listing this reaction ÷ all reports naming the drug received that year. It is a share of reports, not of patients, and still not a measure of risk.' : 'Reports that name the drug and list this reaction, by year received.');
  if (!ds.length) { $('#rxChart').innerHTML = '<div class="empty">No data for the selected drugs.</div>'; $('#rxTable').innerHTML = ''; return; }
  const years = d3.range(d3.min(ds, d => d.events?.by_year?.[0]?.year ?? 9999), d3.max(ds, d => d.events?.by_year?.slice(-1)[0]?.year ?? 0) + 1);
  const tot = (d, y) => (d.events?.by_year?.find(x => x.year === y) || {}).count || 0;
  const cnt = (d, y) => (r.drugs[d.drug].find(x => x.year === y) || {}).count || 0;
  const v = (d, y) => S.rxMode === 'share' ? (tot(d, y) ? +(100 * cnt(d, y) / tot(d, y)).toFixed(2) : null) : cnt(d, y);
  const last = ds.map(d => d.events?.last_receive_date).filter(Boolean).sort().pop(), ly = last ? +last.slice(0, 4) : null, partial = last && last.slice(4) !== '1231';
  lineChart('#rxChart', ds.map(d => ({ id: d.drug, pts: years.map(y => [y, v(d, y)]) })), {
    share: S.rxMode === 'share', ylabel: S.rxMode === 'share' ? `% of the drug's reports listing ${cap(r.term)}` : `reports listing ${cap(r.term)}`,
    partialYear: partial ? ly : null, partialNote: partial ? `${ly}: to ${ymd(last)}` : '',
    tip: (id, y, val) => `<b>${esc(drugName(id))}</b> · ${y}<br>${fmt(cnt(ds.find(d => d.drug === id), y))} reports listing ${esc(cap(r.term))}<br>of ${fmt(tot(ds.find(d => d.drug === id), y))} reports naming the drug${S.rxMode === 'share' ? ` (${val}%)` : ''}`,
  });
  $('#rxTable').innerHTML = table([{ h: 'Year', v: y => y + (partial && y === ly ? ` (to ${ymd(last)})` : '') }, ...ds.flatMap(d => [{ h: drugName(d.drug) + ' reports', r: 1, v: y => fmt(cnt(d, y)) }, { h: 'share', r: 1, v: y => tot(d, y) ? fmtPct(100 * cnt(d, y) / tot(d, y)) : '—' }])], years.slice().reverse());
}
$('#rxPick').addEventListener('change', e => { S.rxTerm = e.target.value; S.rx = null; renderRx(fdaDrugs()); });
$('#rxMode').addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; S.rxMode = b.dataset.v; $('#rxMode').querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', x === b)); renderRx(fdaDrugs()); });

// --------------------------------------------------------- labels ---
function renderLabels() {
  const list = S.fda.drugs.filter(d => !S.f.drugs.size || S.f.drugs.has(d.drug));
  const rows = [];
  list.forEach(d => {
    const L = d.labels;
    if (!L) rows.push({ d, none: 'Not searched yet.' });
    else if (L.ok === false && !L.brands?.length) rows.push({ d, none: `Search failed: ${L.error}` });
    else if (!L.brands?.length) rows.push({ d, none: `No label found on openFDA (searched ${when(L.fetched_at)}).` });
    else L.brands.forEach(b => rows.push({ d, b, L }));
  });
  $('#labelTable').innerHTML = `<table class="data" data-nocheck><thead><tr><th>Drug</th><th>Brand</th><th>Labeler</th><th>Label date</th><th>Boxed warning</th><th>Indication (start of section)</th><th>Label</th></tr></thead><tbody>` +
    rows.map(r => r.none ? `<tr><td class="nowrap"><span class="dot" style="background:${dcol(r.d.drug)}"></span> ${esc(r.d.name)}</td><td colspan="6" class="muted">${esc(r.none)}</td></tr>` :
      `<tr><td class="nowrap"><span class="dot" style="background:${dcol(r.d.drug)}"></span> ${esc(r.d.name)}</td><td><strong>${esc(r.b.brand)}</strong>${r.b.route ? `<br><span class="small muted">${esc(cap(r.b.route))}</span>` : ''}</td><td class="small">${esc(r.b.manufacturer)}</td><td class="nowrap">${esc(ymd(r.b.effective))}</td>
      <td class="small" style="min-width:12rem">${r.b.boxed_warning ? `<details><summary>Yes</summary>${esc(r.b.boxed_warning.replace(/^WARNING:?\s*/i, ''))}…</details>` : 'None in label'}</td>
      <td class="small" style="min-width:14rem">${r.b.indications ? `<details><summary>${esc(r.b.indications.replace(/^\s*(1\s*)?INDICATIONS (AND|&) USAGE\s*/i, '').slice(0, 90))}…</summary>${esc(r.b.indications.replace(/^\s*(1\s*)?INDICATIONS (AND|&) USAGE\s*/i, ''))}…</details>` : '—'}</td>
      <td class="nowrap">${r.b.set_id ? `<a href="https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid=${encodeURIComponent(r.b.set_id)}" target="_blank" rel="noopener">DailyMed ↗</a>` : '—'}</td></tr>`).join('') + '</tbody></table>';
  const at = S.fda.drugs.map(d => d.labels?.fetched_at).filter(Boolean).sort();
  $('#provLabels').innerHTML = `Source: openFDA drug label API, searched on <code>openfda.generic_name</code>; most recent label per brand (repackager labels share a brand). Fetched ${at.length ? when(at[0]) : '—'}. Text excerpts are cut at a few hundred characters; read the full label on DailyMed.`;
}

// -------------------------------------------------------- sources ---
function renderSources() {
  $('#ctSources').innerHTML = S.ctSources.length ? table([
    { h: 'Drug', v: s => s.name },
    { h: 'Search (query.intr)', html: 1, v: s => `<code>${esc(s.query)}</code>` },
    { h: 'Studies', r: 1, v: s => s.total == null ? '—' : `${fmt(s.received)} of ${fmt(s.total)}` },
    { h: 'Fetched', v: s => when(s.fetched_at) },
    { h: 'Status', html: 1, v: s => s.ok ? '<span class="badge st-active">OK</span>' : `<span class="badge st-stopped">Failed</span> <span class="small">${esc(s.error)}</span>` },
    { h: 'Request', html: 1, v: s => s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">JSON ↗</a>` : '—' },
  ], S.ctSources) : '<div class="empty">Not available.</div>';
  const f = S.fda?.drugs || [];
  const rows = [];
  f.forEach(d => {
    if (d.events) Object.entries(d.events.queries || {}).forEach(([k, u]) => rows.push({ d: d.name, what: { total: 'Total reports', serious: 'Serious reports', by_date: 'Reports by date', top: 'Top reactions' }[k] || k, at: d.events.fetched_at, ok: !(d.events.errors || []).some(e => e.startsWith({ total: 'Total', serious: 'Serious', by_date: 'Reports by', top: 'Most' }[k])), u }));
    if (d.labels) rows.push({ d: d.name, what: `Labels (${fmt(d.labels.n_labels ?? 0)})`, at: d.labels.fetched_at, ok: d.labels.ok !== false, u: d.labels.url, err: d.labels.error });
  });
  $('#fdaSources').innerHTML = rows.length ? table([
    { h: 'Drug', v: r => r.d }, { h: 'Data', v: r => r.what }, { h: 'Fetched', v: r => when(r.at) },
    { h: 'Status', html: 1, v: r => r.ok ? '<span class="badge st-active">OK</span>' : `<span class="badge st-stopped">Failed</span>` },
    { h: 'Request', html: 1, v: r => r.u ? `<a href="${esc(r.u)}" target="_blank" rel="noopener">JSON ↗</a>` : '—' },
  ], rows) : '<div class="empty">Not available.</div>';
}
async function loadLog() {
  try {
    const j = await getJSON('/api/log');
    $('#fetchLog').innerHTML = table([{ h: 'When', v: l => when(l.at) }, { h: 'Source', v: l => l.source }, { h: 'Request', html: 1, v: l => l.url ? `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.label)}</a>` : esc(l.label) }, { h: 'Result', html: 1, v: l => l.ok ? `OK${l.n != null ? ' · ' + fmt(l.n) + ' records' : ''}` : `<span class="st-stopped">Failed</span> ${esc(l.error)}` }], j.log);
  } catch (e) { $('#fetchLog').innerHTML = `<div class="empty">Log not available: ${esc(e.message)}</div>`; }
}

// ------------------------------------------------------- refreshing ---
let watchTimer = null;
function watchRefresh(on) {
  const st = $('#refreshState');
  if (st) st.innerHTML = on ? '<span class="pill"><span class="dot wait"></span> Fetching fresh data…</span>' : '';
  $('#refreshBtn').disabled = !!on;
  clearTimeout(watchTimer);
  if (on) watchTimer = setTimeout(async () => {
    try { const m = await getJSON('/api/meta'); if (m.refreshing) return watchRefresh(true); } catch { return watchRefresh(true); }
    await loadAll();
  }, 5000);
}
$('#refreshBtn').addEventListener('click', async () => {
  $('#refreshBtn').disabled = true;
  try { const r = await getJSON('/api/refresh', { method: 'POST' }); alertMsg(r.message); if (r.started) watchRefresh(true); else $('#refreshBtn').disabled = false; }
  catch (e) { alertMsg('Could not start a refresh: ' + e.message); $('#refreshBtn').disabled = false; }
});
function alertMsg(m) { const b = document.createElement('div'); b.className = 'banner info'; b.textContent = m; $('#sources .sec-head').after(b); setTimeout(() => b.remove(), 8000); }
// check for newer data every 15 minutes while the page is open
setInterval(async () => { try { const m = await getJSON('/api/meta'); const a = JSON.stringify(m.ct.map(s => s.fetched_at)), b = JSON.stringify(S.ctSources.map(s => s.fetched_at)); if (a !== b || m.refreshing) loadAll(); } catch { /* keep showing what we have, banners say when */ } }, 15 * 60e3);

// ------------------------------------------------------ re-draw ---
let lastW = 0;
const redraw = debounce(() => { const w = document.body.clientWidth; if (Math.abs(w - lastW) < 8) return; lastW = w; renderAll(); renderFdaAll(); }, 200);
window.addEventListener('resize', redraw);
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (hasD3()) renderMap(filtered()); });
window.addEventListener('hashchange', () => { readHash(); buildFilters(); S.page = 0; renderAll(); renderFdaAll(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') hideTip(); });

lastW = document.body.clientWidth;
loadAll().catch(e => { $('#banners').innerHTML = `<div class="banner bad"><strong>The dashboard failed to start:</strong> ${esc(e.message)}</div>`; });
})();
