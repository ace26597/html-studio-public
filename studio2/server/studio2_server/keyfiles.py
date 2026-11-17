"""Files that hold keys (CONTRACT.md, "Key files"): one rule for attach, ship, templates and export.

A path is a key file when any part of it is one of these names, so a folder named `.env` (a
virtualenv, or a folder of settings) counts too:

- `.env`, `.env.<anything>`, `.envrc` (direnv);
- `.dev.vars`, `.dev.vars.<anything>` (Cloudflare wrangler's local secrets);
- `.netrc`, `.pypirc` (passwords for servers and package indexes);
- `id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519` (SSH private keys).

Left out on purpose, because ordinary projects keep them in their code and refusing them would
break ordinary work: `.npmrc` (usually a registry address, with any token read from the
environment), `*.pem` and `*.key` (public certificates and Keynote files share them), `*.pub`.

For shipping only, the usual key-free names (`.env.example`, `.sample`, `.template`, `.dist`,
`.defaults`) are ordinary files: they are committed on purpose and edited with every new setting.
"""

from __future__ import annotations

from pathlib import PurePosixPath

NAMES = {".env", ".envrc", ".dev.vars", ".netrc", ".pypirc", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
PREFIXES = (".env.", ".dev.vars.")
KEY_FREE = {"example", "sample", "template", "dist", "defaults"}
# The same names as git patterns: without a slash they match at any depth, files and folders.
GIT_PATTERNS = [".env", ".env.*", ".envrc", ".dev.vars", ".dev.vars.*", ".netrc", ".pypirc",
                "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"]
PATHSPEC_EXCLUDES = [f":(exclude,glob)**/{pattern}" for pattern in GIT_PATTERNS]


def is_key_name(name: str) -> bool:
    return name in NAMES or name.startswith(PREFIXES)


def _parts(path) -> tuple[str, ...]:
    return tuple(path) if isinstance(path, (tuple, list)) else PurePosixPath(str(path)).parts


def is_key_file(path) -> bool:
    """`path` is a posix path string or a tuple of its parts."""
    return any(is_key_name(part) for part in _parts(path))


def is_key_free(path) -> bool:
    """A key file by name that by convention holds no keys (`.env.example`): ordinary for a ship."""
    parts = _parts(path)
    if not parts or not is_key_name(parts[-1]) or any(is_key_name(part) for part in parts[:-1]):
        return False
    return parts[-1].rsplit(".", 1)[-1].lower() in KEY_FREE and parts[-1] not in NAMES
