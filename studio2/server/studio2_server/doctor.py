"""`studio doctor`: what is installed, which model accounts work and why not, whether the
assistant can look at pages, the ports, the data folder and the version. It reads; it changes
nothing, starts no model call and never downloads anything."""

from __future__ import annotations

import os
import platform
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__, setup
from .config import REPO_ROOT, STUDIO2_ROOT, Settings, load_connections, range_for
from .picker import picking_model
from .store import port_is_free

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"
MARK = {OK: "ok  ", WARN: "warn", FAIL: "FAIL", INFO: "    "}
# The Node releases the interface's build tool (Vite, locked in web/package-lock.json) runs on;
# install.sh checks the same range.
NODE_RANGE = "20.19 or later in 20.x, or 22.12 or later"
PRIVATE_NODE = STUDIO2_ROOT / ".tools" / "node" / "bin" / "node"


def system_name() -> str:
    if sys.platform == "darwin":
        return f"macOS {platform.mac_ver()[0]}"
    name = platform.system()
    try:
        fields = platform.freedesktop_os_release()
        name = fields.get("PRETTY_NAME") or name
    except (OSError, AttributeError):
        pass
    return name


def package_hint(mac: str, debian: str, rpm: str) -> str:
    """The command that installs an optional program on this kind of machine."""
    if sys.platform == "darwin":
        return mac
    if shutil.which("apt-get"):
        return debian
    if shutil.which("dnf") or shutil.which("yum"):
        return rpm
    return f"{debian}  (Debian/Ubuntu), or  {rpm}  (Fedora/RHEL)"


def _version(command: list[str]) -> str | None:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (done.stdout or done.stderr).strip().splitlines()
    return text[0].strip() if done.returncode == 0 and text else None


def commit() -> str | None:
    if not (REPO_ROOT / ".git").exists():
        return None
    return _version(["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"])


def node_accepted(version: str | None) -> bool:
    """Whether a `node --version` answer (v22.11.0) is one the interface's build runs on (NODE_RANGE)."""
    match = re.match(r"v?(\d+)\.(\d+)\.(\d+)", version or "")
    if not match:
        return False
    release = tuple(int(part) for part in match.groups())
    return release >= (20, 19, 0) if release[0] == 20 else release >= (22, 12, 0)


def node() -> tuple[str | None, str | None]:
    """→ (path, version) of the Node the studio will use: the one on PATH when the build accepts it,
    otherwise the private copy the installer put under the studio's own folder."""
    for candidate in (shutil.which("node"), str(PRIVATE_NODE) if PRIVATE_NODE.is_file() else None):
        if not candidate:
            continue
        version = _version([candidate, "--version"])
        if node_accepted(version):
            return candidate, version
    stale = shutil.which("node")
    return (stale, _version([stale, "--version"])) if stale else (None, None)


def _item(area: str, status: str, name: str, detail: str = "", fix: str | None = None) -> dict:
    return {"area": area, "status": status, "name": name, "detail": detail, "fix": fix}


def _online(connection: dict, settings: Settings) -> tuple[str, str] | None:
    """One request that costs nothing (the provider's list of models) to see whether the key is
    accepted. Only for an Anthropic key and a gateway; the clouds have no such call here."""
    import httpx
    from .config import connection_key
    key = connection_key(connection, os.environ, settings.secret_store())
    if not key:
        return None
    if connection["kind"] == "anthropic-api":
        url = (os.environ.get("STUDIO2_ANTHROPIC_URL") or "https://api.anthropic.com").rstrip("/") + "/v1/models"
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    elif connection["kind"] == "anthropic-gateway":
        url = str(connection.get("base_url") or "").rstrip("/") + "/v1/models"
        headers = {"authorization": f"Bearer {key}", "x-api-key": key}
    elif connection["kind"] == "litellm":
        import asyncio
        from . import gateway
        try:
            listed = asyncio.run(gateway.list_models(str(connection.get("provider")), key, connection.get("base_url")))
        except gateway.GatewayError as exc:
            refused = "refused the key" in str(exc)
            return (FAIL if refused else WARN), str(exc)
        known = {m["id"] for m in listed}
        missing = [m["id"] for m in connection["models"] if m["id"] not in known]
        if missing:
            return WARN, f"the key was accepted just now, but the provider does not list: {', '.join(missing)}"
        return OK, "the key was accepted just now and the provider lists every model offered"
    else:
        return None
    try:
        response = httpx.get(url, headers=headers, timeout=httpx.Timeout(15.0, connect=5.0), trust_env=False)
    except httpx.HTTPError as exc:
        return WARN, f"could not be reached ({exc.__class__.__name__})"
    if response.status_code < 400:
        return OK, "the key was accepted just now"
    if response.status_code in (401, 403):
        return FAIL, f"the key was refused (HTTP {response.status_code})"
    return WARN, f"answered HTTP {response.status_code} to a request for its models"


