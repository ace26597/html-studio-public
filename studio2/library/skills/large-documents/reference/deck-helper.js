// The frame of ONE deck, written once for this document from build/look.json (copy this file to
// tools/deck_helper.js and change it to the look this person asked for; it is an example of the
// shape, not a house style). Section modules call the functions below and nothing else: they
// never set a colour, a font, a size or a position, and every figure comes from the ledger by
// its key through fact(). pptxgenjs, as the pptx skill installs it (tools/node_modules).
//
//   const deck = makeDeck();                 // reads build/look.json and data/facts.json
//   deck.h.statement({ id: "intro-2", title: "…", text: `… ${deck.h.fact("revenue_2025", { scale: "million" })} …` });
//   await deck.write("files/board-pack.pptx"); // writes the file, then the theme's colours
const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");
const { applyTheme } = require("../../library/skills/pptx/scripts/apply_theme.js");

const ROOT = path.resolve(__dirname, "..");
const read = (file) => JSON.parse(fs.readFileSync(path.join(ROOT, file), "utf8"));

// ---- figures: the only way a number reaches a slide ----------------------------------------
const SYMBOLS = { USD: "$", EUR: "€", GBP: "£", INR: "₹", JPY: "¥" };
const SCALES = { thousand: [1e3, "k"], million: [1e6, "m"], billion: [1e9, "bn"] };

function loadFacts() {
  const file = path.join(ROOT, "data/facts.json");
  if (!fs.existsSync(file)) return {};
  const facts = {};
  for (const fact of JSON.parse(fs.readFileSync(file, "utf8")).facts || []) facts[fact.key] = fact;
  return facts;
}

