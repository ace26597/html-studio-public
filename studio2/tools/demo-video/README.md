# Recording the demo video

The video in `studio2/docs/demo/` is a recording of the studio's real interface, driven by
Playwright, with a real agent run. Nothing in it is staged; waiting is time-lapsed in the edit
and the clock in the caption strip shows real elapsed time.

Run from this folder with the studio running on port 8200 (`../../.venv/bin/python`):

```sh
python part_a.py frames/a                 # new project, type the request, record until the turn ends (50-minute cap)
python part_a2.py <project id> frames/a2  # only if the turn outlived the cap: keeps recording until it ends
python part_b.py <project id> frames/b --nav "Tab one|Tab two" --target "A heading" --ask "What to change"
python assemble.py plan.json ../../docs/demo/studio-demo.mp4
```

- `rec.py` takes timestamped screenshots (1920x1200) and draws the pointer; phases are named so
  `plan.json` can give each one a speed and a caption. `still_cap` cuts down stretches where the
  screen does not change.
- `part_b.py --dry` rehearses the clicks without sending a request.
- The agent cannot be given approvals by the recorder; if a run stops on an approval card,
  answer it in the studio yourself.
- The GIF: `ffmpeg -i studio-demo.mp4 -vf "setpts=PTS/1.6,fps=8,scale=760:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" -loop 0 studio-demo.gif`

## The 13 November 2026 videos

The report, edit-and-draw and app videos, the six clips and the start-to-end video were made with
the scripts below. They take the request and the targets as arguments, so another subject needs no
edit to the scripts.

```sh
python run.py frames/report --name "Project name" --prompt "The request" [--minutes 75]   # blank page → the turn ends
python part_tour.py <project id> frames/report-tour --stops "A heading|Another heading"     # walk through the page, files, versions
python part_c.py <project id> frames/hands --edit "Old text" --edit-to "New text" \
    --select "Words in an element" --select-ask "What to change" \
    --box-at "Words in an element" --box-label "What goes there" \
    --circle "Words in an element" --note "A note" --draw-ask "What to say with the drawing"   # each part is optional; --dry rehearses
python part_app.py <project id> frames/app-use --city "Tokyo" --select "A heading" --ask "What to change"   # use an app, its log, then change it
python part_use.py <project id> frames/use --do "type|#field|1400" --do "click|input[value='2.5']"   # use what is on the page
python assemble.py plans/report.json ../../docs/demo/demo-report.mp4      # one plan per video and per clip, in plans/
python gif.py ../../docs/demo/demo-report.mp4 ../../docs/demo/demo-report.gif --width 640 --speed 2.0 --fps 6 --colors 64
python sheet.py video.mp4 sheet.jpg 5 20 40                                 # frames at those seconds, to check a video by eye
```

- `steps.py` holds what the hands-on scripts share (find an element by its words, switch mode, ask
  in the chat and wait for the turn).
- A plan can keep part of a phase: `after` and `until` are real seconds from the start of the phase.
- An approval card no longer ends a recording: it is printed (`WAITING FOR APPROVAL`) and filmed,
  and the run carries on when somebody answers it in the studio. None came up in these runs.
- The page reloads when a turn or an inline edit changes a file, and when an app saves its own
  data. A script must ask for the preview frame again after that (`frame()`), or Playwright fails
  with "Frame was detached".
- What was cut: about three seconds at the end of two change turns, where the chat showed the
  agent's closing message before the recorder closed the chat (`after`), and the tail of one
  working phase. One take of the app tour and one of the hands-on session failed on the detached
  frame and were recorded again; the report was restored to the agent's version in between, which
  is why its version list shows a restore.
- The cost shown in the chat during these recordings ("≈ $11.0 at API prices" on a 37-second
  turn) is the conversation's running total, which the studio showed as each turn's cost until
  this day's fix. The videos were not recorded again.


## Depth and the check (13 November 2026, later)

`part_effort.py` records a request with the composer's new controls set by real clicks: a depth
step (Quick / Standard / Deep, the effort button's menu, `menuitemradio`) and the **Check the result** switch
(found by its label) before the request is typed, or **Check this page** on an existing project
instead of typing. After the request it scrolls the chat to the result card (`.verification`),
opens its details and, with `--scroll`, wheels slowly down the finished page.

```sh
python part_effort.py frames/quick --name "Project name" --prompt "The request" --depth quick
python part_effort.py frames/deep --name "Project name" --prompt "The request" --depth deep --verify --scroll --minutes 75
python part_effort.py frames/checkpage --project <id> --check-page
python assemble.py plans/clip-depth.json ../../docs/demo/clips/depth.mp4     # also clip-check.json, deep-check.json
```

- A new project has no `events.jsonl` until its first request; read it as empty, not as an error.
- A new project's model is unset (`null`, the account's default). The script sets it with
  `--model` (default `opus`) so the run is pinned; `--project` keeps what the project has.
- Phases: `blank`, `depth`, `verify`, `typing` (also the press of Check this page), `sent`,
  `working`, `finished`, `card`, `details`, `scroll`, `end`.
- The clock in the caption starts at the Enter press and the recorder notices the end on a 5-second
  poll, so the clock at the end reads a few seconds more than the request's own duration. Put the
  server's figure (`turn_finished.usage.duration_ms`) in the captions.
- When a request changes an app, the studio restarts the app's back end as the request ends, at the
  moment the preview reloads. In the check recording the weather app's chart stayed empty after
  that until the page was opened again; the app does not retry its first call. It is filmed as it
  happened.
- A Check this page run asked to use the machine's Playwright connector (picked by the studio, not
  switched on by the person). It was denied; the agent carried on with `look_at_page`.
- `sheet.py` fails on a time past the end of the video; ask for times inside it.


## The live-data app (17 November 2026)

```sh
python part_effort.py frames/glp1 --name "The GLP-1 race" --prompt "The request" --depth deep --verify --scroll --minutes 100 --model opus
python assemble.py plans/glp1.json ../../docs/demo/demo-glp1.mp4
python gif.py ../../docs/demo/demo-glp1.mp4 ../../docs/demo/demo-glp1.gif --width 640 --speed 2.0 --fps 6 --colors 64
```

- The depth steps and the Check the result switch moved that day from a row of buttons in the
  composer into the menu behind the effort button. `part_effort.py` opens the menu (`.effort-btn`)
  and clicks the step (`menuitemradio`) and the switch (`menuitemcheckbox`) there. The first take
  failed on the old selector before anything was sent; its empty project was deleted.
- The result card, its details and the walk down the page are in the same recording as the run
  (no fresh load afterwards).
- The clock at the end reads 26:15 and the server's figure is 26 min 2 s: the clock starts at the
  Enter press, the server's count after the library has been picked. The captions use the server's.
- The stages in the captions (trying the APIs, the back end, the page, looking at it, the checker,
  the fixes) were cut at times read from the project's `events.jsonl` tool calls.
- Checked as contact sheets from the finished file, not played from start to end by a person. The
  tool rows show paths under the Mac's user folder, as the earlier videos do.

