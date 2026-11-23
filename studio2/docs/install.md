# Install and first run

Moved from the [front page](../../README.md). Model accounts are in [models.md](models.md).

## Install

You need macOS or Linux (x86-64 or ARM64), `git`, `bash` and `curl` or `wget`. Windows is not
supported.

```sh
git clone https://github.com/ace26597/html-studio-public.git html-studio
./html-studio/studio2/install.sh
```

The installer fetches what is missing and nothing else:

- `uv`, and through it Python 3.12 and the studio's packages, into `studio2/.venv`. The
  packages are the exact versions in `studio2/server-requirements.txt`, each checked by hash,
  not whatever is newest that day.
- Node 20.19 or later in 20.x, or 22.12 or later (what the interface's build tool runs on). It
  uses the one on your PATH when it is one of those; otherwise it downloads a private copy
  from nodejs.org into `studio2/.tools` and verifies its checksum.
- It builds the interface and writes a `studio` command into `~/.local/bin`.

It does not use sudo, and it is safe to run again. It asks up to four questions; `--yes` asks
nothing and answers no to all of them:

| Question | Default | What a yes does |
|---|---|---|
| Download a browser? (only when it finds none the assistant can drive) | no | Downloads a Chromium of about 170 MB. Without one the assistant cannot take screenshots, check its own pages or render video |
| Install LiteLLM? | no | About 430 MB. Only needed for a key from OpenAI, OpenRouter, Azure OpenAI or Gemini |
| Run setup now? | yes | Asks the three questions below |
| Start the studio now? | yes | Starts it and opens the browser |

It also reports whether `ffmpeg` (video files), LibreOffice (PowerPoint, Word and Excel files,
and converting them to PDF, RTF or ODT) and pandoc (Markdown to Word, EPUB or HTML, and Word to
Markdown) are on the machine, with the command that installs each. All three are optional.
Diagrams need nothing extra: the studio carries its own copy of Mermaid.

**Looking at pages** (screenshots, the page check, video) needs two things. The installer adds
the first; the second is the browser question above.

- The `look` extra: `playwright==1.62.0`, pinned to the Chromium build it drives. To add it
  by hand: `cd studio2 && uv pip install -e '.[look]'`.
- A Chromium already in the Playwright cache. The studio looks in `$PLAYWRIGHT_BROWSERS_PATH`
  when it is set, else `~/Library/Caches/ms-playwright`, else `~/.cache/ms-playwright`, and
  never downloads one itself. Get one with `studio2/.venv/bin/python -m playwright install chromium`
  (about 170 MB), or point `PLAYWRIGHT_BROWSERS_PATH` at a cache that already has one.

A running studio notices either within a few seconds of it being installed; if it still says it
cannot look, restart it (`studio stop`, `studio start`). `studio doctor` says which one is missing.

If your shell says `studio: command not found`, `~/.local/bin` is not on your PATH. The
installer prints the line to add to your shell profile; until then use
`./html-studio/studio2/.venv/bin/studio`.

**What has been run.** macOS only, in a scratch home folder with nothing but the system's own
programs on the PATH: the installer finished in 10 seconds and the studio answered within 3
seconds. A brand-new macOS user account has not been tried. On Linux it is installed and running
on Red Hat Enterprise Linux (the owner's report, 13 November 2026); Debian and Ubuntu have not been run.

## First run

If you said yes to the installer's last two questions, this has already happened. Otherwise:

```sh
studio setup     # three questions
studio start     # starts in the background, prints the address, opens the browser
```

Setup asks three things. You can answer them in the terminal, or skip `studio setup` and
answer them on the screen the studio shows when nothing is configured yet (later at `/setup`).

1. **Which model account.** See [Model accounts](models.md). Setup says whether it found
   a Claude sign-in on this computer.
2. **Where it runs.** This computer only (the default), or also on a private network address,
   and the port (8200 unless you change it). Anyone who can reach that address can run commands
   on this computer through the assistant. Other devices sign in once with the access key that
   setup makes; `studio access-key` prints it again.
3. **Who uses it.** Just you, or shared with other people. Shared switches the Claude plan off
   and makes every device sign in with an access key. (Behind a gate that signs people in, the
   owners you name in `STUDIO2_OWNERS` keep the plan for their own requests.)

Setup writes `~/.config/studio2/config.json`. When that file already exists, setup shows what
would change first: in the terminal it then asks, and a command with flags writes only with
`--yes`. So changing one thing later looks like `studio setup --port 8400 --yes`.

`studio start` prints the address, normally `http://127.0.0.1:8200/`. When another program
holds that port it picks a free one and says so. Press **New page**, choose a blank page or a
template, and type what you want in the chat.

| Command | What it does |
|---|---|
| `studio start` | Start in the background and open the browser. `--foreground`, `--no-open`, `--port`, `--data` |
| `studio stop` | Stop the studio that `studio start` started. A request that is still running is ended |
| `studio status` | Whether it runs, and where |
| `studio doctor` | What is installed, which model accounts work and why not, ports, data folder |
| `studio setup` | The three questions. `--show` prints the current setup; `--dry-run` writes nothing |
| `studio litellm install` | Install the LiteLLM the studio runs for other providers' keys |
| `studio access-key` | Print the key other devices sign in with |
| `studio login` | Sign this computer in to your Claude plan |

When something does not work, start with `studio doctor`.
