"""`python -m studio2_server` — the studio on 127.0.0.1:8200. Loopback only unless STUDIO2_HOSTS
(or setup's "where it runs") names more addresses, which then requires an access key (see
access.py). The `studio` command (cli.py) starts this same server."""

from __future__ import annotations

import argparse
import socket

import uvicorn

from .access import check_settings
from .api import create_app
from .config import Settings


def serve(settings: Settings) -> None:
    """Check the settings, bind the addresses and serve until stopped."""
    check_settings(settings.hosts, settings.access_key, settings.open_tailnet, settings.shared)
    access = "no key: any device on the tailnet" if settings.open_tailnet else "access key required"
    print(f"Studio 2 on http://127.0.0.1:{settings.port}/  data: {settings.data_dir}"
          f"{'  (fake harness)' if settings.fake else ''}", flush=True)
    if settings.shared:
        print("Shared with other people: the Claude plan is switched off; every device signs in with the access key.",
              flush=True)
    # This server can run a shell. Loopback is always served; any other address is served only
    # behind the access key (AccessGuard), and only when the person asked for it.
    sockets = []
    for host in ("127.0.0.1", *(() if settings.tls else settings.hosts)):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, settings.port))
        sockets.append(sock)
        if host != "127.0.0.1":
            print(f"Also on http://{host}:{settings.port}/ ({access})", flush=True)
    for host in settings.hosts if settings.tls else ():   # bound and served with the certificate at startup (api.py)
        print(f"Also on https://{host}:{settings.port}/ by the name on the certificate ({access})", flush=True)
    config = uvicorn.Config(create_app(settings), log_level="info", timeout_graceful_shutdown=3)
    uvicorn.Server(config).run(sockets=sockets)


def main() -> None:
    parser = argparse.ArgumentParser(prog="studio2_server", description="Studio 2 server")
    parser.add_argument("--port", type=int, default=None,
                        help="port (default: the one chosen in setup, else 8200)")
    parser.add_argument("--data", help="data directory (default: $STUDIO2_DATA or <repo>/data/studio2)")
    args = parser.parse_args()
    serve(Settings.from_env(port=args.port, data=args.data))


if __name__ == "__main__":
    main()
