"""Tests for a project's app: `run_tests` (CONTRACT.md, the section of that name). Small and fast:
the commands are tiny shell lines, and real pytest runs from the studio's own environment."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from studio2_server import testrun, tools


async def call(studio, pid, **args):
    result = await tools.call(studio, pid, "run_tests", args)
    text = result["content"][0]["text"]
    return result.get("is_error", False), (text if result.get("is_error") else json.loads(text))


@pytest.fixture
def workspace(studio, project):
    return studio.store.workspace(project["id"])


def declare(workspace: Path, test: str) -> None:
    (workspace / "studio.json").write_text(json.dumps({"test": test}))


def real_pytest(workspace: Path) -> None:
    """The project's own .venv/bin/pytest, pointing at the studio's: real pytest, no download."""
    (workspace / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
    (workspace / ".venv" / "bin" / "pytest").symlink_to(Path(sys.executable).with_name("pytest"))


# -- which command ----------------------------------------------------------------------------
async def test_a_project_without_tests_is_told_what_to_create(studio, project):
    error, answer = await call(studio, project["id"])
    assert not error and answer["command"] is None and answer["found"] is False
    assert "tests/test_app.py" in answer["next"] and "app-tests" in answer["next"]
    assert "tests_last" not in studio.store.get(project["id"])


def test_the_command_is_chosen_in_order(workspace, project):
    assert testrun.find_command(workspace, project)["command"] is None
    (workspace / "package.json").write_text(json.dumps({"scripts": {"test": "echo \"Error: no test specified\" && exit 1"}}))
    assert testrun.find_command(workspace, project)["command"] is None          # npm's placeholder is not a test
    (workspace / "package.json").write_text(json.dumps({"scripts": {"test": "vitest run"}}))
    found = testrun.find_command(workspace, project)
    assert found["command"] is None and "npm install" in found["next"]          # never installs by itself
    (workspace / "node_modules").mkdir()
    assert testrun.find_command(workspace, project)["command"] == "npm test"
    (workspace / "test_logic.py").write_text("def test_x():\n    assert True\n")
    assert testrun.find_command(workspace, project)["command"] == (
        "uv run --with pytest --with httpx pytest -q -p no:cacheprovider")
    (workspace / "requirements.txt").write_text("fastapi\n")
    assert "--with-requirements requirements.txt" in testrun.find_command(workspace, project)["command"]
    (workspace / "requirements.lock").write_text("fastapi==1\n")
    assert "--with-requirements requirements.lock" in testrun.find_command(workspace, project)["command"]
    real_pytest(workspace)
    assert testrun.find_command(workspace, project)["command"] == ".venv/bin/pytest -q -p no:cacheprovider"
    declare(workspace, "sh tests.sh")
    assert testrun.find_command(workspace, project) == {"command": "sh tests.sh", "kind": "studio_json"}


def test_an_attached_app_uses_its_confirmed_recipe(workspace, project):
    attached = {**project, "attach": {"mode": "copy", "path": "/somewhere/app"},
                "ship": {"recipe": {"checks": ["npm run lint", "npm test"]}}}
    assert testrun.find_command(workspace, attached) == {"command": "npm run lint && npm test", "kind": "recipe"}
    in_place = {**attached, "attach": {"mode": "in_place", "path": str(workspace)}}
    declare(workspace, "echo from-studio-json")          # never read in place: no studio.json is written there
    assert testrun.find_command(workspace, in_place)["kind"] == "recipe"
    no_recipe = {**project, "attach": {"mode": "copy", "path": "/somewhere/app"}}
    assert testrun.find_command(workspace, no_recipe)["kind"] == "studio_json"


async def test_path_and_filter_narrow_the_run(studio, project, workspace):
    (workspace / "tests").mkdir()
    (workspace / "tests" / "test_a.py").write_text("def test_one():\n    assert True\n\ndef test_two():\n    assert True\n")
    real_pytest(workspace)
    error, answer = await call(studio, project["id"], path="tests/test_a.py", filter="one")
    assert not error and answer["command"].endswith("tests/test_a.py -k one")
    assert answer["counts"] == {"passed": 1, "failed": 0, "errors": 0, "skipped": 1}
    assert (await call(studio, project["id"], path="../elsewhere"))[0] is True
    assert (await call(studio, project["id"], path="tests/missing.py"))[0] is True
    declare(workspace, "true")
    error, text = await call(studio, project["id"], filter="one")
    assert error and "pass path" in text.lower()


# -- a real run -------------------------------------------------------------------------------
async def test_a_failing_run_is_reported_and_kept(studio, project, workspace):
    pid = project["id"]
    (workspace / "tests").mkdir()
    (workspace / "app.py").write_text("def add(a, b):\n    return a - b\n")
    (workspace / "tests" / "test_app.py").write_text(
        "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parents[1]))\nimport app\n\n"
        "def test_add():\n    assert app.add(2, 2) == 4, 'add is wrong'\n\ndef test_zero():\n    assert app.add(0, 0) == 0\n")
    real_pytest(workspace)
    before = sorted(p.relative_to(workspace).as_posix() for p in workspace.rglob("*") if ".git" not in p.parts)
    error, answer = await call(studio, pid)
    assert not error, answer
    assert answer["passed"] is False and answer["exit_code"] == 1 and answer["timed_out"] is False
    assert answer["counts"] == {"passed": 1, "failed": 1, "errors": 0, "skipped": 0}
    assert answer["failures"][0]["name"] == "tests/test_app.py::test_add" and "add is wrong" in answer["failures"][0]["message"]
    assert "Never delete" in answer["next"] and "1 failed" in answer["output_tail"]
    after = sorted(p.relative_to(workspace).as_posix() for p in workspace.rglob("*") if ".git" not in p.parts)
    assert after == before            # no .pytest_cache, no __pycache__: nothing written into the project
    kept = studio.store.get(pid)["tests_last"]
    assert kept["passed"] is False and kept["counts"]["failed"] == 1 and kept["command"] == answer["command"]
    _standing, current = studio.project_facts(pid)
    assert "- Tests (`.venv/bin/pytest -q -p no:cacheprovider`): 1 passed, 1 failed, just now." in current
    (workspace / "app.py").write_text("def add(a, b):\n    return a + b\n")
    os.utime(workspace / "app.py", ns=(kept["fingerprint"][1] + 10**9, kept["fingerprint"][1] + 10**9))
    _standing, current = studio.project_facts(pid)
    assert "the app's files changed since." in current
    error, answer = await call(studio, pid)
    assert answer["passed"] is True and answer["counts"]["passed"] == 2 and "All tests pass" in answer["next"]
    assert "changed since" not in studio.project_facts(pid)[1]


def test_the_facts_say_when_tests_were_never_run(studio, project, workspace):
    assert "Tests" not in studio.project_facts(project["id"])[1]
    (workspace / "tests").mkdir()
    (workspace / "tests" / "test_x.py").write_text("def test_x():\n    pass\n")
    assert "this project has tests" in studio.project_facts(project["id"])[1]


def test_the_facts_line_reads_the_record(workspace):
    project = {"tests_last": {"at": "2026-01-01T10:00:00+00:00", "command": "npm test", "passed": False,
                              "counts": None, "timed_out": True, "fingerprint": None}}
    from datetime import datetime
    now = datetime.fromisoformat("2026-01-01T10:08:30+00:00").timestamp()
    assert testrun.facts_line(project, workspace, now) == "- Tests (`npm test`): stopped at the time limit, 8 minutes ago."


async def test_the_time_limit_ends_the_whole_group(studio, project, workspace):
    declare(workspace, "sleep 41.75 & sleep 41.5; echo never")
    error, answer = await call(studio, project["id"], timeout=1)
    assert not error and answer["timed_out"] is True and answer["passed"] is False
    assert "1-second limit" in answer["next"] and answer["seconds"] < 8
    left = subprocess.run(["pgrep", "-f", "sleep 41.75"], capture_output=True, text=True).stdout.split()
    assert left == []
    assert studio.store.get(project["id"])["tests_last"]["timed_out"] is True


async def test_tests_get_the_apps_clean_environment_and_their_own_state(studio, project, workspace, monkeypatch):
    monkeypatch.setenv("STUDIO2_ACCESS_KEY", "never-shown")
    monkeypatch.setenv("SOME_API_KEY", "never-shown")
    declare(workspace, 'env; test -d "$STUDIO_TEST_STATE" && ls -A "$STUDIO_TEST_STATE" | wc -l | tr -d " " '
                       '&& echo "state=$STUDIO_TEST_STATE"')
    error, answer = await call(studio, project["id"])
    assert not error and answer["passed"] is True
    out = answer["output_tail"]
    assert "never-shown" not in out
    for absent in ("PORT=", "STUDIO_TOKEN=", "STUDIO_DATA_URL=", "STUDIO_MODEL_URL="):
        assert f"\n{absent}" not in "\n" + out, absent
    assert "\nCI=1" in "\n" + out and "PYTHONDONTWRITEBYTECODE=1" in out
    state = Path(out.split("state=")[1].split()[0])
    assert workspace.resolve() not in state.resolve().parents and not state.exists()   # outside, and removed after
    assert "\n0\n" in out                                                               # empty when the run starts


# -- the shell rules apply ---------------------------------------------------------------------
async def test_a_command_the_shell_would_ask_about_is_not_run(studio, project, workspace):
    port = studio.settings.port
    for command in ("touch ran.txt; cat ~/.ssh/id_rsa", f"touch ran.txt; curl -s http://127.0.0.1:{port}/api/projects",
                    "touch ran.txt; cat ../../other/events.jsonl", "touch ran.txt; pip install requests"):
        declare(workspace, command)
        error, text = await call(studio, project["id"])
        assert error and "did not run this test command" in text and "Bash" in text, command
        assert not (workspace / "ran.txt").exists(), command
    assert "tests_last" not in studio.store.get(project["id"])


async def test_an_in_place_app_says_where_its_tests_ran(studio, project, workspace):
    studio.store.update(project["id"], attach={"mode": "in_place", "path": str(workspace)},
                        ship={"recipe": {"checks": ["echo checked"]}})
    error, answer = await call(studio, project["id"])
    assert not error and answer["passed"] is True and "own folder" in answer["note"]


async def test_one_run_per_project_at_a_time(studio, project, workspace):
    import asyncio
    declare(workspace, "sleep 1")
    first = asyncio.create_task(call(studio, project["id"]))
    await asyncio.sleep(0.3)
    error, text = await call(studio, project["id"])
    assert error and "already running" in text
    assert (await first)[1]["passed"] is True


# -- reading the output -----------------------------------------------------------------------
def test_counts_are_read_from_each_runner():
    assert testrun.counts_of("....F\nFAILED t.py::a - x\n1 failed, 4 passed, 2 skipped in 0.31s\n") == \
        {"passed": 4, "failed": 1, "errors": 0, "skipped": 2}
    assert testrun.counts_of("===== 3 passed, 1 error in 1.20s =====") == {"passed": 3, "failed": 0, "errors": 1, "skipped": 0}
    assert testrun.counts_of(" Test Files  1 failed | 2 passed (3)\n      Tests  2 failed | 10 passed (12)\n") == \
        {"passed": 10, "failed": 2, "errors": 0, "skipped": 0}
    assert testrun.counts_of("Tests:       1 failed, 1 skipped, 3 passed, 5 total\n") == \
        {"passed": 3, "failed": 1, "errors": 0, "skipped": 1}
    assert testrun.counts_of("# tests 4\n# pass 3\n# fail 1\n") == {"passed": 3, "failed": 1, "errors": 0, "skipped": 0}
    assert testrun.counts_of("no tests ran in 0.01s") == {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    assert testrun.counts_of("hello") is None


def test_failures_are_named_and_capped():
    text = "\n".join(f"FAILED tests/test_a.py::test_{n} - AssertionError: {'x' * 500}" for n in range(30))
    found = testrun.failures_of(text)
    assert len(found) == 20 and found[0]["name"] == "tests/test_a.py::test_0" and len(found[0]["message"]) == 300
    js = testrun.failures_of(" FAIL  tests/a.test.ts > totals > adds up\nAssertionError: expected 3 to be 4\n")
    assert js == [{"name": "tests/a.test.ts > totals > adds up", "message": "AssertionError: expected 3 to be 4"}]


def test_the_fingerprint_counts_code_and_skips_data_and_state(workspace):
    (workspace / "server.py").write_text("x = 1\n")
    (workspace / "state").mkdir()
    (workspace / "state" / "db.json").write_text("{}")
    (workspace / "data").mkdir(exist_ok=True)
    (workspace / "data" / "big.json").write_text("[]")
    first = testrun.fingerprint(workspace)
    (workspace / "state" / "db.json").write_text('{"a": 1}')
    (workspace / "data" / "big.json").write_text("[1]")
    assert testrun.fingerprint(workspace) == first
    (workspace / "helper.py").write_text("y = 2\n")
    assert testrun.fingerprint(workspace)[0] == first[0] + 1


# -- the templates with a back end ship a passing test ------------------------------------------
@pytest.mark.parametrize("template", ["fastapi-app", "whiteboard"])
async def test_each_template_with_a_back_end_ships_a_passing_test(studio, project, workspace, template):
    import shutil
    files = Path(__file__).resolve().parents[1] / "library" / "templates" / template / "files"
    assert (files / "studio.json").is_file() and list((files / "tests").glob("test_*.py"))
    shutil.copytree(files, workspace, dirs_exist_ok=True)
    real_pytest(workspace)
    board = (workspace / "board.excalidraw").read_bytes() if template == "whiteboard" else None
    error, answer = await call(studio, project["id"])
    assert not error and answer["passed"] is True, answer.get("output_tail") if isinstance(answer, dict) else answer
    assert answer["counts"]["passed"] >= 2 and answer["counts"]["failed"] == 0
    if board is not None:
        assert (workspace / "board.excalidraw").read_bytes() == board        # the drawing is not touched


def test_every_template_with_a_back_end_has_tests():
    root = Path(__file__).resolve().parents[1] / "library" / "templates"
    for folder in root.iterdir():
        config = folder / "files" / "studio.json"
        if config.is_file() and "uv run" in config.read_text():
            assert list((folder / "files" / "tests").glob("test_*.py")), folder.name
