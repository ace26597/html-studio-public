#!/usr/bin/env bash
# Install Studio 2 from a clone, on macOS or Linux:
#
#   git clone <repository> html-studio && ./html-studio/studio2/install.sh
#
# It fetches what is missing and nothing else: uv (the official installer), through it Python 3.12
# and the studio's packages (into studio2/.venv), and Node 20 or newer (the one on PATH when it is
# new enough, otherwise a private copy under studio2/.tools from nodejs.org, checksum verified).
# It builds the interface, puts a `studio` command in ~/.local/bin, and offers to run setup.
#
# It never uses sudo by itself: when a system package is truly needed (git) it prints the exact
# command and asks. It never downloads a browser or LiteLLM by itself: each is a yes/no question,
# default no. Safe to run again; it only redoes what changed.
#
#   --yes            ask nothing; take the defaults (no sudo, no browser, no LiteLLM, no setup)
#   --no-setup       do not offer to run setup at the end
#   --with-browser   download a Chromium for looking at pages without asking (about 170 MB)
#   --with-litellm   install LiteLLM without asking: needed only for OpenAI, OpenRouter, Azure OpenAI
#                    and Gemini keys (about 430 MB; one exact version, every package checked by hash)
#   --dev            also install what the tests need
#   --no-build       keep the interface as it is built now
#   --no-launcher    do not write the `studio` command (it stays at studio2/.venv/bin/studio)
#   STUDIO_BIN_DIR   where the `studio` command goes (default ~/.local/bin)
#   STUDIO_NODE_VERSION  the Node release to fetch when one is needed (default v24.22.2)

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"      # .../studio2
TOOLS="$HERE/.tools"
BIN_DIR="${STUDIO_BIN_DIR:-$HOME/.local/bin}"
NODE_VERSION="${STUDIO_NODE_VERSION:-v24.22.2}"
NODE_MIN=20
PYTHON_VERSION=3.12

ASSUME_YES=0; RUN_SETUP=1; WITH_BROWSER=0; WITH_LITELLM=0; DEV=0; BUILD=1; LAUNCHER=1
for arg in "$@"; do
  case "$arg" in
    --yes|-y) ASSUME_YES=1 ;;
    --no-setup) RUN_SETUP=0 ;;
    --with-browser) WITH_BROWSER=1 ;;
    --with-litellm) WITH_LITELLM=1 ;;
    --dev) DEV=1 ;;
    --no-build) BUILD=0 ;;
    --no-launcher) LAUNCHER=0 ;;
    -h|--help) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf 'Unknown option: %s (see --help)\n' "$arg" >&2; exit 2 ;;
  esac
done

if [ -t 1 ]; then BOLD=$'\033[1m'; DIM=$'\033[2m'; OFF=$'\033[0m'; else BOLD=""; DIM=""; OFF=""; fi
say()  { printf '%s\n' "$*"; }
step() { printf '\n%s%s%s\n' "$BOLD" "$*" "$OFF"; }
note() { printf '  %s\n' "$*"; }
die()  { printf '\n%sStopped:%s %s\n' "$BOLD" "$OFF" "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

interactive() { [ "$ASSUME_YES" = 0 ] && [ -t 0 ]; }

# ask "Question" default(y|n) → 0 for yes. Without a terminal, or with --yes, the default answers.
ask() {
  local question="$1" default="$2" reply=""
  if ! interactive; then [ "$default" = y ]; return; fi
  if [ "$default" = y ]; then printf '%s [Y/n] ' "$question"; else printf '%s [y/N] ' "$question"; fi
  read -r reply || reply=""
  case "$reply" in
    "") [ "$default" = y ] ;;
    y|Y|yes|Yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

# -- which machine ------------------------------------------------------------------------------
case "$(uname -s)" in
  Darwin) OS=darwin ;;
  Linux) OS=linux ;;
  *) die "this installer knows macOS and Linux; this is $(uname -s)." ;;
esac
case "$(uname -m)" in
  x86_64|amd64) ARCH=x64 ;;
  arm64|aarch64) ARCH=arm64 ;;
  *) die "this installer knows x86-64 and ARM64 machines; this is $(uname -m)." ;;
esac
PKG=""                      # the Linux family, for the commands this script prints
if [ "$OS" = linux ]; then
  if have apt-get; then PKG=apt
  elif have dnf; then PKG=dnf
  elif have yum; then PKG=yum
  elif have zypper; then PKG=zypper
  elif have pacman; then PKG=pacman
  fi
fi

