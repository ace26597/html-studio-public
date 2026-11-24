---
name: page-animation
description: Add motion to a page so it explains or guides - entrances, scroll-driven reveals, animated charts and numbers, SVG line drawing, hover and press feedback, a scroll-told story. Use whenever the person asks for animation, motion, "make it feel alive", transitions, a scrollytelling page or an animated hero.
---

# Animation on a page

Motion earns its place when it shows order (what to read first), change (what just happened)
or cause (this moved because of that). Decide which of the three each animation is for; if it
is none, leave it out.

## Pick the lightest tool that does it
| Need | Use |
|---|---|
| Hover, press, open and close, theme change | CSS `transition` on `transform`, `opacity`, colour |
| An entrance, a loop, a drawn line | CSS `@keyframes` |
| Reveal as it scrolls into view | `IntersectionObserver` adding a class once; or `animation-timeline: view()` inside `@supports (animation-timeline: view())` with the observer as the fallback |
| Progress tied to scroll position, a pinned section that changes as you scroll | GSAP with ScrollTrigger (`https://cdn.jsdelivr.net/npm/gsap@3.15.1/dist/gsap.min.js` and `…/dist/ScrollTrigger.min.js`) |
| A sequence of many parts, timed against each other | a GSAP timeline, or the Web Animations API (`el.animate(…)`) |
| Numbers counting up, a chart drawing itself | `requestAnimationFrame` from a start time, eased; a chart library's own animation |
| Thousands of moving things | one `<canvas>`, not thousands of elements |

## Rules
- Animate `transform` and `opacity`. Animating width, height, top, left or box-shadow makes the
  page stutter; if a size must change, scale it.
- Timing: feedback 100 to 200 ms; entrances 300 to 600 ms with an ease-out
  (`cubic-bezier(.2,.8,.2,1)`); a group staggered 40 to 80 ms; exits shorter than entrances.
  Nothing a person has to wait for takes over a second.
- An entrance runs once. Loops are for things that are genuinely ongoing (a live indicator)
  and must be slow and small.
- A number that counts up ends on the exact, sourced value and is in the page as text from
  the start (set the final value in the HTML, animate from zero in script), so it is right
  with scripts off and in the checked-numbers pass.
- Content must be visible without the animation: start states are applied by script
  (`document.documentElement.classList.add("js")` and style under `.js`), never by CSS alone.
- Respect `prefers-reduced-motion: reduce`: no movement, cross-fades at most, counters show
  their final value. Wrap the motion in `@media (prefers-reduced-motion: no-preference)` or
  check `matchMedia` in script.
- Scroll stories: the graphic is `position: sticky`, the steps scroll past it, and each step
  sets the graphic's state. Every state is reachable by scrolling back. Works with the keyboard.

## See what you made
A screenshot shows one instant. To see motion use `render_video` with `check: true` on the
page itself: `{path: "index.html", check: true, duration: 3, width: 1440, height: 900}` returns
a sheet of frames across the first three seconds (`times: [0.1, 0.4, 0.8, 2]` to look at
chosen moments). Look for content that starts invisible and never arrives, things that
overlap mid-way, and a layout that jumps when the animation ends. For hover, click and
scroll states use `look_at_page` with `actions`.
