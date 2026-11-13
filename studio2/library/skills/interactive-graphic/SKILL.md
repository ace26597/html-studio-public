---
name: interactive-graphic
description: Build a graphic a person moves around in - a zoomable diagram or system map, a node-link network, a geographic map, a timeline to scrub, an explorable process or org chart, a large scatter to pan and filter. Use whenever the person asks for an interactive or navigable graphic, a map, a network graph, "zoom and pan", an explorable diagram or an explainer you can click through.
---

# Navigable graphics

The graphic is the page's main content: give it room (70 to 85vh), a title that says what it
shows, and a side panel or strip for the details of whatever is selected.

## Pick the build
| Shape of the thing | Build |
|---|---|
| Diagram, flow, architecture, org chart, floor plan | SVG, with `d3-zoom` for pan and zoom; lay out by hand, with `d3-hierarchy` (trees) or `dagre`/`elkjs` (flows) |
| Network of nodes and links | `d3-force` to lay out once, then stop the simulation and let people drag; over about 1,500 nodes draw on `<canvas>` |
| Countries, regions, points on a map | `d3-geo` with TopoJSON (`world-atlas`, `us-atlas` from jsDelivr) for a shaded map; MapLibre GL with OpenFreeMap tiles when streets matter |
| Events over time | a horizontal time axis with `d3-zoom` restricted to x; brush to select a range |
| Many points (tens of thousands) | `<canvas>` with a quadtree for picking, or deck.gl |
| A process to step through | a sticky graphic and steps (see `page-animation`), plus Previous and Next buttons |

D3 modules: `https://cdn.jsdelivr.net/npm/d3@7/+esm` (or the single module you need).

## What every one needs
- **Move:** drag to pan, wheel and pinch to zoom, double-click to zoom in. Buttons for +, − and
  Reset in a corner, and the same on the keyboard (arrows, `+`, `-`, `0`). Set zoom limits and
  keep the content from being dragged out of view (`translateExtent`).
- **Start framed:** the whole thing fits on load with a margin, at any window size.
- **Detail by zoom:** show group names when far out and item labels as you zoom in; never a
  cloud of overlapping text. Line widths and label sizes stay constant on screen
  (`vector-effect: non-scaling-stroke`; scale text by `1/k`).
- **Find:** a search box that jumps to and highlights the match, when there are more than
  about thirty items.
- **Select:** click or tap shows the item's facts and its sources in the panel and highlights
  its neighbours; clicking the background clears. The selection is in the address
  (`location.hash`) so a link opens on it.
- **Hover is a bonus,** not the only way to information: a phone has no hover.
- **Legend and scale:** what colour, size and line style mean; a scale bar or units on a map.
- **Keyboard and readers:** items are focusable (`tabindex="0"`, `role="button"`, an
  `aria-label`), Enter selects, and the same data is in a table under the graphic.
- **Data** comes from files in the project with their sources (the `sourced-report` skill);
  the graphic reads the file, it does not carry a second copy typed into the script.
- Motion between states is 300 to 500 ms and can be turned off by `prefers-reduced-motion`.

## Check it
`look_at_page`, then again with `actions`: click a zoom button, click an item, type in the
search box. Look for labels on top of each other, content that starts off-screen or tiny, a
panel that covers the graphic at 1440 px, and errors in the console list. Try width 390 too:
the panel goes under the graphic and the buttons stay reachable with a thumb.
