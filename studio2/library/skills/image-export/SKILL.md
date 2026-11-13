---
name: image-export
description: Make picture files from a design built as a page - a social card, a banner, a poster, a thumbnail, a logo or icon, a chart image for a deck, a transparent PNG. Use whenever the person asks for an image, a graphic to post or download, a PNG or JPG, or "export this chart as a picture".
---

# Pictures as files

You design in HTML and SVG at an exact pixel size and the studio saves it as a picture
(`render_image`). That covers typography, layout, charts, diagrams, icons, logos and
illustration you can draw. It does not make photographs: use pictures the person supplied or
a generation tool if one is available, and say plainly when neither exists.

## Where things go
- The design: `graphics/<name>.html`, one artboard per page:
  `<main id="art" style="width:1200px;height:630px">…</main>` with `body { margin: 0 }`.
- The file: `media/<name>.png` (flat colour, text, logos, transparency) or `.jpg` (photographs).

## Sizes people ask for
| Use | Pixels |
|---|---|
| Link preview (Open Graph), LinkedIn, X post | 1200 × 630 |
| Instagram feed | 1080 × 1080 or 1080 × 1350 |
| Story, Reel cover | 1080 × 1920 |
| YouTube thumbnail | 1280 × 720 |
| Presentation slide picture | 1920 × 1080 |
| A4 poster at print quality | 1240 × 1754 with `scale: 2` |
| Logo or icon | draw as SVG; export 512 × 512 and 1024 × 1024, `transparent: true` |

## Make it
```
render_image  path: "graphics/launch-card.html"  out: "media/launch-card.png"
              width: 1200  height: 630  scale: 2
```
- `scale: 2` gives twice the pixels of the same design (sharp on phones). Use it for anything
  with small text.
- `selector: "#chart"` saves one element of any page, which is how a chart on the report
  becomes a picture for a deck or a document.
- `transparent: true` drops the page background (give `body` no background).
- `at: 4.5` takes a still from an animated page or a video stage at that second.
- Several sizes of one design: size the artboard from the address
  (`?w=1080&h=1350`, read in a two-line script) and lay out with flex and `clamp()`, then call
  `render_image` once per size.

## Design rules for a picture
- One message. A headline of at most eight words that reads at thumbnail size; test by
  looking at the file small.
- Text at least 4% of the picture's height; 8% margins; contrast that survives a phone in sunlight.
- Real names, figures and logos only. A logo comes from the person's files.
- Fonts that are on the machine (system-ui, Georgia, Arial) or loaded and waited for.

## Check and show
Open the saved file with the Read tool every time; fix clipping, overflow, a wrong crop and
empty space, and save again. Then show the pictures on the page in a small gallery, each
with what it is for, its size, and a download link (`<a href="media/launch-card.png" download>`).