# The command that installs a system package here. $1 = what it is called on macOS (Homebrew),
# $2 on Debian/Ubuntu, $3 on Fedora/RHEL.
install_command() {
  case "$OS:$PKG" in
    darwin:*) printf '%s' "$1" ;;
    linux:apt) printf 'sudo apt-get install -y %s' "$2" ;;
    linux:dnf) printf 'sudo dnf install -y %s' "$3" ;;
    linux:yum) printf 'sudo yum install -y %s' "$3" ;;
    linux:zypper) printf 'sudo zypper install -y %s' "$2" ;;
    linux:pacman) printf 'sudo pacman -S --needed %s' "$2" ;;
    *) printf 'install “%s” with this system’s package manager' "$2" ;;
  esac
}

fetch() {                   # fetch URL FILE
  if have curl; then curl --proto '=https' --tlsv1.2 -fsSL --retry 2 -o "$2" "$1"
  elif have wget; then wget -q -O "$2" "$1"
  else die "neither curl nor wget is here. $(install_command 'curl ships with macOS' curl curl)"
  fi
}

sha256_of() {
  if have sha256sum; then sha256sum "$1" | cut -d' ' -f1
  elif have shasum; then shasum -a 256 "$1" | cut -d' ' -f1
  elif have openssl; then openssl dgst -sha256 "$1" | sed 's/^.*= *//'
  else die "no program to check a download with (sha256sum, shasum or openssl)."
  fi
}

STARTED=$(date +%s)
say "${BOLD}Studio installer${OFF}  ${DIM}($OS $ARCH, into $HERE)${OFF}"
# uv, Python and Node all arrive as .tar.gz; a small container or server image can lack tar or gzip.
for tool in tar gzip; do
  have "$tool" || die "$tool is not installed, and the downloads cannot be unpacked without it. $(install_command "$tool ships with macOS" "$tool" "$tool")"
done
WORK="$(mktemp -d "${TMPDIR:-/tmp}/studio-install.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

# -- git ----------------------------------------------------------------------------------------
step "1/6  git"
if have git; then
  note "$(git --version)"
else
  if [ "$OS" = darwin ]; then GIT_CMD="xcode-select --install"; else GIT_CMD="$(install_command git git git)"; fi
  note "git is not installed. The studio keeps every version of a page with it."
  note "The command that installs it:  $GIT_CMD"
  if interactive && ask "  Run that command now?" n; then
    sh -c "$GIT_CMD" || true
  fi
  have git || die "git is still missing. Run:  $GIT_CMD   and then this installer again."
fi

# -- uv, Python and the studio's packages --------------------------------------------------------
step "2/6  uv, Python $PYTHON_VERSION and the studio's packages"
UV=""
for candidate in "$(command -v uv 2>/dev/null || true)" "$TOOLS/uv/uv" "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then UV="$candidate"; break; fi
done
if [ -z "$UV" ]; then
  note "uv is not installed; fetching it into $TOOLS/uv (the official installer, astral.sh/uv)."
  mkdir -p "$TOOLS/uv"
  fetch https://astral.sh/uv/install.sh "$WORK/uv-install.sh"
  # UV_UNMANAGED_INSTALL: into this folder only, no shell profile touched.
  UV_UNMANAGED_INSTALL="$TOOLS/uv" sh "$WORK/uv-install.sh" >"$WORK/uv-install.log" 2>&1 \
    || { cat "$WORK/uv-install.log" >&2; die "the uv installer failed (see above)."; }
  UV="$TOOLS/uv/uv"
  [ -x "$UV" ] || die "uv was not found at $UV after its installer ran."
fi
note "$("$UV" --version)  ($UV)"

VENV="$HERE/.venv"
if [ -x "$VENV/bin/python" ] && "$VENV/bin/python" -c "import sys; sys.exit(sys.version_info[:2] != (3, 12))" 2>/dev/null; then
  note "Python environment: $VENV (kept)"
else
  note "Making the Python environment (uv fetches Python $PYTHON_VERSION when this machine has none)."
  "$UV" venv --quiet --clear --python "$PYTHON_VERSION" "$VENV"
fi
EXTRAS="look"; [ "$DEV" = 1 ] && EXTRAS="look,dev"
"$UV" pip install --quiet --python "$VENV/bin/python" -e "${HERE}[${EXTRAS}]"
note "$("$VENV/bin/python" --version), packages installed"
[ -x "$VENV/bin/studio" ] || die "the studio command was not installed into $VENV/bin."

