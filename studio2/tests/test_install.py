"""install.sh: which Node it accepts, and the package lists it installs the server from.

The Node check is run through bash exactly as the installer has it (the block between its two
"node versions" markers), so the shell and `studio doctor` are held to the same table.
"""

from __future__ import annotations

import re
import subprocess
import tomllib

import pytest
from packaging.requirements import Requirement

from studio2_server import doctor
from studio2_server.config import STUDIO2_ROOT

INSTALL = STUDIO2_ROOT / "install.sh"

# The interface's build tool (Vite and its Rolldown, web/package-lock.json) declares
# "^20.19.0 || >=22.12.0".
NODES = [
    ("18.20.4", False), ("20.0.0", False), ("20.18.3", False), ("20.19.0", True), ("20.19.5", True),
    ("20.100.0", True), ("21.7.3", False), ("22.0.0", False), ("22.11.9", False), ("22.12.0", True),
    ("22.20.1", True), ("23.0.0", True), ("24.22.2", True), ("25.5.0", True), ("100.0.0", True),
    ("", False), ("v", False), ("22", False), ("22.12", False), ("junk", False),
]


def shell_block() -> str:
    text = INSTALL.read_text()
    match = re.search(r"^# -- node versions.*?^# -- end node versions --$", text, re.S | re.M)
    assert match, "install.sh lost its node versions markers"
    return match.group(0)


def test_install_script_node_check_matches_the_build_range():
    block = shell_block()
    cases = "\n".join(f"if node_accepted '{version}'; then echo '{version}=1'; else echo '{version}=0'; fi"
                      for version, _ in NODES)
    done = subprocess.run(["bash", "-c", f"set -u\n{block}\n{cases}"], capture_output=True, text=True, timeout=10)
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
    got = dict(line.rsplit("=", 1) for line in done.stdout.splitlines())
    assert got == {version: "1" if ok else "0" for version, ok in NODES}


@pytest.mark.parametrize(("version", "ok"), NODES)
def test_doctor_node_check_matches_the_build_range(version, ok):
    assert doctor.node_accepted(f"v{version}") is ok


def test_vite_still_declares_the_range_the_checks_use():
    lock = (STUDIO2_ROOT / "web" / "package-lock.json").read_text()
    entry = re.search(r'"node_modules/vite": \{.*?"node": "([^"]+)"', lock, re.S)
    assert entry and entry.group(1) == "^20.19.0 || >=22.12.0", "update install.sh, doctor.py and NODES together"


def pins(path):
    names = {}
    for line in path.read_text().splitlines():
        match = re.match(r"([A-Za-z0-9._-]+)==([^ ;\\]+)", line)
        if match:
            names[re.sub(r"[-_.]+", "-", match.group(1)).lower()] = match.group(2)
    return names


@pytest.mark.parametrize(("lock", "extras"), [("server-requirements.txt", ["look"]),
                                              ("server-dev-requirements.txt", ["look", "dev"])])
def test_server_lock_covers_every_dependency_with_hashes(lock, extras):
    project = tomllib.loads((STUDIO2_ROOT / "pyproject.toml").read_text())["project"]
    wanted = list(project["dependencies"])
    for extra in extras:
        wanted += project["optional-dependencies"][extra]
    locked = pins(STUDIO2_ROOT / lock)
    for line in wanted:
        requirement = Requirement(line)
        name = re.sub(r"[-_.]+", "-", requirement.name).lower()
        assert name in locked, f"{line} is in pyproject.toml but not in {lock}: run tools/lock-server.sh"
        assert requirement.specifier.contains(locked[name], prereleases=True), f"{lock} has {name}=={locked[name]}, outside {line}"
    text = (STUDIO2_ROOT / lock).read_text()
    for block in re.split(r"\n(?=[A-Za-z0-9])", text):
        if "==" in block:
            assert "--hash=sha256:" in block, f"no hash in {lock}: {block.splitlines()[0]}"
