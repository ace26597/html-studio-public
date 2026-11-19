# Bring your own app and make it live

Moved from the [front page](../../README.md).

Press **New page**, then **An app on this computer**. Type the folder's full path and press
**Check**. The studio reads the folder without writing to it and shows what it found: the kind
of app, the git branch and last commit, uncommitted files, the names of env files, and the
commands it would use to install packages and start the app. Then choose how it works on it:

- **Safe copy** (the default). For a folder under git the project is a clone on its own
  branch; for a plain folder it is a copy. Your folder is not changed. Uncommitted changes and
  env files are taken only if you tick them, and env files are never kept in versions.
- **Edit in place.** The project is your folder itself, for a development machine. The studio
  keeps its versions in a history of its own, so the app's `.git` is not touched. You can give
  the address where the app's dev server already runs; the studio shows it and never starts or
  stops it. Refused for your home folder, a disk root and the studio's own folders.

Press **Create**. A safe copy installs the app's packages the first time it starts; the app
log shows it. Then select or draw on the app and ask for changes, as on any other page.

If the app has a sign-in screen, the agent sees that screen and not what is behind it, unless
you tick **Let the agent use my sign-in** in the project's menu. It is off for an app you
brought, because the agent would then act in that app as you. See
[safety.md](safety.md) for what the studio keeps.

To send a change back, press **Make it live** in the project's top bar, or say "make it live"
in the chat. The studio does the shipping, not the agent:

1. **A recipe, once.** The checks to run, how the change reaches the original, the commands to
   run there afterwards, an address to check, and paths that need the owner. Ask the agent to
   "set up making this live" and it proposes one from the app's own scripts and notes; you
   confirm it on a card, and again whenever it changes.
2. **A card, every time.** The files changed, the check results, exactly what will be written
   where and which commands will run. Nothing reaches your folder before you press Allow. A
   change to a path the recipe marks as protected also needs the project's name typed.
3. **How it arrives.** By default the studio leaves a branch in your repository and touches
   neither your files nor your checked-out branch. Per project you can choose a fast-forward
   merge instead. A plain folder gets the changed files copied, and what they replace is kept.
4. **Going back by itself.** If a command or the address check fails after the change was
   applied, the studio puts things back and says what state they are in.

## When an app stops by itself

An app that had been running for at least 20 seconds and then exits without being asked to is
started again by the studio, at most three times in ten minutes. Its output keeps what the run
that died said last, then one line from the studio with the exit code and how long it had run.
An app that exits sooner (a start that fails) stays stopped with its output, as does one that
has used its three restarts. Exit code 0 does not mean a clean stop: `next dev` exits with 0
when the process that serves its pages has died. That is how this was found, on 19 November
2026: a Next.js site's image library (`sharp` 0.34.5 with libvips 8.17.3) aborts when several
threads read a broken SVG at once, which took the whole development server down, and the
studio left it down. Run in tests against a small server that exits on a signal; the restart of a
real Next.js app after such an abort has not been seen yet.

## Point at code or a running app without bringing it in

Sometimes the project is not the app: you want a report on a code base, a page that explains how
something works, or answers about an app while you build something else. In the chat, beside
**Attach files**, press **Add context** and type one of:

- the full path of a **folder** (`~/dev/my-app`), of any size;
- the full path of a **file** (a spec, a schema, a log);
- the **address** where an app is running (`http://localhost:3000`, or `localhost:3000`).

Press **Check**. The studio reads without writing and shows what it found: the kind of app, the
git branch, about how many files, the names at the top of the folder; or whether the address
answered and its page title. Give it a label and, if it helps, a line on what it is, then **Add**.
A project holds up to 12 of these; they are listed in the row above the message box and in the
project's library panel under **Context**, where you edit or remove them.

What happens then:

- Nothing is copied, uploaded or indexed. On every request the agent is told what the items are,
  that your questions may be about them, and what is at the top of each folder at that moment. It
  searches a folder and reads the files a question needs.
- **It is read only.** The agent's file tools cannot write outside the project at all, and a
  shell command that would change something there asks you first. It is told not to run an
  install, a build or a formatter there; that part is an instruction, not a lock. To try a change
  it copies the files into the project. To have the studio edit the code itself, bring the folder
  in as an app (above).
- An address is read with ordinary requests, and the agent can look at its pages in the studio's
  browser, never signed in as you.
- Refused: your home folder, a disk, key files (`.env`, SSH keys) and the hidden places in your
  home folder where keys and settings live (`~/.ssh`, `~/.aws`, `~/.config`).
- The path is on the computer the studio runs on. From a phone or another computer, or on a
  studio someone shares with you, it is still that computer's folder; files from your own device
  are attached or dropped in the chat.

What has been run for context, on one Mac: a Next.js and FastAPI code base of 692 tracked files
and the address it runs at, both added from the dialog (each check answered in under a tenth of
a second); the dialog, the row above the message box and the panel looked at in a real browser
at desktop, tablet and phone widths; and one real request on the Claude plan (Haiku), which
answered a question about the code from the two files that define the answer and described the
running app's first screen from a look at it, in 19 seconds, with the folder's git state the
same before and after. Not run: a folder past the 50,000-file count, a folder without git of
real size, a studio on another machine or a shared one, a real iPad or phone.

What has been run for bringing an app: a Next.js site attached as a safe copy from the dialog, running in the
studio in about ten seconds with the original unchanged, and a Vite app edited in place beside
its own dev server. Shipping has been run against stand-in folders only; no real app has been
made live with it yet. Detail, limits and the steps for apps the dialog cannot set up:
[`studio2/ATTACH.md`](../ATTACH.md).
