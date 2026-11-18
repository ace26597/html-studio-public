# The visual review

One pass by a reviewer who did not write the slides (the `slide-reviewer` helper), after every
deterministic check has passed or its remaining findings are known.

## Which slides to look at

Up to 60 slides: all of them. Past 60, the reviewer looks at:

1. every slide a deterministic check flagged (`check_file`'s `by_part`: not found, placeholders,
   conflicts; `validate.py`'s slide errors);
2. the first slide of each layout (from `build/slide-map.json`), the first and last slide, and the
   first slide of each section;
3. every slide with a chart, a table or a picture (their `layout` in the slide map);
4. one in four of the rest, spread evenly (slide 5, 9, 13, …, skipping those already chosen);
5. all of the `overview` sheets `render_document` returns: every slide small, 20 to a picture, to
   compare the look across the whole deck (titles at the same height, the same margins, colours
   that do not drift). They are too small for text.

Write the list into the brief, with the reason for each slide, so the report can say "40 of 104
slides looked at" and which.

## Batches

20 slides a batch (each picture costs the reader up to about 1,600 tokens; 20 leave it room to
think). `render_document` draws at most 60 pages a call: ask for the batch's range with `pages`
(`"41-60"`); the PDF is reused, so only the pictures are drawn. Run batches one after another, or
two reviewers at once at most.

## The questions, per slide (answer yes or no; every yes is a defect)

1. Is any text cut off, overflowing its box or the slide, or broken mid-word or between a number and its unit?
2. Does anything overlap (text over a shape, a line through words, a footer under content)?
3. Is the title somewhere other than where the same layout puts it on other slides?
4. Is any text too small to read or too faint against its background?
5. Is a chart missing a title, units or readable labels, or does it disagree with the numbers in its text?
6. Is a table cut off, cramped, or its columns misaligned?
7. Is there a large empty area, or a box with nothing in it?
8. Is there leftover text: a prompt, a template mark, "TBD", lorem ipsum?
9. Does the slide say something other than its `message` in the outline?

## What the reviewer writes

`build/review.json`, and only that file:

```json
{"looked_at": [3, 4, 5, 17, 41], "pass": 1,
 "defects": [{"slide": 41, "question": 1, "where": "body, third bullet",
              "what": "The last line runs past the bottom of the box", "fix": "Cut the bullet or split the slide"}]}
```

Its final message: the file's path, how many slides it looked at, and how many defects.

## After the review

- Map each defect's slide to its module through `build/slide-map.json`; redo only those modules
  (one `section-writer` per module, given the defects), reassemble, run the deterministic checks
  again, and render only the changed slides' range.
- A second pass looks only at the changed slides. Then stop, whatever remains, and report it.
