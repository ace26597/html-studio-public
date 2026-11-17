"""Whether the agent can look at pages (CONTRACT.md, "Look"): checked again after a "cannot"."""

import sys
from pathlib import Path

from studio2_server import look
from studio2_server.look import Looker


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_a_missing_browser_is_noticed_once_it_is_there(monkeypatch, tmp_path):
    clock, calls = Clock(), []
    monkeypatch.setattr(look.time, "monotonic", clock)
    browser: list[Path | None] = [None]

    def find():
        calls.append(1)
        return browser[0]

    monkeypatch.setattr(look, "find_browser", find)
    looker = Looker()
    said = looker.reason()
    assert said and "No Chromium" in said and "playwright install chromium" in said
    assert "PLAYWRIGHT_BROWSERS_PATH" in said
    browser[0] = tmp_path / "chrome"                      # someone installs one
    assert looker.reason() == said and len(calls) == 1    # not asked again within a few seconds
    clock.now += look.RECHECK + 0.1
    assert looker.reason() is None and len(calls) == 2    # asked again after them
    browser[0] = None
    clock.now += look.RECHECK * 10
    assert looker.reason() is None and len(calls) == 2    # a "can work" is remembered


def test_a_missing_package_says_how_to_install_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)   # makes the import fail
    said = Looker().reason()
    assert said and "playwright==1.62.0" in said and "uv pip install" in said and "[look]" in said
    assert "restart" in said
