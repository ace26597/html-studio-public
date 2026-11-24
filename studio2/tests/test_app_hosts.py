"""A project's own back end answers only to the names the studio gives it (DNS rebinding).

CONTRACT.md: "`studio.json` — a project with its own back end" (the app answers only to the names in
STUDIO_ALLOWED_HOSTS) and "Names the studio answers to". Zero model calls.
"""

import asyncio
import json
import os
import re
import shutil
import socket
import sys
from pathlib import Path

import httpx

from studio2_server import apps as apps_module

APP_ALLOWED_HOSTS = getattr(apps_module, "APP_ALLOWED_HOSTS", "127.0.0.1,localhost")

TEMPLATES = Path(__file__).resolve().parents[1] / "library" / "templates"


async def until(check, seconds=15.0):
    deadline = asyncio.get_running_loop().time() + seconds
    while not check():
        assert asyncio.get_running_loop().time() < deadline, "timed out waiting"
        await asyncio.sleep(0.05)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def answering(port: int) -> None:
    async with httpx.AsyncClient() as http:
        for _ in range(300):
            try:
                await http.get(f"http://127.0.0.1:{port}/")
                return
            except httpx.TransportError:
                await asyncio.sleep(0.05)
    raise AssertionError("the app never answered")


async def get(port: int, path: str, host: str) -> httpx.Response:
    async with httpx.AsyncClient() as http:
        return await http.get(f"http://127.0.0.1:{port}{path}", headers={"host": host})


async def test_an_app_is_given_the_names_it_may_answer_to(client, studio, project):
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    (workspace / "show.py").write_text(
        "import os, http.server, socketserver\n"
        "print('ALLOWED=' + repr(os.environ.get('STUDIO_ALLOWED_HOSTS')), flush=True)\n"
        "socketserver.TCPServer.allow_reuse_address = True\n"
        "socketserver.TCPServer(('127.0.0.1', int(os.environ['PORT'])), http.server.SimpleHTTPRequestHandler).serve_forever()\n")
    (workspace / "studio.json").write_text(json.dumps({"run": f"{sys.executable} show.py"}))
    await studio.sync_app(pid)
    await until(lambda: studio.app(pid).state == "running")
    text = "\n".join(line["text"] for line in studio.app(pid).logs()["lines"])
    assert getattr(apps_module, "APP_ALLOWED_HOSTS", None) == "127.0.0.1,localhost"
    assert f"ALLOWED='{APP_ALLOWED_HOSTS}'" in text


async def run_template(tmp_path, template: str, argv, allowed: str | None):
    folder = tmp_path / f"{template}-{allowed is not None}"
    shutil.copytree(TEMPLATES / template / "files", folder)
    port = free_port()
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "TMPDIR", "LANG")}
    env.update({"PORT": str(port), "PYTHONUNBUFFERED": "1"})
    if allowed is not None:
        env["STUDIO_ALLOWED_HOSTS"] = allowed
    process = await asyncio.create_subprocess_exec(*[a.replace("$PORT", str(port)) for a in argv], cwd=folder,
                                                   env=env, stdout=asyncio.subprocess.DEVNULL,
                                                   stderr=asyncio.subprocess.DEVNULL)
    return process, port


async def test_the_templates_back_ends_refuse_a_name_they_were_not_given(tmp_path):
    """A rebinding page sends `Host: evil.example:<port>`; the studio sends `Host: 127.0.0.1:<port>`."""
    cases = [("fastapi-app", [sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", "$PORT"],
              "/api/health"),
             ("whiteboard", [sys.executable, "server.py"], "/api/board/stamp")]
    for template, argv, path in cases:
        for allowed in (APP_ALLOWED_HOSTS, None):
            process, port = await run_template(tmp_path, template, argv, allowed)
            try:
                await answering(port)
                ours = await get(port, path, f"127.0.0.1:{port}")
                assert ours.status_code == 200, (template, ours.status_code)
                assert (await get(port, path, f"localhost:{port}")).status_code == 200
                evil = await get(port, path, f"evil.example:{port}")
                if allowed is None:    # outside the studio (an export, a ship) the code is not limited
                    assert evil.status_code == 200, template
                else:
                    assert evil.status_code in (400, 421), (template, evil.status_code)
                    assert "ok" not in evil.text and "stamp" not in evil.text
                    page = await get(port, "/", f"evil.example:{port}")
                    assert page.status_code in (400, 421) and "<html" not in page.text.lower()
            finally:
                process.terminate()
                await process.wait()


def test_the_vite_template_leaves_vites_own_name_check_on():
    config = (TEMPLATES / "vite-app" / "files" / "vite.config.js").read_text()
    assert not re.search(r"allowedHosts\s*:", config)      # true would answer any name
    wanted = json.loads((TEMPLATES / "vite-app" / "files" / "package.json").read_text())["devDependencies"]["vite"]
    major, minor, patch = (int(n) for n in re.match(r"\^(\d+)\.(\d+)\.(\d+)", wanted).groups())
    assert (major, minor, patch) >= (6, 0, 9)      # the first 6.x that checks names by default


async def test_the_agent_is_told_to_answer_only_to_the_names_it_is_given(client, studio, project):
    # A rule that is the same for every project: said once, in the system prompt (one bullet).
    told = (studio.settings.library_dir / "system-prompt.md").read_text()
    line = next(" ".join(bullet.split()) for bullet in told.split("\n- ") if "STUDIO_ALLOWED_HOSTS" in bullet)
    assert "Host" in line and "TrustedHostMiddleware" in line and "allowedHosts" in line
    # Host carries the port (127.0.0.1:$PORT): a check that compares it whole refuses the studio itself.
    assert "without its port" in line
    assert "req.hostname" in line and "request.host.rsplit(':', 1)[0]" in line
