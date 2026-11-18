# Examples

Moved from the [front page](../../README.md). Pictures link to the full-size videos.

[![One request from a blank page to a finished data explorer, then a change made by pointing at the page](demo/studio-demo.gif)](demo/studio-demo.mp4)

*One real run in the real interface, 95 seconds of video: 52 minutes of agent work shown as a
time-lapse, then a change made by selecting a heading and asking, which took 34 seconds. The
clock in the corner shows real time. Recorded 12 November 2026.
[Full-size video](demo/studio-demo.mp4) · [how it was recorded](../tools/demo-video/README.md)*

## More examples

Three more real runs, recorded on 13 November 2026 in the same way: the real interface, a real
agent on Claude Opus, waiting shown as a time-lapse with the real clock in the corner. Each picture
is a shortened GIF; the link under it is the full-size video.
[All three as one video, start to end](demo/demo-full.mp4) (4 minutes 48 seconds).

**A sourced report from one question.** "Should I replace a gas furnace with a heat pump in a
Minneapolis winter?"

[![A question typed into a blank page becomes a sourced report with charts, tables and a list of what could not be confirmed](demo/demo-report.gif)](demo/demo-report.mp4)

*21 minutes of agent work in 81 seconds of video. The agent reported 66 documents saved and 39
cited; the page's own check lists 168 figures compared with their saved sources (148 quoted, 20
calculated from quoted ones), none failed. The report has a section on what it could not confirm.
A match means the words are in the saved source, not that the source is right.
[Full-size video](demo/demo-report.mp4)*

**Change it by hand.** On that report: retype the title in place, point at the checklist and ask
for tick boxes, then draw a box, a circle and a note on the page and send the drawing.

[![The title is edited in place, a checklist is selected and made interactive, and a drawing on the page becomes a working calculator](demo/demo-edit-draw.gif)](demo/demo-edit-draw.mp4)

*The checklist took 37 seconds and the calculator, built from the drawing, 76 seconds. Every
change is a version. [Full-size video](demo/demo-edit-draw.mp4)*

**An app from one request.** A weather comparison with a back end of its own that saves the list
of cities, on live Open-Meteo data.