def collect(settings: Settings, *, online: bool = False, state: dict | None = None,
            login=setup.detect_claude_login, tailscale=setup.detect_tailscale) -> dict:
    """→ {summary, items:[{area, status, name, detail, fix}]}. `state` is what `studio start`
    recorded (pid, port) when a studio it started is running."""
    items: list[dict] = []
    add = lambda *args, **kwargs: items.append(_item(*args, **kwargs))  # noqa: E731
    python = sys.version.split()[0]

    # -- what is installed ------------------------------------------------------------------
    area = "Installed"
    add(area, OK if sys.version_info[:2] >= (3, 12) else FAIL, f"Python {python}", sys.executable)
    uv = shutil.which("uv") or next((str(p) for p in (STUDIO2_ROOT / ".tools/uv/uv", Path.home() / ".local/bin/uv")
                                     if p.is_file()), None)
    add(area, OK if uv else INFO, "uv" + (f" ({_version([uv, '--version']) or 'version unknown'})" if uv else ""),
        uv or "not found (only the installer needs it)")
    node_path, node_version = node()
    node_ok = node_accepted(node_version)
    add(area, OK if node_ok else WARN, f"Node {node_version or 'not found'}",
        node_path or "needed to build the interface and to run JavaScript apps",
        None if node_ok else f"Run {STUDIO2_ROOT / 'install.sh'} (it fetches a private copy of a Node the build runs on: {NODE_RANGE}).")
    git = shutil.which("git")
    add(area, OK if git else FAIL, _version(["git", "--version"]) if git else "git not found", git or "",
        None if git else package_hint("xcode-select --install", "sudo apt-get install -y git", "sudo dnf install -y git"))
    built = (settings.web_dist / "index.html").is_file()
    add(area, OK if built else FAIL, "The interface is built" if built else "The interface is not built",
        str(settings.web_dist), None if built else f"Run {STUDIO2_ROOT / 'install.sh'}, or: cd {STUDIO2_ROOT / 'web'} && npm ci && npm run build")
    command = shutil.which("studio")
    add(area, OK if command else WARN, "The `studio` command" + ("" if command else " is not on your PATH"),
        command or "", None if command else "Add ~/.local/bin to PATH, or run install.sh again.")
    from .harness.claude import find_cli
    cli = find_cli()
    add(area, OK if cli else FAIL, "The agent program (Claude Code)" + ("" if cli else " was not found"),
        (f"{_version([cli, '--version']) or ''}  {cli}").strip() if cli else "",
        None if cli else "Run install.sh again: it comes with the studio's Python packages.")

    # -- model accounts ---------------------------------------------------------------------
    area = "Model accounts"
    settings.secret_store()                 # opened first, so the copy below shares it (never a second store)
    real = replace(settings, fake=False)    # the accounts as they are configured, scripted agent or not
    if settings.fake:
        add(area, INFO, "The scripted agent is in use (STUDIO2_FAKE=1)", "no model is called while it is on")
    from .harness import Registry
    registry = Registry(real)
    usable = 0
    for connection, public in zip(load_connections(real), registry.describe_all()):
        models = ", ".join(m["id"] for m in public["models"])
        name = f"{public['label']} ({public['kind']}; models: {models})"
        if not public["available"]:
            add(area, FAIL, name, public["reason"] or "not available")
            continue
        status, detail, fix = OK, "", None
        if public["kind"] == "claude-subscription":
            found = login()
            detail = found["detail"]
            if not found["found"]:
                status, fix = FAIL, "Sign in with `studio login`, or choose a key in `studio setup`."
        elif connection.get("api_key_env"):
            detail = f"key from the environment variable {connection['api_key_env']}"
        elif connection.get("api_key_secret"):
            detail = f"key kept in {real.secret_store().label}"
        else:
            detail = "uses the cloud sign-in the studio is started with"
        if online and status == OK:
            checked = _online(connection, real)
            if checked:
                status, detail = checked[0], f"{detail}; {checked[1]}"
        elif status == OK and public["kind"] != "claude-subscription":
            detail += " (not tried; `studio doctor --online` asks the provider for its model list, which is free)"
        usable += status == OK
        add(area, status, name, detail, fix)
        picks = picking_model(connection)
        if picks:
            add(area, OK, f"{public['label']}: skills are picked by {picks}", "a model of this account; nothing goes elsewhere")
        else:
            add(area, WARN, f"{public['label']}: no cheap model for picking skills", "the word search decides instead",
                "Add `small_model` to this account (`studio setup`), naming a cheap model it offers.")
    if not usable:
        add(area, FAIL, "No model account works", "", "Run `studio setup`.")
    if real.shared:
        add(area, INFO, "This studio is shared with other people", "the Claude plan is switched off")

    # -- the LiteLLM the studio runs for OpenAI, OpenRouter, Azure OpenAI and Gemini keys -------------
    area = "Other models (the studio's own LiteLLM)"
    from .cli import litellm_report
    lite = litellm_report(settings)
    users = ", ".join(lite["needed_by"])
    if lite["installed"]:
        add(area, OK, f"LiteLLM {lite['version']} is installed", f"{lite['home']}; every package was checked "
            f"against the hashes in {lite['requirements']}")
    elif lite["needed_by"]:
        add(area, FAIL, "LiteLLM is not installed", f"needed by the model account{'s' if ',' in users else ''} {users}",
            "Run `studio litellm install`.")
    else:
        add(area, INFO, "LiteLLM is not installed", "only needed for an OpenAI, OpenRouter, Azure OpenAI or Gemini "
            "key; `studio litellm install` installs it")
    if lite["running"]:
        add(area, OK, f"Running on this computer only: 127.0.0.1:{lite['port']}", f"pid {lite['pid']}; log {lite['log']}")
    elif lite["left_behind"]:
        add(area, WARN, f"A LiteLLM this studio started (pid {lite['pid']}) no longer answers",
            f"the next request starts a new one; log {lite['log']}")
    elif lite["installed"]:
        add(area, INFO, "Not running", f"it starts when a request needs it and stops with the studio"
            + (f"; used by {users}" if users else "; no model account needs it yet"))
    add(area, INFO, "Claude models work best with the studio; other models are experimental",
        "the Library's “Check this model” runs a fixed request on a model and keeps the result")

    # -- looking at pages, and the optional extras ------------------------------------------
    area = "Looking at pages"
    from .look import PLAYWRIGHT_PIN, browser_folders, find_browser
    browser = find_browser()
    first, second = browser_folders()
    # Only one folder is searched: the first when it exists, else the second. Say which.
    folders = f"{first if first.is_dir() else second}; {first} is used when it exists, else {second}"
    try:
        import playwright  # noqa: F401
        driver = True
    except ImportError:
        driver = False
    if browser and driver:
        add(area, OK, "A browser the assistant can look with", str(browser))
    if not driver:
        add(area, WARN, f"The optional `look` extra ({PLAYWRIGHT_PIN}) is not installed",
            "the assistant cannot look at pages or render video; a running studio finds it within a few "
            "seconds of installing, or after a restart",
            f"cd {STUDIO2_ROOT} && uv pip install --python {sys.executable} --require-hashes -r server-requirements.txt")
    if not browser:
        add(area, WARN, "No browser for looking at pages",
            f"the assistant cannot take screenshots, check its pages or render video; looked in {folders} "
            "(PLAYWRIGHT_BROWSERS_PATH names another first folder)",
            f"{sys.executable} -m playwright install chromium   (downloads about 170 MB; the studio never does "
            "this by itself), or set PLAYWRIGHT_BROWSERS_PATH to a cache that already has one")
    area = "Optional"
    from . import generate, media, office, voice
    ffmpeg = media.find_ffmpeg()
    add(area, OK if ffmpeg else INFO, "ffmpeg (video files)" if ffmpeg else "ffmpeg is not installed (no video files)",
        ffmpeg or "", None if ffmpeg else package_hint(
            "brew install ffmpeg", "sudo apt-get install -y ffmpeg", "sudo dnf install -y ffmpeg-free"))
    soffice = office.find_soffice()
    add(area, OK if soffice else INFO,
        "LibreOffice (PowerPoint, Word and Excel files)" if soffice else "LibreOffice is not installed (no Office files)",
        str(soffice or ""), None if soffice else package_hint(
            "brew install --cask libreoffice", "sudo apt-get install -y libreoffice", "sudo dnf install -y libreoffice"))
    transcriber = voice.command()
    add(area, OK if transcriber else INFO, "Voice input" if transcriber else "Voice input is not set up",
        str(transcriber or ""), None if transcriber else
        "Set STUDIO2_TRANSCRIBE to a command that takes an audio file and prints its text.")
    higgsfield = generate.find_cli()
    add(area, OK if higgsfield else INFO,
        "Generated pictures and clips (Higgsfield)" if higgsfield else "No generation service (Higgsfield) is set up",
        higgsfield or "")

    # -- where it runs ----------------------------------------------------------------------
    area = "Where it runs"
    port = int((state or {}).get("port") or settings.port)
    low, high = settings.port_range if not state else range_for(port)
    if state:
        add(area, OK, f"Running at http://127.0.0.1:{port}/", f"pid {state.get('pid')}, started by `studio start`")
    elif port_is_free(port):
        add(area, OK, f"Port {port} is free", f"project pages use {low}–{high}, their own back ends {low + 100}–{high + 100}")
    else:
        from .cli import studio_at
        answer = studio_at(port)
        add(area, OK if answer else WARN,
            f"A studio answers on port {port}" if answer else f"Port {port} is used by another program",
            "it was not started with `studio start`" if answer else "`studio start` will take the next free one")
    hosts = ", ".join(settings.hosts)
    if settings.hosts:
        how = ("no key for devices on the tailnet" if settings.open_tailnet else
               "access key set" if settings.access_key else "NO ACCESS KEY: it will not start")
        add(area, OK if settings.open_tailnet or settings.access_key else FAIL, f"Also listens on {hosts}", how,
            None if settings.open_tailnet or settings.access_key else "Run `studio setup`.")
    else:
        add(area, OK, "This computer only", "nothing else can reach it")
    if settings.shared and not settings.access_key:
        add(area, FAIL, "Shared, but there is no access key", "", "Run `studio setup`.")
    if settings.tls:
        add(area, OK if all(Path(p).is_file() for p in settings.tls) else FAIL, "HTTPS certificate", settings.tls[0])
    found = tailscale()
    add(area, INFO, "Tailscale", found["detail"])

    # -- data and settings ------------------------------------------------------------------
    area = "Data and settings"
    data = settings.data_dir
    parent = next((p for p in (data, *data.parents) if p.exists()), None)
    writable = bool(parent and os.access(parent, os.W_OK))
    pages = sum(1 for p in settings.projects_dir.iterdir() if p.name.startswith("p_")) \
        if settings.projects_dir.is_dir() else 0
    add(area, OK if writable else FAIL, f"Data folder: {data}",
        f"{pages} page{'s' if pages != 1 else ''}" if data.is_dir() else "will be made on first start",
        None if writable else "Choose a folder you can write to: STUDIO2_DATA, or `studio start --data DIR`.")
    current = setup.configured(settings)
    add(area, OK if settings.config_path.is_file() else INFO, f"Config file: {settings.config_path}",
        ("set up" if current["done"] else "written by hand; `studio setup` can extend it")
        if settings.config_path.is_file() else "none yet: the defaults apply; `studio setup` writes it")
    store = settings.secret_store()
    secret_status, secret_detail = OK, ""
    if store.backend == "file":
        path = getattr(store, "path", None)
        if path and path.is_file() and stat.S_IMODE(path.stat().st_mode) & 0o077:
            secret_status, secret_detail = FAIL, "other users of this machine can read it; run: chmod 600 " + str(path)
    add(area, secret_status, f"Keys are kept in {store.label}", secret_detail)

    worst = FAIL if any(i["status"] == FAIL for i in items) else WARN if any(i["status"] == WARN for i in items) else OK
    return {
        "version": __version__, "commit": commit(), "system": f"{system_name()} {platform.machine()}",
        "python": python, "studio": str(STUDIO2_ROOT), "status": worst, "items": items,
    }


def render(report: dict) -> str:
    head = f"Studio {report['version']}"
    if report.get("commit"):
        head += f" ({report['commit']})"
    lines = [f"{head} · {report['system']} · Python {report['python']}", f"Installed in {report['studio']}"]
    area = None
    for item in report["items"]:
        if item["area"] != area:
            area = item["area"]
            lines += ["", area]
        line = f"  {MARK[item['status']]}  {item['name']}"
        if item["detail"]:
            line += f"\n          {item['detail']}"
        if item["fix"] and item["status"] != OK:
            line += f"\n          → {item['fix']}"
        lines.append(line)
    verdict = {OK: "Everything the studio needs is in place.",
               WARN: "The studio can run; the warnings above say what is missing.",
               FAIL: "Something the studio needs is missing: see FAIL above."}[report["status"]]
    return "\n".join([*lines, "", verdict])
