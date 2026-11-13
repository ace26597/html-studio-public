"""Where the studio keeps secrets (CONTRACT.md, "Install and first run": "Where a key is kept").

Model keys typed in setup, the access key setup makes, database passwords and connector secrets
all go through one store. It is the operating system's keychain (through `keyring`) when there
is one, and a file only its owner can read (`~/.config/studio2/secrets.json`, mode 600) when
there is not: a Linux server without a desktop has no keychain. Nothing here is ever written to
`config.json`, returned by the API or logged.

`STUDIO2_SECRETS` forces a choice: `keychain`, `file` or `memory` (kept for the life of the
process; what the fake studio and the tests use, so they can never touch the real keychain).
"""

from __future__ import annotations

import json
import logging
import os
import stat
import tempfile
import threading
from pathlib import Path

log = logging.getLogger("studio2")

MODEL_KEYS = "studio2-model-key"     # name → the key of a connection (`api_key_secret`)
ACCESS = "studio2-access"            # name → the studio's access key (`access_key_secret`)
FILE_NAME = "secrets.json"


class SecretError(Exception):
    pass


class MemoryStore:
    backend = "memory"
    label = "this process only (nothing is saved)"

    def __init__(self):
        self.values: dict[tuple[str, str], str] = {}

    def get(self, service: str, name: str) -> str | None:
        return self.values.get((service, name))

    def set(self, service: str, name: str, value: str) -> None:
        self.values[(service, name)] = value

    def delete(self, service: str, name: str) -> None:
        self.values.pop((service, name), None)


class KeyringStore:
    """The operating system's keychain: Keychain on macOS, Secret Service or KWallet on Linux."""

    backend = "keychain"

    @property
    def label(self) -> str:
        return keychain_name() or "the system keychain"

    def get(self, service: str, name: str) -> str | None:
        import keyring
        return keyring.get_password(service, name)

    def set(self, service: str, name: str, value: str) -> None:
        import keyring
        keyring.set_password(service, name, value)

    def delete(self, service: str, name: str) -> None:
        import keyring
        import keyring.errors
        try:
            keyring.delete_password(service, name)
        except keyring.errors.PasswordDeleteError:
            pass


class FileStore:
    """`{service: {name: value}}` in one JSON file that only its owner can read."""

    backend = "file"

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    @property
    def label(self) -> str:
        return f"a file only you can read ({self.path})"

    def _read(self) -> dict:
        try:
            mode = stat.S_IMODE(self.path.stat().st_mode)
        except OSError:
            return {}
        if mode & 0o077:    # someone loosened it: put it back before trusting it again
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                log.warning("%s can be read by other users of this machine and could not be locked down.", self.path)
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict) -> None:
        folder = self.path.parent
        if not folder.is_dir():
            folder.mkdir(parents=True, mode=0o700)
        # Created with mode 600 from the first byte (mkstemp), then moved into place.
        handle, temporary = tempfile.mkstemp(dir=folder, prefix=".secrets-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=1, sort_keys=True)
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def get(self, service: str, name: str) -> str | None:
        with self._lock:
            value = (self._read().get(service) or {}).get(name)
        return value if isinstance(value, str) else None

    def set(self, service: str, name: str, value: str) -> None:
        with self._lock:
            data = self._read()
            data.setdefault(service, {})[name] = value
            self._write(data)

    def delete(self, service: str, name: str) -> None:
        with self._lock:
            data = self._read()
            if name in (data.get(service) or {}):
                del data[service][name]
                if not data[service]:
                    del data[service]
                self._write(data)


class FallbackStore:
    """The keychain, with the file behind it for the moments the keychain refuses (a locked or
    absent desktop session on Linux). A secret is read from wherever it was put."""

    def __init__(self, first, second: FileStore):
        self.first, self.second = first, second

    @property
    def backend(self) -> str:
        return self.first.backend

    @property
    def label(self) -> str:
        return self.first.label

    def get(self, service: str, name: str) -> str | None:
        try:
            value = self.first.get(service, name)
        except Exception as exc:   # keyring raises its own family of errors, and backends raise others
            log.warning("The system keychain could not be read (%s); looking in %s.", exc.__class__.__name__,
                        self.second.path)
            value = None
        return value if value is not None else self.second.get(service, name)

    def set(self, service: str, name: str, value: str) -> None:
        try:
            self.first.set(service, name, value)
        except Exception as exc:
            log.warning("The system keychain refused to save (%s); using %s instead.", exc.__class__.__name__,
                        self.second.path)
            self.second.set(service, name, value)
        else:
            self.second.delete(service, name)   # never leave an older copy behind in the file

    def delete(self, service: str, name: str) -> None:
        try:
            self.first.delete(service, name)
        except Exception as exc:
            log.warning("The system keychain could not delete an entry (%s).", exc.__class__.__name__)
        self.second.delete(service, name)


def keychain_name() -> str | None:
    """A plain name for the keychain `keyring` would use, or None when it has none to use."""
    try:
        import keyring
        from keyring.backends import fail
        backend = keyring.get_keyring()
    except Exception:
        return None
    if isinstance(backend, fail.Keyring):
        return None
    module = type(backend).__module__
    if module.endswith(".null"):
        return None
    if "macOS" in module or "OS_X" in module:
        return "the macOS Keychain"
    if "SecretService" in module or "libsecret" in module:
        return "the system keychain (Secret Service)"
    if "kwallet" in module.lower():
        return "the system keychain (KWallet)"
    if "Windows" in module:
        return "the Windows Credential Manager"
    return f"the system keychain ({getattr(backend, 'name', None) or type(backend).__name__})"


def open_store(config_dir: Path, fake: bool = False, mode: str | None = None):
    """The store for this studio. `config_dir` is the folder that holds `config.json`."""
    mode = (mode or os.environ.get("STUDIO2_SECRETS") or ("memory" if fake else "auto")).strip().lower()
    file = FileStore(Path(config_dir) / FILE_NAME)
    if mode == "memory":
        return MemoryStore()
    if mode == "file":
        return file
    if mode in ("keychain", "keyring"):
        return KeyringStore()
    if mode != "auto":
        raise SecretError(f"STUDIO2_SECRETS must be keychain, file or memory, not “{mode}”.")
    return FallbackStore(KeyringStore(), file) if keychain_name() else file