[![A request becomes a running app with a back end; a city is added, the app's log is opened, and a second chart is asked for by pointing at the first](demo/demo-app.gif)](demo/demo-app.mp4)

*The app took 1 minute 49 seconds and the second chart 24 seconds.
[Full-size video](demo/demo-app.mp4)*

These ran on a Claude plan, which is not billed per request. At API prices the agent program put
the report at about $10.60, the two changes to it at about $1.05 together, the app at about $0.50
and its second chart at about $0.14.

## Deep, with Check the result

The switch is now labelled **Verify after each request**, and **Check this page** is now
**Verify content**; the recordings on this page were made before the rename and show the earlier names.

**One research question at depth Deep, with the check switched on.** "How much does it cost to
own an electric car compared with a petrol car over five years in the US? Purchase price, fuel or
electricity, maintenance, insurance, incentives that exist today. Charts where numbers help."

[![Deep and Check the result are chosen, six researchers work, a checker goes over the page, and the result card and the finished report are shown](demo/demo-deep-check.gif)](demo/demo-deep-check.mp4)

*25 minutes 38 seconds of agent work in 80 seconds of video, recorded 13 November 2026 on Claude
Opus. The agent wrote a plan, then six researcher sub-agents ran in parallel for about six
minutes; 126 sources were saved and 58 are cited on the page, which has a section on what could
not be confirmed. A separate checker sub-agent then went over the page, and the agent reported the
check once: 387 items, 373 matched, 13 fixed, none left wrong, 1 could not be checked (a baseline
taken from a PDF table). Five figures picked by hand were in their saved sources. One reported fix,
a source missing from the list, is not on the final page: two citations (Kelley Blue Book and AAA)
still have no entry in the source list. The agent's own closing message says 11 problems were
fixed, the card 13. Two shell commands asked first: one, editing the page, was allowed; one that
also deleted files outside the project folder was denied, and the agent made its edit without it.
The waits on those two cards (about two minutes) are in the 25 minutes 38 seconds. The result card
and the walk through the page were filmed on a fresh load after the request ended. At API prices
the agent program put it at about $15.0.
[Full-size video](demo/demo-deep-check.mp4)*

**Quick** on a small request ("A one-page comparison of the three main ways to heat a home: gas
furnace, heat pump, electric baseboard. Short.") took 3 minutes 15 seconds: 35 tool calls, no
sub-agents, 5 sources saved, one table with 4 sourced figures, about $0.64 at API prices.
**Check this page** on the weather app took 5 minutes 26 seconds: one checker sub-agent, 343
checked, 337 matched, 6 fixed, among them both charts drawing Tokyo's days under the wrong dates.

## A live-data app, Deep with Check the result

**One request, a dashboard app on two public APIs.** "Build a live dashboard app called The GLP-1
race, about the obesity and diabetes drugs: semaglutide, tirzepatide, liraglutide and dulaglutide,
plus the newer ones still in trials […]. Use only free public data, fetched live by the app's own
back end and cached: the ClinicalTrials.gov API (version 2) for the trials, and openFDA for
adverse-event reports and drug labels. […] Say clearly that FDA reports are counts of reports, not
rates, and cannot be compared as risk. Never invent a number: if a source fails, say so on the
page. […]" The whole request is in the video.

[![Deep and Check the result are chosen in the effort menu, the agent tries the two APIs, writes a back end and a page, looks at it on a phone and a desktop, a checker recomputes the figures, and the finished dashboard is scrolled](demo/demo-glp1.gif)](demo/demo-glp1.mp4)

*26 minutes 2 seconds of agent work in 78 seconds of video, recorded 17 November 2026 on Claude
Opus: one request, 104 tool calls, no approval card. The app has a back end of its own that
fetches both sources, keeps a copy and fetches again every 12 hours (trials) and 24 hours (FDA
data). As fetched that evening: 1,763 trials across nine drugs from nine searches, 598 lead
sponsors, sites in 77 countries, and FDA report counts for the five drugs with a US label. The
page has filters by drug, phase, status, study type and sponsor, a drug-by-phase grid, a phase 3
finish timeline, a world map of sites, a trial list where each trial opens to its record with a
link to ClinicalTrials.gov, and a section that lists every query with the time it was fetched.
A separate checker sub-agent recomputed the figures from the app's own data and asked the two
sources directly; the agent reported the check once: 38 items, 29 matched, 9 fixed, none left
wrong. The nine were wording, definitions and layout (among them a "Cities" count that counted
coordinates, not city names), not figures that disagreed with a source. The studio's own
re-check of the page agreed (7 of 7 quotes). Checked by hand afterwards: three figures against
the sources directly (812 semaglutide trials, 78,023 semaglutide reports, 181,068 tirzepatide
reports; all three equal), the filters (1,763 trials, 321 with Tirzepatide, 56 with Phase 3
added, the same as the grid's cell), and no console errors or failed requests at 390 and 1440
pixels in light and dark. Left as built: two chart labels overlap on a phone, the sources
section leaves an empty gap on a wide screen, and the phone page is very long. FDA figures are
counts of reports, not rates; the page says so. The numbers are live and will have moved. At API
prices the agent program put it at about $6.48.
[Full-size video](demo/demo-glp1.mp4) · [the app's files, and how to run it](examples/glp1-race/README.md)*

## Checks, a sign-in, a diagram and a 40-slide deck (18 November)

Seven real requests on a throwaway studio, on a Claude plan (about $15.91 at API prices, 53
minutes in all). They are not recorded as videos and their projects are not kept as examples.

- **A wrong number in a deck.** A five-slide deck was built from the past week's USGS earthquake
  feed, then its headline total was changed by hand from 106 to 113. **Verify content** named
  slide 1, put 106 back from the saved feed, and the studio's own re-check agreed on deck and
  page, 24 figures of 24 each (5 minutes, about $1.50).
- **An app behind a sign-in.** The agent built a task board with a sign-in screen. The studio
  was restarted, a separate browser signed in, and **Polish the look** was pressed: every look
  used that sign-in, saw the dashboard without typing a password, and four layout defects were
  fixed (3 minutes, about $0.87). The app shows its demo password on the sign-in screen, so the
  agent also signed in by itself on some looks.
- **A diagram and three conversions.** One request added a diagram of how the brief is made, in
  the page's own colours, the deck as a PDF, and a Word summary with the same text as RTF (2
  minutes, about $1.12).
- **A 40-slide board pack**, outline first, on India's solar build-out from Ember, ministry and
  Central Electricity Authority data: 15 minutes and about $3.72 for the outline (20 sources
  saved, a ledger of 93 figures), 16 minutes and about $5.51 for the build. All 445 figures in
  the deck traced to a saved source or data file; a separate reviewer reported 47 layout and
  wording issues over the 40 slides and the author fixed 46. The agent wrote every section
  itself; section writers working in parallel were not used.

## One feature at a time

Cut from the same recordings.

| | |
|---|---|
| **Ask, and watch it work**<br>[![A request is typed and the agent researches and writes the page](demo/clips/ask-report.gif)](demo/clips/ask-report.mp4) | **Edit text in place**<br>[![A heading is clicked and retyped, and saved as a version](demo/clips/edit-text.gif)](demo/clips/edit-text.mp4) |
| **Point at a part and ask**<br>[![A checklist is selected and turned into tick boxes](demo/clips/select-ask.gif)](demo/clips/select-ask.mp4) | **Draw on the page**<br>[![A box, a circle and a note are drawn on the page and built by the agent](demo/clips/draw.gif)](demo/clips/draw.mp4) |
| **An app with its own back end**<br>[![A request becomes a running app, which is then used](demo/clips/app.gif)](demo/clips/app.mp4) | **Files and versions**<br>[![The project's files and its list of versions](demo/clips/versions-files.gif)](demo/clips/versions-files.mp4) |
| **Choose how much to do**<br>[![Quick is chosen under the chat box and a short comparison page comes back](demo/clips/depth.gif)](demo/clips/depth.mp4) | **Check the page**<br>[![Check this page is pressed on an app and a result card lists what was checked and fixed](demo/clips/check.gif)](demo/clips/check.mp4) |
