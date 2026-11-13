#!/bin/bash
# Start Studio 2 for this Mac plus the owner's own devices over Tailscale (never a public address).
# Secrets are read from 600-mode files at start; nothing secret lives in this script.
cd "$(dirname "$0")" || exit 1
TS="$(command -v tailscale || echo /Applications/Tailscale.app/Contents/MacOS/Tailscale)"
export STUDIO2_HOSTS="${STUDIO2_HOSTS:-$("$TS" ip -4 2>/dev/null | head -1)}"
# The owner's choice (11 November 2026): the tailnet is the boundary, so its devices need no access key. Start with STUDIO2_OPEN_TAILNET=0 to ask for the key again.
export STUDIO2_OPEN_TAILNET="${STUDIO2_OPEN_TAILNET:-1}"
[ -f ~/.secrets/studio2-access.key ] && export STUDIO2_ACCESS_KEY="$(cat ~/.secrets/studio2-access.key)"
[ -f ~/.config/litellm/gateway.key ] && export LITELLM_API_KEY="$(cat ~/.config/litellm/gateway.key)"
# With a certificate for this Mac's tailnet name the Tailscale address speaks HTTPS, which is what
# lets the studio sit inside another HTTPS page (an HTTPS page cannot frame an HTTP one). Make the pair once:
#   tailscale cert --cert-file ~/.secrets/studio2-tls.crt --key-file ~/.secrets/studio2-tls.key <name>.ts.net
if [ -f ~/.secrets/studio2-tls.crt ] && [ -f ~/.secrets/studio2-tls.key ]; then
  export STUDIO2_TLS_CERT=~/.secrets/studio2-tls.crt STUDIO2_TLS_KEY=~/.secrets/studio2-tls.key
fi
[ -n "$STUDIO2_HOSTS" ] || { echo "No Tailscale address found; set STUDIO2_HOSTS."; exit 1; }
exec .venv/bin/python -m studio2_server
