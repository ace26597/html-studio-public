// What a section worker writes: build/sections/<nn>-<section id>.js. One function that makes the
// section's slides, in the outline's order, with the ids the outline gives them, through the
// helper's functions only. Every figure is h.fact("<key>") or h.value("<key>"); a figure the
// ledger does not have is left out and listed in the worker's reply, never typed in.
module.exports = (h) => {
  h.divider({ id: "regions-1", title: "Two regions carry the year" });
  h.statement({
    id: "regions-2",
    title: "North sold most of the units",
    text: `North sold ${h.fact("units_north", { bare: true })} of the ${h.fact("units_total")} sold in 2025.`,
  });
  h.chart({
    id: "regions-3",
    title: "Units by region, 2025",
    chart: { type: "bar", labels: ["North", "South"], series: [{ name: "Units", keys: ["units_north", "units_south"] }] },
    points: [`Revenue per unit: ${h.fact("revenue_per_unit")}`, "Source: company sales file (data/sales.csv)"],
  });
  h.table({
    id: "regions-4",
    title: "The two regions side by side",
    header: ["Region", "Units, 2025"],
    rows: [["North", h.fact("units_north", { bare: true })], ["South", h.fact("units_south", { bare: true })]],
  });
};
