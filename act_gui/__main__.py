#
# This source file is part of the Alexandria Chemistry Toolkit.
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
"""!
@file
@brief Starts the interface on the desktop: `python -m act_gui`.

The server listens on the local machine only, on a port that is free, and
prints the address with the key that the browser needs. Unless told
otherwise it opens that address in the browser.
"""
from __future__ import annotations

import argparse
import socket
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path
from typing import List, Optional

import uvicorn

from . import __version__, cluster
from .app import create_app
from .settings import Settings


def free_port() -> int:
    """!
    @brief Find a port on the local machine that is not in use.
    @return The port number.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main(argv: Optional[List[str]] = None) -> int:
    """!
    @brief Start the server for one user on this machine.
    @param argv The command line without the name of the program, that of
    Python if None.
    @return The exit status.
    """
    parser = argparse.ArgumentParser(
        prog="act-gui", description="Graphical user interface of the Alexandria "
        "Chemistry Toolkit, for the browser.")
    parser.add_argument("--port", type=int, default=0,
                        help="port to listen on, a free one by default")
    parser.add_argument("--data", type=Path, default=None,
                        help="directory for uploads and results, a temporary "
                        "directory that is removed at the end by default")
    parser.add_argument("--config", type=Path, default=None,
                        help="file with your settings, such as the cluster for long "
                        "jobs; %s by default" % cluster.default_config_file())
    parser.add_argument("--no-browser", action="store_true",
                        help="do not open the browser, only print the address")
    parser.add_argument("--version", action="version", version="act-gui " + __version__)
    args = parser.parse_args(argv)

    temporary = None
    data_dir = args.data
    if data_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="act-gui-")
        data_dir = Path(temporary.name)
    settings = Settings(data_dir=data_dir, mode="desktop",
                        config_file=args.config or cluster.default_config_file())
    if not settings.default_forcefield.is_file():
        sys.stderr.write("The default force field %s does not exist\n"
                         % settings.default_forcefield)
        return 1
    port = args.port or free_port()
    address = "http://127.0.0.1:%d/?token=%s" % (port, settings.token)
    print("ACT interface %s\nOpen %s\nStop with Ctrl-C." % (__version__, address), flush=True)
    if not args.no_browser:
        # When the server has had the time to start
        threading.Timer(1.0, webbrowser.open, args=(address,)).start()
    app = create_app(settings, allowed_hosts=["127.0.0.1", "localhost"])
    try:
        # Only the local machine can connect
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    finally:
        if temporary is not None:
            temporary.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