function format(value, unit, how = {}) {
  const [divisor, suffix] = SCALES[how.scale] || [1, ""];
  const shown = value / divisor;
  const decimals = how.decimals ?? (suffix ? 1 : unit === "%" ? 1 : Number.isInteger(shown) ? 0 : 2);
  const number = new Intl.NumberFormat("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
    .format(Math.abs(shown));
  const sign = shown < 0 ? "-" : "";
  if (unit === "%") return `${sign}${number}%`;
  if (SYMBOLS[unit]) return `${sign}${SYMBOLS[unit]}${number}${suffix}`;
  return `${sign}${number}${suffix}${unit && !how.bare ? " " + unit : ""}`;
}

// ---- the deck ------------------------------------------------------------------------------
function makeDeck() {
  const look = read("build/look.json");
  const facts = loadFacts();
  const pres = new pptxgen();
  pres.layout = look.size || "LAYOUT_WIDE";
  const W = pres.layout === "LAYOUT_WIDE" ? 13.333 : 10, H = 7.5 * (pres.layout === "LAYOUT_WIDE" ? 1 : 0.75);
  const m = look.margin ?? 0.6;
  const c = look.colors, f = look.fonts, s = look.sizes;
  pres.theme = { headFontFace: f.heading, bodyFontFace: f.body };
  const made = [];                 // [{section, id, layout}] in slide order: build/slide-map.json
  let section = null;

  // One layout per slide type; positions and styles live here and nowhere else.
  const frame = [
    { text: { text: look.footer || "", options: { x: m, y: H - 0.45, w: W - 2 * m - 1, h: 0.3, fontSize: s.caption,
      color: c.muted, fontFace: f.body } } },
  ];
  const ph = (name, type, box, options = {}) => ({ placeholder: { options: { name, type, ...box, ...options }, text: "" } });
  const title = (y = m) => ph("title", "title", { x: m, y, w: W - 2 * m, h: 0.9 },
    { fontFace: f.heading, fontSize: s.title, color: c.dk1, bold: true, align: "left", valign: "top", margin: 0 });
  const number = { x: W - m - 0.8, y: H - 0.45, w: 0.8, h: 0.3, fontSize: s.caption, color: c.muted, align: "right" };
  const layouts = {
    TITLE: { background: { color: c.dk2 }, objects: [
      ph("title", "title", { x: m, y: H * 0.34, w: W - 2 * m, h: 1.4 }, { fontFace: f.heading, fontSize: s.cover, color: c.lt1, bold: true, align: "left", margin: 0 }),
      ph("body", "body", { x: m, y: H * 0.34 + 1.5, w: W - 2 * m, h: 0.8 }, { fontFace: f.body, fontSize: s.body, color: c.lt2, margin: 0 })] },
    SECTION: { background: { color: c.lt2 }, objects: [
      ph("title", "title", { x: m, y: H * 0.4, w: W - 2 * m, h: 1.2 }, { fontFace: f.heading, fontSize: s.title, color: c.dk2, bold: true, align: "left", margin: 0 })],
      slideNumber: number },
    STATEMENT: { background: { color: c.lt1 }, objects: [...frame, title(),
      ph("body", "body", { x: m, y: m + 1.3, w: W - 2 * m, h: H - 2 * m - 1.6 }, { fontFace: f.body, fontSize: s.statement, color: c.dk1, valign: "top", margin: 0 })],
      slideNumber: number },
    BULLETS: { background: { color: c.lt1 }, objects: [...frame, title(),
      ph("body", "body", { x: m, y: m + 1.3, w: W - 2 * m, h: H - 2 * m - 1.6 }, { fontFace: f.body, fontSize: s.body, color: c.dk1, valign: "top", margin: 0 })],
      slideNumber: number },
    CHART: { background: { color: c.lt1 }, objects: [...frame, title(),
      ph("chart", "chart", { x: m, y: m + 1.2, w: (W - 2 * m) * 0.64, h: H - 2 * m - 1.6 }, { color: c.dk1 }),
      ph("body", "body", { x: m + (W - 2 * m) * 0.68, y: m + 1.3, w: (W - 2 * m) * 0.32, h: H - 2 * m - 1.7 }, { fontFace: f.body, fontSize: s.body, color: c.dk1, valign: "top", margin: 0 })],
      slideNumber: number },
    TABLE: { background: { color: c.lt1 }, objects: [...frame, title()], slideNumber: number },
  };
  for (const [name, layout] of Object.entries(layouts)) pres.defineSlideMaster({ title: name, ...layout });

  function slide(kind, id) {
    if (!id) throw new Error(`Every slide needs the id the outline gives it (a ${kind} slide has none).`);
    made.push({ section: section && section.id, id, layout: kind });
    return pres.addSlide({ masterName: kind, sectionTitle: section ? section.title : undefined });
  }
  const paragraphs = (lines) => lines.map((text, i) => ({ text, options: { bullet: true, breakLine: i < lines.length - 1, paraSpaceAfter: 6 } }));

  const h = {
    /** A figure from data/facts.json, formatted: fact("revenue_2025", { scale: "million", decimals: 1 }). */
    fact(key, how = {}) {
      const fact = facts[key];
      if (!fact) throw new Error(`No fact "${key}" in data/facts.json. Do not type the number in: leave it out and list it as missing.`);
      return format(Number(fact.value), fact.unit, how);
    },
    /** The raw value of a fact, for a chart's data. */
    value(key) {
      if (!facts[key]) throw new Error(`No fact "${key}" in data/facts.json.`);
      return Number(facts[key].value);
    },
    cover({ id, title, subtitle }) {
      const s1 = slide("TITLE", id);
      s1.addText(title, { placeholder: "title" });
      if (subtitle) s1.addText(subtitle, { placeholder: "body" });
      return s1;
    },
    divider({ id, title }) {
      slide("SECTION", id).addText(title, { placeholder: "title" });
    },
    statement({ id, title, text }) {
      const s1 = slide("STATEMENT", id);
      s1.addText(title, { placeholder: "title" });
      s1.addText(text, { placeholder: "body" });
    },
    bullets({ id, title, points }) {
      const s1 = slide("BULLETS", id);
      s1.addText(title, { placeholder: "title" });
      s1.addText(paragraphs(points), { placeholder: "body" });
    },
    /** chart: { type: "bar" | "line" | "pie", labels: [..], series: [{ name, keys: [fact keys] }] }. */
    chart({ id, title, chart, points = [] }) {
      const s1 = slide("CHART", id);
      s1.addText(title, { placeholder: "title" });
      const data = chart.series.map((one) => ({ name: one.name, labels: chart.labels, values: one.keys.map((k) => h.value(k)) }));
      s1.addChart(pres.ChartType[chart.type || "bar"], data, {
        placeholder: "chart", showValue: true, dataLabelFontSize: s.caption, dataLabelColor: c.dk1,
        dataLabelFontFace: "+mn-lt", catAxisLabelFontFace: "+mn-lt", valAxisLabelFontFace: "+mn-lt",
        catAxisLabelColor: c.muted, valAxisLabelColor: c.muted, catAxisLabelFontSize: s.caption, valAxisLabelFontSize: s.caption,
        valGridLine: { color: c.lt2, size: 0.5 }, catGridLine: { style: "none" }, showLegend: chart.series.length > 1,
        chartColors: chart.series.length > 1 ? [c.accent1, c.accent2, c.accent3, c.accent4, c.accent5, c.accent6] : [c.accent1],
      });
      if (points.length) s1.addText(paragraphs(points), { placeholder: "body" });
    },
    /** rows: arrays of strings (figures through fact()); the header row is styled here. */
    table({ id, title, header, rows }) {
      const s1 = slide("TABLE", id);
      s1.addText(title, { placeholder: "title" });
      const cell = (text, head) => ({ text, options: { bold: head, color: head ? c.lt1 : c.dk1,
        fill: { color: head ? c.dk2 : c.lt1 }, fontFace: f.body, fontSize: s.table, border: { type: "solid", color: c.lt2, pt: 0.75 } } });
      s1.addTable([header.map((t) => cell(t, true)), ...rows.map((r) => r.map((t) => cell(t, false)))],
        { x: m, y: m + 1.25, w: W - 2 * m, autoPage: false, margin: 0.06 });
    },
    sources({ id, title = "Sources", items }) {
      const s1 = slide("BULLETS", id);
      s1.addText(title, { placeholder: "title" });
      s1.addText(paragraphs(items), { placeholder: "body", fontSize: s.caption });
    },
  };

  return {
    pres, h, made,
    begin(next) { section = next; pres.addSection({ title: next.title }); },
    async write(file) {
      const target = path.resolve(ROOT, file);     // from the project folder, or an absolute /tmp path
      await pres.writeFile({ fileName: target });
      await applyTheme(target, { name: look.name || "Deck", colors: look.colors });
      return made;
    },
  };
}

module.exports = { makeDeck, format };
