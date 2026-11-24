"""A throwaway studio that serves a staged build of the interface (tools/release-ui.sh).

    python serve_staged.py <data folder> <folder of the built interface> <port>

It makes no model calls when STUDIO2_FAKE=1 is set, which the release script does.
"""
import dataclasses
import runpy
import sys
from pathlib import Path

import studio2_server.config as config

data, dist, port = sys.argv[1], Path(sys.argv[2]).resolve(), sys.argv[3]
_from_env = config.Settings.from_env
config.Settings.from_env = classmethod(lambda cls, **kw: dataclasses.replace(_from_env(**kw), web_dist=dist))
sys.argv = ["studio2_server", "--port", port, "--data", data]
runpy.run_module("studio2_server", run_name="__main__")
