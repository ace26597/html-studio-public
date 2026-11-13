---
name: theme
description: Colour, type and spacing tokens for a page that reads well in light and dark, plus a quick contrast and polish checklist. Use when styling any new page, when a page looks plain or inconsistent, or when the person asks for a restyle.
---

# Theme

Define the look once as CSS variables and use only those in the rest of the stylesheet.

```css
:root {
  color-scheme: light dark;
  --bg: #ffffff; --surface: #f6f7f9; --card: #ffffff; --rule: #e3e6eb;
  --fg: #14171c; --muted: #5b6472; --accent: #1f6feb; --on-accent: #ffffff;
  --good: #1a7f4b; --warn: #9a6700; --bad: #c0362c;
  --c1: #1f6feb; --c2: #d9730d; --c3: #1a7f4b; --c4: #8250df; --c5: #bf3989; --c6: #57606a;
  --radius: 10px; --gap: 1.25rem;
  --font: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f1216; --surface: #161a20; --card: #1a1f27; --rule: #2a313c;
    --fg: #e7eaee; --muted: #9aa4b2; --accent: #6ea8fe; --on-accent: #0f1216;
    --good: #4ac27d; --warn: #d4a72c; --bad: #f47067;
    --c1: #6ea8fe; --c2: #f0994a; --c3: #4ac27d; --c4: #b392f0; --c5: #f778ba; --c6: #9aa4b2;
  }
}
body { margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.55 var(--font); }
```

## Rules
- No literal colours outside `:root`. Charts read the variables at draw time
  (`getComputedStyle(document.documentElement).getPropertyValue('--c1')`) and redraw when
  `matchMedia('(prefers-color-scheme: dark)')` changes.
- One accent colour. Status colours only for status. Series colours `--c1…--c6` in a fixed
  order; the same thing keeps the same colour across every chart on the page.
- Body text contrast at least 4.5:1 in both schemes; `--muted` is for secondary text, never
  for the main content.
- Type scale: 2rem page title, 1.4rem section, 1.1rem card title, 1rem body, 0.85rem notes.
  Sentence case. Numbers in tables use `font-variant-numeric: tabular-nums`.
- Spacing in multiples of 0.25rem; cards have 1–1.25rem padding, a 1px `--rule` border and
  `--radius`. Avoid shadows in dark mode.
- A brand palette from the person replaces `--accent` and `--c1…`; keep the neutrals.

## Before finishing
Look at the page with `look_at_page` twice: default and `dark: true`. Check that nothing is
invisible, charts changed colour with the page, and tables are still readable.