# -- Node ---------------------------------------------------------------------------------------
step "3/6  Node $NODE_MIN or newer"
node_major() { "$1" --version 2>/dev/null | sed -n 's/^v\([0-9][0-9]*\)\..*/\1/p'; }
new_enough() { local major; major="$(node_major "$1")"; [ -n "$major" ] && [ "$major" -ge "$NODE_MIN" ]; }

NODE_BIN=""
if have node && new_enough "$(command -v node)" && have npm; then
  NODE_BIN="$(dirname "$(command -v node)")"
  note "Using the Node on this machine: $(node --version)  ($(command -v node))"
elif [ -x "$TOOLS/node/bin/node" ] && new_enough "$TOOLS/node/bin/node"; then
  NODE_BIN="$TOOLS/node/bin"
  note "Using the studio's own copy: $("$NODE_BIN/node" --version)  ($NODE_BIN)"
else
  if have node; then note "The Node on this machine is $(node --version 2>/dev/null || echo unusable): too old."; fi
  if [ "$OS" = linux ] && (ldd --version 2>&1 || true) | grep -qi musl; then
    die "nodejs.org publishes no build for this kind of Linux (musl). Install Node $NODE_MIN or newer with the system's package manager (Alpine: apk add nodejs npm) and run this again."
  fi
  FILE="node-$NODE_VERSION-$OS-$ARCH.tar.gz"
  note "Fetching a private copy into $TOOLS/node: $FILE from nodejs.org (nothing on the system changes)."
  fetch "https://nodejs.org/dist/$NODE_VERSION/$FILE" "$WORK/$FILE"
  # The checksums of the default release are written here; another release is checked against
  # the list nodejs.org publishes beside it.
  WANT=""
  case "$FILE" in
    node-v24.22.2-darwin-arm64.tar.gz) WANT=aa9549349e47dffaa42dcf95d6e06ade8c4bdafc980979883b88767670df385a ;;
    node-v24.22.2-darwin-x64.tar.gz)   WANT=d5e336d588c45be0c37dbd52cc29895593009ca4997a77a1fd02815131ed0e52 ;;
    node-v24.22.2-linux-arm64.tar.gz)  WANT=95c264150aceb7f3437a29fd6984924f5f8d1511ea3cbd6c508b83f6c555dae2 ;;
    node-v24.22.2-linux-x64.tar.gz)    WANT=361718c444e49af2ca5a110c83ba6a8901143f23ab285ebbef0341f391f482a9 ;;
    *) fetch "https://nodejs.org/dist/$NODE_VERSION/SHASUMS256.txt" "$WORK/SHASUMS256.txt"
       WANT="$(awk -v f="$FILE" '$2 == f {print $1}' "$WORK/SHASUMS256.txt")" ;;
  esac
  [ -n "$WANT" ] || die "no checksum is known for $FILE."
  GOT="$(sha256_of "$WORK/$FILE")"
  [ "$GOT" = "$WANT" ] || die "the download of $FILE does not match its checksum (got $GOT, expected $WANT). Nothing was installed from it."
  note "Checksum verified (sha256 $WANT)."
  rm -rf "$TOOLS/node.new"
  mkdir -p "$TOOLS/node.new"
  tar -xzf "$WORK/$FILE" -C "$TOOLS/node.new" --strip-components=1
  rm -rf "$TOOLS/node"
  mv "$TOOLS/node.new" "$TOOLS/node"
  NODE_BIN="$TOOLS/node/bin"
  note "Node $("$NODE_BIN/node" --version) is in $TOOLS/node. The studio uses it for JavaScript apps too."
fi

# -- the interface ------------------------------------------------------------------------------
step "4/6  The interface"
if [ "$BUILD" = 1 ]; then
  (
    cd "$HERE/web"
    export PATH="$NODE_BIN:$PATH"
    if [ ! -d node_modules ] || [ package-lock.json -nt node_modules/.package-lock.json ]; then
      note "Installing its packages (npm ci)."
      npm ci --no-audit --no-fund --no-update-notifier >"$WORK/npm.log" 2>&1 || { cat "$WORK/npm.log" >&2; exit 1; }
    else
      note "Its packages are up to date."
    fi
    note "Building."
    npm run build --silent >"$WORK/build.log" 2>&1 || { cat "$WORK/build.log" >&2; exit 1; }
  ) || die "the interface could not be built (see above)."
  note "Built: $HERE/web/dist"
elif [ -f "$HERE/web/dist/index.html" ]; then
  note "Kept as built (--no-build)."
