// Open Data Atlas — static dashboard over files in data/
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = n => n == null || isNaN(n) ? "—" : Number(n).toLocaleString("en-US");
const short = n => { n = +n; if (n >= 1e9) return (n / 1e9).toFixed(1) + "B"; if (n >= 1e6) return (n / 1e6).toFixed(1) + "M"; if (n >= 1e4) return Math.round(n / 1e3) + "k"; return fmt(n); };
const day = s => s ? new Date(typeof s === "number" ? s * 1000 : s).toISOString().slice(0, 10) : "—";
const tidy = v => typeof v === "string" && /^\d{4}-\d\d-\d\dT/.test(v) ? v.replace(/T00:00:00(\.000)?$/, "").replace("T", " ").replace(/:00(\.000)?$/, "") : v;
const plain = h => { const d = document.createElement("div"); d.innerHTML = String(h || "").replace(/<br\s*\/?>/gi, "\n").replace(/<\/p>/gi, "\n"); return d.textContent.replace(/\n{3,}/g, "\n\n").trim(); };
const pct = (a, b) => b ? (100 * a / b).toFixed(1) + "%" : "—";
const tip = Object.assign(document.createElement("div"), { className: "tip" }); document.body.append(tip);
document.addEventListener("mousemove", e => { if (tip.style.display === "block") { tip.style.left = Math.min(e.clientX + 12, innerWidth - 270) + "px"; tip.style.top = e.clientY + 12 + "px"; } });
document.addEventListener("mouseover", e => { const t = e.target.closest("[data-tip]"); if (t) { tip.textContent = t.dataset.tip; tip.style.display = "block"; } else tip.style.display = "none"; });

let CAT = null, PROF = null, ACCESS = null;
async function load(path) { try { const r = await fetch(path, { cache: "no-cache" }); if (!r.ok) throw 0; return await r.json(); } catch { return null; } }

