---
name: generated-media
description: Get a photograph, an illustration, a short film clip, music or a voice from the generation service when code cannot draw it - a hero photo, a product-style shot, background footage for a video, a soundtrack. Use when the person asks for generated or AI images or video, a photo-real picture, footage, music or narration.
---

# Generated pictures, clips and sound

`generate_media` asks a generation service (Higgsfield, on the person's account). Every call
spends their credits. The studio shows them the price and waits for their answer before each
one, so every call interrupts them. Treat it as something you order, not something you try.

## When
- The person asked for generated imagery, footage, music or a voice; or the piece plainly
  needs a photograph and there is none in the project.
- Not for anything you can draw (icons, diagrams, charts, type, simple illustration: use SVG
  and `render_image`), and not for a picture of something real the person can supply.

## It is an illustration, never evidence
- A generated picture must not be presented as a photo of the person's real product,
  installation, premises, team or customers. Caption it where it is shown
  ("Illustration, generated") and say in your answer which pictures are generated.
- No real people, no brand logos or trademarked characters, no text baked into the picture
  (set text in HTML over it instead).

## Order well, once
1. Decide exactly what the frame is before asking: subject, setting, light, lens and
   distance, mood, colours that match the page, what must be left empty for text. One
   paragraph, concrete nouns, no lists of adjectives.
2. Ask for the shape you will use: `aspect_ratio` "16:9", "9:16", "1:1", "4:5".
3. Start cheap: `quality: "medium"`, `resolution: "1k"` for a picture costs a small part of the
   default (high, 2k), and a short clip at the fast, 480p setting a small part of one at 1080p.
   Prices change, so none are written here: the price the person sees on the approval card is
   the service's own quote for your request.
4. One at a time. Look at the result (Read the file) before ordering the next.
5. If the person says no, do not ask again for the same thing. If a generation fails, do not
   switch models to get round it; report what the service said.

```
generate_media  kind: "image"  out: "media/hero.png"  aspect_ratio: "16:9"  quality: "medium"  resolution: "1k"
                prompt: "Wide photograph of a tiled rooftop terrace with rows of potted herbs at golden hour,
                         low sun from the left, clear sky filling the upper third, no people, no text"
```
`generation_models` (free) lists the models for a kind and, with `model`, what one accepts:
use it before asking for sound, which has no default model, or when the person names a model.

## Clips
- A clip follows a picture best: generate or choose the still first, then
  `kind: "video", start_image: "media/hero.png", prompt: "slow push in, light shifting"`.
- To use a clip in a video you build (the `motion-video` skill), put it in a scene as the
  background (`<video>` with `object-fit: cover`), set your titles over it in HTML, check the
  frames, then render.
- `references` are project pictures the service should follow (a product photo the person
  uploaded, for instance).

## Show it
Put the file on the page like any other media, with its caption. In a video, generated
footage or stills go under real text and real figures; the claims still come from sources.