else
  note "Not built (--no-build), and there is no build yet: the studio will say so when opened."
fi

# -- the studio command -------------------------------------------------------------------------
step "5/6  The studio command"
STUDIO="$VENV/bin/studio"
if [ "$LAUNCHER" = 1 ]; then
  TARGET="$BIN_DIR/studio"
  if [ -e "$TARGET" ] && ! grep -q "studio2 launcher" "$TARGET" 2>/dev/null; then
    note "$TARGET exists and is something else; it was left alone."
    note "The studio's command is $STUDIO"
  else
    mkdir -p "$BIN_DIR"
    printf '#!/bin/sh\n# studio2 launcher, written by %s/install.sh. Safe to delete.\nexec "%s" "$@"\n' "$HERE" "$STUDIO" >"$TARGET"
    chmod 755 "$TARGET"
    STUDIO="$TARGET"
    note "$TARGET"
    case ":$PATH:" in
      *":$BIN_DIR:"*) STUDIO=studio ;;
      *) note "$BIN_DIR is not on your PATH. To type just “studio”, add this line to ~/.zshrc or ~/.bashrc:"
         note "  export PATH=\"$BIN_DIR:\$PATH\"" ;;
    esac
  fi
else
  note "$STUDIO (no launcher written)"
fi

# -- what is optional ---------------------------------------------------------------------------
step "6/6  Optional parts (reported, never installed without a yes)"
BROWSER="$("$VENV/bin/python" -c 'from studio2_server.look import find_browser; print(find_browser() or "")' 2>/dev/null || true)"
if [ -n "$BROWSER" ]; then
  note "Looking at pages: a browser is here ($BROWSER)."
else
  note "Looking at pages: no browser the assistant can drive. Without one it cannot take"
  note "  screenshots, check its own pages or render video. It is a Chromium of about 170 MB,"
  note "  downloaded by:  $VENV/bin/python -m playwright install chromium"
  if [ "$WITH_BROWSER" = 1 ] || { interactive && ask "  Download it now?" n; }; then
    "$VENV/bin/python" -m playwright install chromium || note "The download failed; run the command above later."
    if [ "$OS" = linux ]; then
      note "If it does not start, Linux may lack its system libraries:"
      note "  sudo $VENV/bin/python -m playwright install-deps chromium"
    fi
  else
    note "  Not downloaded."
  fi
fi
if "$VENV/bin/python" -c 'import sys; from studio2_server import gateway; sys.exit(0 if gateway.installed()["installed"] else 1)' 2>/dev/null; then
  note "Other models: LiteLLM is here (for OpenAI, OpenRouter, Azure OpenAI and Gemini keys)."
else
  note "Other models: Claude models need nothing more. A key from OpenAI, OpenRouter, Azure OpenAI or"
  note "  Gemini runs through LiteLLM, which the studio starts beside itself on this computer only."
  note "  It is one exact version (about 430 MB), every package checked against a hash kept in"
  note "  $HERE/litellm-requirements.txt. Setup offers it again when you choose such a key."
  if [ "$WITH_LITELLM" = 1 ] || { interactive && ask "  Install it now?" n; }; then
    "$VENV/bin/studio" litellm install --yes || note "It was not installed; run later:  $VENV/bin/studio litellm install"
  else
    note "  Not installed. Later:  $VENV/bin/studio litellm install"
  fi
fi
if have ffmpeg; then note "Video files: ffmpeg is here."
else note "Video files: ffmpeg is missing.  $(install_command 'brew install ffmpeg' ffmpeg ffmpeg-free)"; fi
if have soffice || have libreoffice || [ -x /Applications/LibreOffice.app/Contents/MacOS/soffice ]; then
  note "PowerPoint, Word and Excel files: LibreOffice is here."
else
  note "PowerPoint, Word and Excel files: LibreOffice is missing.  $(install_command 'brew install --cask libreoffice' libreoffice libreoffice)"
fi

ELAPSED=$(( $(date +%s) - STARTED ))
step "Installed in ${ELAPSED}s."
say "  $STUDIO setup     choose the model account, where it runs and who uses it"
say "  $STUDIO start     start it and open it in the browser"
say "  $STUDIO doctor    check what is installed and which model accounts work"
if [ "$RUN_SETUP" = 1 ] && interactive; then
  say ""
  if ask "Run setup now?" y; then
    "$VENV/bin/studio" setup || true
    say ""
    if ask "Start the studio now?" y; then "$VENV/bin/studio" start || true; fi
  fi
fi