// ---------- charts (plain SVG) ----------
const CW = () => Math.max(300, Math.min(560, (document.querySelector("#view")?.clientWidth || 600) - 34));
function hbar(rows, { highlight, width = CW(), max = 12, unit = "" } = {}) {
  rows = rows.slice(0, max); if (!rows.length) return `<p class="muted small">No values.</p>`;
  const lw = Math.min(width < 420 ? 130 : 200, Math.max(...rows.map(r => String(r[0]).length)) * 6.4 + 8), bh = 22, gap = 6, vw = 70;
  const W = width, plot = W - lw - vw, H = rows.length * (bh + gap);
  const m = Math.max(...rows.map(r => +r[1])) || 1;
  const bars = rows.map((r, i) => {
    const y = i * (bh + gap), w = Math.max(1, plot * (+r[1]) / m), hi = highlight && String(r[0]).toUpperCase() === highlight.toUpperCase();
    const label = String(r[0] || "(blank)"); const cap = width < 420 ? 19 : 30; const lab = label.length > cap ? label.slice(0, cap - 1) + "…" : label;
    return `<g data-tip="${esc(label)}: ${fmt(r[1])}${unit}"><text class="lbl" x="${lw - 6}" y="${y + bh / 2 + 4}" text-anchor="end">${esc(lab)}</text>
      <rect x="${lw}" y="${y}" width="${w}" height="${bh}" rx="3" fill="var(${hi ? "--mn" : "--bar"})"/>
      <text x="${lw + w + 5}" y="${y + bh / 2 + 4}">${short(r[1])}${unit}</text></g>`;
  }).join("");
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img">${bars}</svg></div>`;
}
function line(series, { width = CW(), height = 220, labels = ["Citywide", "Manhattan"], yLabel = "records per month" } = {}) {
  // series: [[period, total, manhattan?]]
  if (!series || series.length < 2) return `<p class="muted small">Not enough dated records for a trend.</p>`;
  const L = 52, R = 10, T = 10, B = 28, W = width, H = height, pw = W - L - R, ph = H - T - B;
  const m = Math.max(...series.map(s => +s[1])) || 1, n = series.length;
  const x = i => L + pw * i / (n - 1), y = v => T + ph - ph * v / m;
  const path = k => series.map((s, i) => s[k] == null ? "" : `${i ? "L" : "M"}${x(i).toFixed(1)},${y(+s[k]).toFixed(1)}`).join("");
  const ticks = [0, .25, .5, .75, 1].map(f => `<line x1="${L}" x2="${W - R}" y1="${y(m * f)}" y2="${y(m * f)}" stroke="var(--line)"/><text x="${L - 6}" y="${y(m * f) + 4}" text-anchor="end">${short(m * f)}</text>`).join("");
  const step = Math.ceil(n / (width < 420 ? 4 : 6));
  const xt = series.map((s, i) => i % step === 0 || (i === n - 1 && (n - 1) % step > step / 2) ? `<text x="${x(i)}" y="${H - 8}" text-anchor="middle">${esc(s[0])}</text>` : "").join("");
  const hasMn = series.some(s => s[2] != null);
  const hits = series.map((s, i) => `<rect x="${x(i) - pw / n / 2}" y="${T}" width="${pw / n}" height="${ph}" fill="transparent" data-tip="${esc(s[0])} — ${labels[0]}: ${fmt(s[1])}${hasMn ? ` · ${labels[1]}: ${fmt(s[2])}` : ""}"/>`).join("");
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img">${ticks}${xt}
    <path d="${path(1)}" fill="none" stroke="var(--bar)" stroke-width="2"/>
    ${hasMn ? `<path d="${path(2)}" fill="none" stroke="var(--mn)" stroke-width="2"/>` : ""}${hits}</svg></div>
    <p class="legend"><span class="sw" style="background:var(--bar)"></span>${labels[0]} ${hasMn ? `&nbsp; <span class="sw" style="background:var(--mn)"></span>${labels[1]}` : ""} · ${esc(yLabel)}</p>`;
}

// ---------- table with search + sort ----------
function table(el, cols, rows, { search = true, pageSize = 50 } = {}) {
  let q = "", sortK = null, dir = 1, shown = pageSize;
  el.innerHTML = `${search ? `<div class="filters"><input type="search" placeholder="Search these rows…"></div>` : ""}<div class="tablewrap"><table><thead></thead><tbody></tbody></table></div><p class="small muted cnt"></p>`;
  const draw = () => {
    let r = rows.filter(x => !q || cols.some(c => String(x[c.k] ?? "").toLowerCase().includes(q)));
    if (sortK) { const c = cols.find(c => c.k === sortK); r = [...r].sort((a, b) => c.num ? dir * ((+a[sortK] || 0) - (+b[sortK] || 0)) : dir * String(a[sortK] ?? "").localeCompare(String(b[sortK] ?? ""))); }
    $("thead", el).innerHTML = `<tr>${cols.map(c => `<th class="${c.num ? "n" : ""}" data-k="${c.k}">${esc(c.label)}${sortK === c.k ? (dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("")}</tr>`;
    $("tbody", el).innerHTML = r.length ? r.slice(0, shown).map(x => `<tr>${cols.map(c => `<td class="${c.num ? "n" : ""}">${c.html ? c.html(x) : esc(c.num ? fmt(x[c.k]) : tidy(x[c.k]))}</td>`).join("")}</tr>`).join("") : `<tr><td colspan="${cols.length}" class="muted">No rows match.</td></tr>`;
    $(".cnt", el).innerHTML = `Showing ${fmt(Math.min(shown, r.length))} of ${fmt(r.length)} rows${r.length > shown ? ` · <button class="more">Show more</button>` : ""}`;
  };
  el.addEventListener("click", e => {
    const th = e.target.closest("th"); if (th) { sortK === th.dataset.k ? dir = -dir : (sortK = th.dataset.k, dir = cols.find(c => c.k === sortK).num ? -1 : 1); draw(); }
    if (e.target.matches(".more")) { shown += pageSize * 4; draw(); }
  });
  if (search) $("input", el).addEventListener("input", e => { q = e.target.value.trim().toLowerCase(); shown = pageSize; draw(); });
  draw();
}

// ---------- views ----------
function setTab(t) { document.querySelectorAll(".tabs a").forEach(a => a.classList.toggle("on", a.dataset.tab === t)); }

function viewHome() {
  setTab("home");
  const P = PROF?.datasets || [], ds = CAT.datasets;
  const byType = {}; ds.forEach(d => byType[d.type] = (byType[d.type] || 0) + 1);
  const byCat = {}; ds.filter(d => d.type === "dataset").forEach(d => byCat[d.category || "(none)"] = (byCat[d.category || "(none)"] || 0) + 1);
  const byAg = {}; ds.filter(d => d.type === "dataset").forEach(d => byAg[d.agency || "(none)"] = (byAg[d.agency || "(none)"] || 0) + 1);
  const yr = new Date(CAT.fetched).getUTCFullYear();
  const fresh = ds.filter(d => d.type === "dataset" && d.updated && new Date(d.updated).getUTCFullYear() === yr).length;
  const profRows = P.reduce((a, p) => a + (p.rows || 0), 0);
  $("#view").innerHTML = `
    <div class="kpis">
      <div class="kpi"><div class="v">${fmt(ds.length)}</div><div class="l">assets in the NYC Open Data catalog</div></div>
      <div class="kpi"><div class="v">${fmt(byType.dataset)}</div><div class="l">of them are tabular datasets</div></div>
      <div class="kpi"><div class="v">${fmt(fresh)}</div><div class="l">datasets updated in ${yr}</div></div>
      <div class="kpi"><div class="v">${P.length ? short(profRows) : "—"}</div><div class="l">rows across the ${P.length || "—"} datasets profiled here</div></div>
    </div>
    ${P.length ? `<h2>Profiled datasets</h2>${facts(P)}<p class="muted small">Each card opens a page with the dataset's description, columns, a Manhattan-vs-city breakdown, trends and sample records. Orange marks Manhattan.</p>
    <div class="cards">${P.map(dcard).join("")}</div>` : ""}
    <div class="grid two" style="margin-top:1.5rem">
      <div class="card"><h3>Catalog datasets by category</h3>${hbar(Object.entries(byCat).sort((a, b) => b[1] - a[1]), { max: 14 })}</div>
      <div class="card"><h3>Top publishing agencies</h3>${hbar(Object.entries(byAg).sort((a, b) => b[1] - a[1]), { max: 14 })}</div>
    </div>`;
}
function facts(P) {
  const g = id => P.find(p => p.id === id), out = [];
  const t = g("hn5i-inap"), b = t?.byBorough?.filter(r => r[0]);
  if (b?.length) { const mn = b.find(r => r[0] === "Manhattan"), lo = [...b].sort((a, c) => a[1] - c[1]); const next = lo[1];
    if (lo[0][0] === "Manhattan") out.push({ v: fmt(mn[1]), t: `living street trees in Manhattan — the fewest of any borough, below ${next[0]} (${fmt(next[1])})`, id: t.id, src: t.name }); }
  const m = g("5wq4-mkjj"); const mb = m?.byBorough;
  if (mb?.length) { const mn = mb.find(r => r[0] === "Manhattan"), tot = mb.reduce((a, r) => a + r[1], 0), mo = new Date(m.monthly?.at(-1)?.[0] + "-15").toLocaleString("en-US", { month: "long", year: "numeric" });
    out.push({ v: pct(mn[1], tot), t: `of all subway entries in ${mo} were at Manhattan stations, more than the other four boroughs combined`, id: m.id, src: m.name }); }
  const s = g("usep-8jbt"), z = s?.extra?.find(e => e.l.includes("$0"));
  if (z && s.manhattan?.count) { const n = +z.v.replace(/,/g, "");
    out.push({ v: pct(n, s.manhattan.count), t: `of Manhattan property sales recorded in the last year (${fmt(n)} of ${fmt(s.manhattan.count)}) had a sale price of $0`, id: s.id, src: s.name }); }
  return out.length ? `<div class="facts">${out.map(f => `<a class="fact" href="#/d/${f.id}"><div class="v">${esc(f.v)}</div><p>${esc(f.t)}</p><div class="src">Source: ${esc(f.src)}</div></a>`).join("")}</div>` : "";
}
function dcard(p) {
  const mn = p.manhattan;
  return `<a class="dcard" href="#/d/${p.id}"><span class="pill">${esc(p.topic || p.category || "")}</span>${p.error ? `<span class="pill bad">fetch failed</span>` : ""}
    <div class="t">${esc(p.name)}</div>
    <div class="meta"><span class="big">${fmt(p.rows)}</span> rows${mn?.count != null ? ` · <span style="color:var(--mn)">${fmt(mn.count)} in Manhattan</span>` : ""}</div>
    <div class="meta">${esc(p.agency || "")} · updated ${day(p.updated)}</div></a>`;
}

function viewDatasets() {
  setTab("datasets");
  const P = PROF?.datasets || [];
  $("#view").innerHTML = `<h2 style="margin-top:.5rem">Profiled datasets (${P.length})</h2>
   <p class="muted">These were queried live through each publisher's API on ${day(PROF?.fetched)}. Figures are aggregates computed by the API over the full dataset, not a sample, unless a page says otherwise.</p>
   <div class="filters"><input type="search" id="dq" placeholder="Filter by name, agency or topic…"></div><div class="cards" id="dc"></div>`;
  const draw = q => { const r = P.filter(p => !q || [p.name, p.agency, p.topic, p.desc].join(" ").toLowerCase().includes(q)); $("#dc").innerHTML = r.length ? r.map(dcard).join("") : `<p class="muted">No datasets match.</p>`; };
  $("#dq").addEventListener("input", e => draw(e.target.value.trim().toLowerCase())); draw("");
}

function partial(p) { const last = p.monthly?.at(-1)?.[0]; if (!last || !p.updated) return false; const u = day(p.updated); return u.slice(0, last.length) === last && !(last.length === 7 && +u.slice(8) >= 28); }
function viewDataset(id) {
  setTab("datasets");
  const p = (PROF?.datasets || []).find(d => d.id === id);
  if (!p) { const c = CAT.datasets.find(d => d.id === id); return viewCatalogItem(c, id); }
  const mn = p.manhattan;
  $("#view").innerHTML = `<a class="back" href="#/datasets">← All profiled datasets</a>
   <h2 style="margin-top:.25rem">${esc(p.name)}</h2>
   <p class="muted small">${esc(p.agency || "")} · ${esc(p.publisher || "NYC Open Data")} · id <code>${esc(p.id)}</code></p>
   ${p.error ? `<div class="note">Part of this profile could not be fetched: ${esc(p.error)}</div>` : ""}
   <div class="kpis">
     <div class="kpi"><div class="v">${fmt(p.rows)}</div><div class="l">${p.id === "citibike-gbfs" ? "stations in the live feed" : "rows in the full dataset"}</div></div>
     <div class="kpi"><div class="v" style="color:var(--mn)">${mn?.count != null ? fmt(mn.count) : "—"}</div><div class="l">${mn?.count != null ? `${mn.label ? `in Manhattan (${esc(mn.label)})` : "in Manhattan"} · ${pct(mn.count, p.rows)} of rows` : `Manhattan rows not counted: ${esc(mn?.why || "no borough field")}`}</div></div>
     <div class="kpi"><div class="v">${p.columns?.length ?? "—"}</div><div class="l">columns</div></div>
     <div class="kpi"><div class="v">${day(p.updated)}</div><div class="l">last data update${p.freq ? ` · ${esc(p.freq)}` : ""}</div></div>
   </div>
   ${(p.extra || []).length ? `<div class="kpis">${p.extra.map(k => `<div class="kpi"><div class="v">${esc(k.v)}</div><div class="l">${esc(k.l)}</div></div>`).join("")}</div>` : ""}
   ${p.caveat ? `<div class="note">${esc(p.caveat)}</div>` : ""}
   <div class="grid two">
     ${p.monthly ? `<div class="card"><h3>${esc(p.monthlyTitle || "Records per month")}</h3>${line(p.monthly, { labels: p.monthlyLabels, yLabel: p.monthlyUnit || "records per month" })}${partial(p) ? `<p class="legend">The last ${p.monthlyTitle?.includes("year") ? "year" : "month"} shown is incomplete: the data was last updated ${day(p.updated)}.</p>` : ""}</div>` : ""}
     ${p.byBorough ? `<div class="card"><h3>${esc(p.byBoroughTitle || "Records by borough")}${p.byBoroughNote ? ` <span class="muted small">· ${esc(p.byBoroughNote)}</span>` : ""}</h3>${hbar(p.byBorough, { highlight: mn?.value })}</div>` : ""}
     ${(p.top || []).map(t => `<div class="card"><h3>${esc(t.title)}</h3>${hbar(t.rows, { max: t.sorted ? 30 : 12, unit: t.unit || "", highlight: t.rows.some(r => r[0] === "Manhattan") ? "Manhattan" : null })}${t.note ? `<p class="legend">${esc(t.note)}</p>` : ""}</div>`).join("")}
   </div>
   <h2>About this dataset</h2>
   <div class="card"><p class="desc">${esc(plain(p.desc) || "No description published.")}</p>
     <dl class="dl"><dt>Publisher</dt><dd>${esc(p.agency || "—")}</dd><dt>Category</dt><dd>${esc(p.category || "—")}</dd>
     <dt>Licence</dt><dd>${esc(p.license || "Not stated")}</dd><dt>Dataset page</dt><dd><a href="${esc(p.page)}" target="_blank" rel="noopener">${esc(p.page)}</a></dd>
     <dt>API</dt><dd><code>${esc(p.api)}</code></dd>${p.queries ? `<dt>Queries</dt><dd class="small"><details><summary>${p.queries.length} API queries produced the figures on this page</summary>${p.queries.map(q => `<code>${esc(q)}</code>`).join("<br>")}</details></dd>` : ""}</dl></div>
   ${p.sample?.length ? `<h2>Sample records${mn?.count != null ? " (Manhattan, most recent)" : ""}</h2><p class="muted small">${p.sampleNote ? `${p.sample.length} rows: ${esc(p.sampleNote)}.` : `${p.sample.length} rows pulled for illustration — not the full dataset.`}</p><div id="samp"></div>` : ""}
   ${p.columns?.length ? `<h2>Columns (${p.columns.length})</h2><div id="cols"></div>` : ""}`;
  if (p.sample?.length) { const ks = p.sampleCols || Object.keys(p.sample[0]); table($("#samp"), ks.map(k => ({ k, label: k })), p.sample, { pageSize: 15 }); }
  if (p.columns?.length) table($("#cols"), [{ k: "name", label: "Column" }, { k: "field", label: "API field" }, { k: "type", label: "Type" }, { k: "desc", label: "Description" }], p.columns, { pageSize: 25 });
  scrollTo(0, 0);
}
function viewCatalogItem(c, id) {
  if (!c) { $("#view").innerHTML = `<a class="back" href="#/catalog">← Catalog</a><p>Dataset <code>${esc(id)}</code> not found.</p>`; return; }
  $("#view").innerHTML = `<a class="back" href="#/catalog">← Full catalog</a><h2 style="margin-top:.25rem">${esc(c.name)}</h2>
   <p class="muted small">${esc(c.agency || "")} · ${esc(c.type)} · id <code>${esc(c.id)}</code></p>
   <div class="note">Not profiled in this dashboard — this is the catalog entry only. The live data is at the links below.</div>
   <div class="card"><p class="desc">${esc(plain(c.desc) || "No description.")}${c.desc?.length >= 600 ? "…" : ""}</p>
   <dl class="dl"><dt>Category</dt><dd>${esc(c.category || "—")}</dd><dt>Tags</dt><dd>${esc((c.tags || []).join(", ") || "—")}</dd><dt>Columns</dt><dd>${c.cols || "—"}</dd>
   <dt>Updated</dt><dd>${day(c.updated)}</dd><dt>Page views</dt><dd>${fmt(c.views)}</dd><dt>Downloads</dt><dd>${fmt(c.downloads)}</dd>
   <dt>Page</dt><dd><a href="https://data.cityofnewyork.us/d/${esc(c.id)}" target="_blank" rel="noopener">data.cityofnewyork.us/d/${esc(c.id)}</a></dd>
   ${c.type === "dataset" ? `<dt>API</dt><dd><code>https://data.cityofnewyork.us/resource/${esc(c.id)}.json</code></dd>` : ""}</dl></div>`;
  scrollTo(0, 0);
}

function viewCatalog() {
  setTab("catalog");
  const ds = CAT.datasets, cats = [...new Set(ds.map(d => d.category || "(none)"))].sort(), types = [...new Set(ds.map(d => d.type))].sort();
  const ags = Object.entries(ds.reduce((a, d) => (a[d.agency || "(none)"] = (a[d.agency || "(none)"] || 0) + 1, a), {})).sort((a, b) => b[1] - a[1]).map(a => a[0]);
  const prof = new Set((PROF?.datasets || []).map(p => p.id));
  $("#view").innerHTML = `<h2 style="margin-top:.5rem">Full NYC Open Data catalog</h2>
   <p class="muted">Every public asset listed on data.cityofnewyork.us, fetched ${day(CAT.fetched)} from the Socrata catalog API. Open a row for its description and links.</p>
   <div class="filters"><select id="ft"><option value="">All types</option>${types.map(t => `<option>${esc(t)}</option>`).join("")}</select>
   <select id="fc"><option value="">All categories</option>${cats.map(t => `<option>${esc(t)}</option>`).join("")}</select>
   <select id="fa"><option value="">All agencies</option>${ags.map(t => `<option>${esc(t)}</option>`).join("")}</select>
   <label class="small"><input type="checkbox" id="fm"> mentions Manhattan</label><button id="fr">Reset</button></div><div id="ct"></div>`;
  const cols = [{ k: "name", label: "Name", html: x => `<a href="#/d/${x.id}">${esc(x.name)}</a>${prof.has(x.id) ? ` <span class="pill ok">profiled</span>` : ""}` },
    { k: "agency", label: "Agency" }, { k: "category", label: "Category" }, { k: "type", label: "Type" },
    { k: "upd", label: "Updated" }, { k: "views", label: "Page views", num: true }];
  const draw = () => {
    const t = $("#ft").value, c = $("#fc").value, a = $("#fa").value, m = $("#fm").checked;
    const r = ds.filter(d => (!t || d.type === t) && (!c || (d.category || "(none)") === c) && (!a || (d.agency || "(none)") === a) && (!m || /manhattan/i.test(d.name + d.desc + (d.tags || []).join(" ")))).map(d => ({ ...d, upd: d.updated ? d.updated.slice(0, 10) : "" }));
    table($("#ct"), cols, r, { pageSize: 50 });
  };
  ["#ft", "#fc", "#fa", "#fm"].forEach(s => $(s).addEventListener("change", draw));
  $("#fr").onclick = () => { $("#ft").value = $("#fc").value = $("#fa").value = ""; $("#fm").checked = false; draw(); };
  draw();
}

function viewAccess() {
  setTab("access");
  const A = ACCESS?.checks || [];
  const cls = s => s === "ok" ? "ok" : s === "partial" ? "warn" : "bad";
  $("#view").innerHTML = `<h2 style="margin-top:.5rem">What could and could not be fetched</h2>
   <p class="muted">Every source was tested from this machine on ${day(ACCESS?.tested)}. “Reachable” means the request returned data; it does not mean every column is complete.</p>
   ${A.length ? `<div class="tablewrap"><table><thead><tr><th>Source</th><th>Status</th><th>What happened</th></tr></thead><tbody>${A.map(a => `<tr><td>${esc(a.name)}<br><code class="small">${esc(a.url)}</code></td><td><span class="pill ${cls(a.status)}">${esc(a.label)}</span></td><td>${esc(a.note)}</td></tr>`).join("")}</tbody></table></div>` : `<p class="muted">Access log not loaded yet.</p>`}
   ${(ACCESS?.unreachable || []).length ? `<h2>Data this dashboard does not include</h2><div class="card"><ul>${ACCESS.unreachable.map(u => `<li><b>${esc(u.what)}</b> — ${esc(u.why)}</li>`).join("")}</ul></div>` : ""}`;
}

async function route() {
  const h = location.hash.replace(/^#\/?/, "");
  if (h.startsWith("d/")) return viewDataset(h.slice(2));
  if (h === "datasets") return viewDatasets();
  if (h === "catalog") return viewCatalog();
  if (h === "access") return viewAccess();
  viewHome();
}
(async () => {
  [CAT, PROF, ACCESS] = await Promise.all([load("data/catalog.json"), load("data/profiles.json"), load("data/access.json")]);
  if (!CAT) { $("#view").innerHTML = `<p>The catalog file could not be loaded.</p>`; return; }
  $("#lede").innerHTML = `A navigable guide to New York City's public data: the full catalog of ${fmt(CAT.total)} assets on NYC Open Data${PROF ? `, plus ${PROF.datasets.length} key datasets profiled in depth with a Manhattan focus` : ""}. Data fetched ${day(CAT.fetched)}.`;
  $("#foot").innerHTML = `Sources: NYC Open Data (data.cityofnewyork.us, Socrata catalog and SODA APIs)${PROF ? ", data.ny.gov (MTA), Citi Bike GBFS feed" : ""}. All figures are computed from files saved in this project's <code>data/</code> folder.`;
  addEventListener("hashchange", route); route();
})();
