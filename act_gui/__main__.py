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
from .settings import DEFAULT_LIBRARY_ID, Settings


## The number of bytes in a megabyte, as the interface counts them
MEGABYTE = 1024 * 1024


def free_port() -> int:
    """!
    @brief Find a port on the local machine that is not in use.
    @return The port number.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def megabytes(text: str) -> int:
    """!
    @brief Read a size from the command line.
    @param text A whole number of megabytes, at least 1.
    @return The size in bytes.
    @throws argparse.ArgumentTypeError if it is not such a number.
    """
    try:
        size = int(text)
    except ValueError:
        size = 0
    if size < 1:
        raise argparse.ArgumentTypeError("'%s' is not a number of megabytes" % text)
    return size * MEGABYTE


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
    parser.add_argument("--cluster-jobs", type=Path, default=None,
                        help="directory in which the jobs on the cluster are noted and "
                        "their results are kept; %s by default" % cluster.default_jobs_dir())
    parser.add_argument("--workdir", type=Path, default=None,
                        help="directory in which the files of a second virial coefficient "
                        "that was computed on the cluster are put when the job is done, in a "
                        "directory with the name of the job; the directory in which the "
                        "interface is started by default")
    parser.add_argument("--max-molprop", type=megabytes, default=None, metavar="MB",
                        help="largest molprop file that can be opened, in megabytes; "
                        "%d by default. The programs of ACT that read the file need memory "
                        "for its compounds, up to a few times the size of the file"
                        % (Settings.max_molprop_bytes // MEGABYTE))
    parser.add_argument("--no-browser", action="store_true",
                        help="do not open the browser, only print the address")
    parser.add_argument("--version", action="version", version="act-gui " + __version__)
    args = parser.parse_args(argv)

    temporary = None
    data_dir = args.data
    if data_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="act-gui-")
        data_dir = Path(temporary.name)
    limits = {"max_molprop_bytes": args.max_molprop} if args.max_molprop else {}
    settings = Settings(data_dir=data_dir, mode="desktop", **limits,
                        config_file=args.config or cluster.default_config_file(),
                        cluster_jobs_dir=args.cluster_jobs or cluster.default_jobs_dir(),
                        work_dir=args.workdir or Path.cwd())
    if settings.default_forcefield is None:
        # The installed force fields and files of the user can still be chosen
        sys.stderr.write("The default force field %s is not installed: install the force "
                         "fields of ACT (the conda package forcefields) or set "
                         "ACT_GUI_FORCEFIELD\n" % DEFAULT_LIBRARY_ID)
    elif not settings.default_forcefield.is_file():
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
