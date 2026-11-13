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

What has been run: a Next.js site attached as a safe copy from the dialog, running in the
studio in about ten seconds with the original unchanged, and a Vite app edited in place beside
its own dev server. Shipping has been run against stand-in folders only; no real app has been
made live with it yet. Detail, limits and the steps for apps the dialog cannot set up:
[`studio2/ATTACH.md`](../ATTACH.md).
