// Charts read their numbers from the data tables on the page (one set of numbers).
(function () {
  const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const fmt$ = v => (v < 0 ? '−$' : '$') + Math.abs(Math.round(v)).toLocaleString('en-US');
  const fmtK = v => (v < 0 ? '−$' : '$') + (Math.abs(v) / 1000).toFixed(Math.abs(v) >= 10000 ? 0 : 1) + 'k';
  const num = td => parseFloat(td.dataset.v);
  const charts = [];
  const narrow = () => window.innerWidth < 560;

  function base() {
    return {
      textStyle: { fontFamily: 'system-ui, -apple-system, Segoe UI, Roboto, sans-serif', color: css('--ink') },
      grid: { left: 8, right: 16, top: 44, bottom: 8, containLabel: true },
      legend: { top: 0, textStyle: { color: css('--ink'), fontSize: 12 }, itemWidth: 12, itemHeight: 10 },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, backgroundColor: css('--surface'), borderColor: css('--line'), textStyle: { color: css('--ink') } },
    };
  }
  const axisStyle = () => ({
    axisLine: { lineStyle: { color: css('--line') } },
    axisLabel: { color: css('--muted') },
    splitLine: { lineStyle: { color: css('--line') } },
    nameTextStyle: { color: css('--muted') },
  });
  const compColors = () => [css('--c1'), css('--c2'), css('--c3'), css('--c4'), css('--c5'), css('--c6')];

  // 1. AAA five-year stacked bars
  function drawAAA() {
    const rows = [...document.querySelectorAll('#t-aaa tbody tr')];
    const short = { 'Medium sedan': 'Sedan', 'Compact SUV': 'Compact SUV', 'Medium SUV': 'Mid SUV', 'Pickup': 'Pickup' };
    const labels = rows.map(r => short[r.cells[0].textContent] + ' · ' + r.cells[1].textContent.replace('Electric', 'EV'));
    const comps = ['Fuel / electricity', 'Maintenance', 'Depreciation', 'Insurance', 'Licence, reg. & taxes', 'Finance'];
    const series = comps.map((name, i) => ({
      name, type: 'bar', stack: 'a', barMaxWidth: 26,
      data: rows.map(r => num(r.cells[2 + i]) * 5),
      emphasis: { focus: 'series' },
    }));
    const totals = rows.map(r => [2, 3, 4, 5, 6, 7].reduce((s, i) => s + num(r.cells[i]) * 5, 0));
    series.push({ name: 'Total', type: 'bar', stack: 'a', data: totals.map(() => 0), label: { show: true, position: 'right', color: css('--ink'), fontSize: 11, formatter: p => fmtK(totals[p.dataIndex]) }, tooltip: { show: false }, legend: { show: false } });
    const c = echarts.init(document.getElementById('ch-aaa'));
    const o = base();
    Object.assign(o, {
      color: compColors(),
      legend: Object.assign(o.legend, { data: comps }),
      grid: { left: 8, right: 52, top: narrow() ? 64 : 48, bottom: 28, containLabel: true },
      tooltip: Object.assign(o.tooltip, { valueFormatter: v => fmt$(v) }),
      xAxis: Object.assign({ type: 'value', splitNumber: narrow() ? 3 : 5, name: 'US$ over 5 years', nameLocation: 'middle', nameGap: 24, axisLabel: { formatter: v => '$' + v / 1000 + 'k', color: css('--muted') } }, { splitLine: { lineStyle: { color: css('--line') } } }),
      yAxis: Object.assign({ type: 'category', inverse: true, data: labels }, axisStyle(), { axisLabel: { color: css('--ink'), fontSize: 11 } }),
      series,
    });
    c.setOption(o); charts.push([c, drawAAA]);
  }

  // 2. Worked example stacked bars
  function drawExample() {
    const rows = [...document.querySelectorAll('#t-ex tbody tr')].filter(r => r.cells[1].dataset.v !== undefined);
    const c = echarts.init(document.getElementById('ch-ex'));
    const o = base();
    const cats = ['Equinox EV', 'Equinox petrol'];
    const series = rows.map(r => ({ name: r.cells[0].textContent, type: 'bar', stack: 'x', barMaxWidth: 70, data: [num(r.cells[1]), num(r.cells[2])] }));
    const tot = [0, 1].map(i => rows.reduce((s, r) => s + num(r.cells[1 + i]), 0));
    series.push({ name: 'Total', type: 'bar', stack: 'x', data: [0, 0], label: { show: true, position: 'top', color: css('--ink'), fontWeight: 600, formatter: p => fmt$(tot[p.dataIndex]) }, tooltip: { show: false } });
    Object.assign(o, {
      color: compColors(),
      legend: Object.assign(o.legend, { data: rows.map(r => r.cells[0].textContent) }),
      grid: { left: 8, right: 12, top: narrow() ? 64 : 48, bottom: 8, containLabel: true },
      tooltip: Object.assign(o.tooltip, { valueFormatter: v => fmt$(v) }),
      xAxis: Object.assign({ type: 'category', data: cats }, axisStyle(), { axisLabel: { color: css('--ink') } }),
      yAxis: Object.assign({ type: 'value', axisLabel: { formatter: v => '$' + v / 1000 + 'k', color: css('--muted') } }, { splitLine: { lineStyle: { color: css('--line') } }, nameTextStyle: { color: css('--muted') } }),
      series,
    });
    c.setOption(o); charts.push([c, drawExample]);
  }

  // 3. Miles sensitivity lines
  function drawMiles() {
    const rows = [...document.querySelectorAll('#t-miles tbody tr')];
    const heads = [...document.querySelectorAll('#t-miles thead th')].slice(1).map(h => h.textContent);
    const x = rows.map(r => r.cells[0].textContent);
    const c = echarts.init(document.getElementById('ch-miles'));
    const o = base();
    const cols = [css('--ink'), css('--c4'), css('--c3')];
    Object.assign(o, {
      color: cols,
      grid: { left: 8, right: 16, top: narrow() ? 64 : 48, bottom: 30, containLabel: true },
      tooltip: { trigger: 'axis', backgroundColor: css('--surface'), borderColor: css('--line'), textStyle: { color: css('--ink') }, valueFormatter: v => fmt$(v) },
      xAxis: Object.assign({ type: 'category', data: x, name: 'miles per year', nameLocation: 'middle', nameGap: 26, boundaryGap: false }, axisStyle()),
      yAxis: Object.assign({ type: 'value', axisLabel: { formatter: v => fmtK(v), color: css('--muted') } }, { splitLine: { lineStyle: { color: css('--line') } }, nameTextStyle: { color: css('--muted') } }),
      series: heads.map((h, i) => ({
        name: h, type: 'line', symbolSize: 6, lineStyle: { width: i === 0 ? 3 : 2, type: i === 0 ? 'solid' : 'dashed' },
        data: rows.map(r => num(r.cells[1 + i])),
        markLine: i === 0 ? { silent: true, symbol: 'none', data: [{ yAxis: 0 }], lineStyle: { color: css('--muted'), type: 'solid' }, label: { formatter: 'break-even', color: css('--muted'), position: 'insideEndTop' } } : undefined,
      })),
    });
    c.setOption(o); charts.push([c, drawMiles]);
  }

  // 4. Depreciation bars
  function drawDep() {
    const rows = [...document.querySelectorAll('#t-dep tbody tr')];
    const c = echarts.init(document.getElementById('ch-dep'));
    const o = base();
    const colorFor = t => t === 'EV' ? css('--ev') : t === 'All' ? css('--c5') : t === 'Hybrid' ? css('--hyb') : css('--gas');
    Object.assign(o, {
      legend: { show: false },
      grid: { left: 8, right: 44, top: 10, bottom: 28, containLabel: true },
      tooltip: Object.assign(o.tooltip, { valueFormatter: v => v + '%' }),
      xAxis: Object.assign({ type: 'value', max: 70, splitNumber: narrow() ? 4 : 7, name: '% of original price lost in 5 years', nameLocation: 'middle', nameGap: 24, axisLabel: { formatter: '{value}%', color: css('--muted') } }, { splitLine: { lineStyle: { color: css('--line') } }, nameTextStyle: { color: css('--muted') } }),
      yAxis: Object.assign({ type: 'category', inverse: true, data: rows.map(r => r.cells[0].textContent) }, axisStyle(), { axisLabel: { color: css('--ink') } }),
      series: [{ type: 'bar', name: 'Value lost', barMaxWidth: 26,
        data: rows.map(r => ({ value: num(r.cells[2]), itemStyle: { color: colorFor(r.cells[1].textContent) } })),
        label: { show: true, position: 'right', formatter: '{c}%', color: css('--ink') } }],
    });
    c.setOption(o); charts.push([c, drawDep]);
  }

  // 5. Energy cost per mile
  function drawEnergy() {
    const rows = [...document.querySelectorAll('#t-energy tbody tr')];
    const c = echarts.init(document.getElementById('ch-energy'));
    const o = base();
    Object.assign(o, {
      legend: { show: false },
      grid: { left: 8, right: 44, top: 10, bottom: 28, containLabel: true },
      tooltip: Object.assign(o.tooltip, { valueFormatter: v => v + '¢ per mile' }),
      xAxis: Object.assign({ type: 'value', splitNumber: narrow() ? 3 : 6, name: 'US cents per mile', nameLocation: 'middle', nameGap: 24, axisLabel: { formatter: '{value}¢', color: css('--muted') } }, { splitLine: { lineStyle: { color: css('--line') } }, nameTextStyle: { color: css('--muted') } }),
      yAxis: Object.assign({ type: 'category', inverse: true, data: rows.map(r => r.cells[0].textContent) }, axisStyle(), { axisLabel: { color: css('--ink') } }),
      series: [{ type: 'bar', barMaxWidth: 28,
        data: rows.map(r => ({ value: num(r.cells[1]), itemStyle: { color: r.cells[0].textContent.startsWith('Petrol') ? css('--gas') : css('--ev') } })),
        label: { show: true, position: 'right', formatter: '{c}¢', color: css('--ink') } }],
    });
    c.setOption(o); charts.push([c, drawEnergy]);
  }

  // ---- Calculator ----
  const presets = {
    equinox: { ev_price: 37195, gas_price_car: 31695, ev_dep_pct: 57.2, gas_dep_pct: 41.8, ev_kwh: 31, gas_mpg: 27 },
    ioniq:   { ev_price: 36900, gas_price_car: 32000, ev_dep_pct: 57.2, gas_dep_pct: 41.8, ev_kwh: 30, gas_mpg: 28 },
    modely:  { ev_price: 41630, gas_price_car: 33895, ev_dep_pct: 57.8, gas_dep_pct: 25.2, ev_kwh: 25, gas_mpg: 43 },
  };
  const shared = { miles: 15000, home_price: 0.182, dcfc_price: 0.54, public_share: 20, gas_price: 3.89, ev_maint: 0.061, gas_maint: 0.101, ev_ins: 2277, gas_ins: 2188, ev_fee: 200, incentive: 0 };
  const ids = ['ev_price', 'gas_price_car', 'ev_dep_pct', 'gas_dep_pct', 'ev_kwh', 'gas_mpg', 'miles', 'home_price', 'dcfc_price', 'public_share', 'gas_price', 'ev_maint', 'gas_maint', 'ev_ins', 'gas_ins', 'ev_fee', 'incentive'];
  const el = id => document.getElementById(id);
  let calcChart;

  function loadPreset(name) {
    const p = Object.assign({}, shared, presets[name]);
    ids.forEach(id => { el(id).value = p[id]; });
    compute();
  }
  function compute() {
    const v = {}; const bad = [];
    ids.forEach(id => {
      const x = parseFloat(el(id).value);
      if (!isFinite(x) || x < 0) { bad.push(el(id).parentElement.firstChild.textContent.trim()); v[id] = 0; } else v[id] = x;
    });
    const warn = [];
    if (bad.length) warn.push('Treated as 0 (empty or negative): ' + bad.join(', ') + '.');
    if (v.ev_dep_pct > 100 || v.gas_dep_pct > 100) warn.push('Value lost cannot exceed 100%.');
    if (v.public_share > 100) warn.push('Fast-charging share cannot exceed 100%.');
    v.ev_dep_pct = Math.min(v.ev_dep_pct, 100); v.gas_dep_pct = Math.min(v.gas_dep_pct, 100); v.public_share = Math.min(v.public_share, 100);
    if (v.gas_mpg <= 0) { v.gas_mpg = 1; warn.push('mpg must be above 0.'); }
    if (v.miles > 60000) warn.push('That is a very high mileage; resale values would fall much further than assumed.');
    const mi = v.miles * 5, sh = v.public_share / 100;
    const ev = {
      Depreciation: v.ev_price * v.ev_dep_pct / 100,
      Energy: mi * v.ev_kwh / 100 * ((1 - sh) * v.home_price + sh * v.dcfc_price),
      Maintenance: mi * v.ev_maint, Insurance: v.ev_ins * 5, 'EV fee': v.ev_fee * 5, Rebate: -v.incentive,
    };
    const gas = { Depreciation: v.gas_price_car * v.gas_dep_pct / 100, Energy: mi / v.gas_mpg * v.gas_price, Maintenance: mi * v.gas_maint, Insurance: v.gas_ins * 5, 'EV fee': 0, Rebate: 0 };
    const te = Object.values(ev).reduce((a, b) => a + b, 0), tg = Object.values(gas).reduce((a, b) => a + b, 0);
    const d = te - tg;
    el('calc-result').innerHTML = `Five years: <span class="ev">EV ${fmt$(te)}</span> · <span class="gas">petrol ${fmt$(tg)}</span><br>` +
      (Math.abs(d) < 250 ? `<strong>About the same</strong> (difference ${fmt$(Math.abs(d))})` :
        d > 0 ? `The EV costs <strong>${fmt$(d)} more</strong>` : `The EV saves <strong>${fmt$(-d)}</strong>`);
    el('calc-warn').textContent = warn.join(' ') + (warn.length ? ' ' : '') + 'Excludes sales tax, loan interest, base registration and a home charger. Depreciation is applied to the price before any rebate.';
    const comps = ['Depreciation', 'Energy', 'Maintenance', 'Insurance', 'EV fee'];
    const o = base();
    const series = comps.map(k => ({ name: k, type: 'bar', stack: 'c', barMaxWidth: 34, data: [ev[k], gas[k]] }));
    if (v.incentive > 0) series.push({ name: 'Rebate', type: 'bar', stack: 'r', barMaxWidth: 34, data: [-v.incentive, 0], itemStyle: { color: css('--good') } });
    Object.assign(o, {
      color: compColors(), animation: false,
      grid: { left: 8, right: 70, top: narrow() ? 56 : 40, bottom: 8, containLabel: true },
      tooltip: Object.assign(o.tooltip, { valueFormatter: x => fmt$(x) }),
      xAxis: Object.assign({ type: 'value', splitNumber: narrow() ? 3 : 5, axisLabel: { formatter: x => fmtK(x), color: css('--muted') } }, { splitLine: { lineStyle: { color: css('--line') } } }),
      yAxis: Object.assign({ type: 'category', inverse: true, data: ['EV', 'Petrol'] }, axisStyle(), { axisLabel: { color: css('--ink') } }),
      series,
    });
    if (!calcChart) calcChart = echarts.init(el('ch-calc'));
    calcChart.setOption(o, true);
  }

  function drawAll() { [drawAAA, drawExample, drawMiles, drawDep, drawEnergy].forEach(f => f()); }
  function redraw() {
    charts.splice(0).forEach(([c]) => c.dispose());
    if (calcChart) { calcChart.dispose(); calcChart = null; }
    drawAll(); compute();
  }

  // Number the reference links in order of the sources list
  function numberRefs() {
    const order = [...document.querySelectorAll('#source-list li')].map(li => li.id);
    document.querySelectorAll('a.ref').forEach(a => {
      const id = a.getAttribute('href').slice(1); const n = order.indexOf(id) + 1;
      a.textContent = n ? '[' + n + ']' : '';
    });
  }

  function init() {
    numberRefs();
    if (!window.echarts) { document.querySelectorAll('.chart').forEach(d => d.textContent = 'The chart library did not load; the data tables below show the same numbers.'); return; }
    drawAll();
    el('preset').addEventListener('change', e => loadPreset(e.target.value));
    ids.forEach(id => el(id).addEventListener('input', compute));
    loadPreset('equinox');
    let t; window.addEventListener('resize', () => { clearTimeout(t); t = setTimeout(() => { charts.forEach(([c]) => c.resize()); calcChart && calcChart.resize(); }, 150); });
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', redraw);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
