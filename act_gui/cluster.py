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
@brief The cluster on which the user of the desktop interface runs long
jobs: the name of the server, the user name there and the working
directory, kept in a file that is read when the interface starts.

The interface logs in with the ssh program of the user's machine, which
uses the user's own configuration, keys and agent. It never asks for, sees
or stores a password or a key. ssh runs in batch mode: a login that needs a
password or a second factor fails with a message instead of a question.

ssh gives its command to a shell on the server, so the three settings are
checked when they are given and when they are read: a name that could be
taken for an option of ssh, or a directory with a character that means
something to a shell, is refused. Every word of the command is quoted.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

## The key of the settings of the cluster in the file
KEY = "cluster"
## The name of a host, or of an entry in the configuration of ssh. It
## starts with a letter or a digit, so it is never an option of ssh.
_SERVER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,252}$")
## A user name on a Linux machine
_USER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
## One part of the name of a directory: nothing that a shell reads as more
## than a name, and not a part that starts with a dash or is only dots
_PART = re.compile(r"^[A-Za-z0-9_+@][A-Za-z0-9._+@-]*$")
## The longest name of a directory
MAX_WORKDIR = 500
## Seconds that ssh waits for the server, and that a check may take
CONNECT_TIMEOUT = 15
CHECK_TIMEOUT = 40.0


class ClusterError(ValueError):
    """!
    @brief Settings that are not accepted, or a file that cannot be read
    or written. The message is for the user.
    """


@dataclass(frozen=True)
class ClusterSettings:
    """!
    @brief Where and as whom long jobs run.
    """
    ## The name of the server, or of its entry in the configuration of ssh
    server: str
    ## The user name on the server
    user: str
    ## The working directory on the server: an absolute path, or a path in
    ## the home directory of the user
    workdir: str


def parse(given: Any) -> ClusterSettings:
    """!
    @brief Check settings that the user gave or that were read from a file.
    @param given An object with server, user and workdir.
    @return The settings.
    @throws ClusterError if one is missing or not possible.
    """
    if not isinstance(given, dict):
        raise ClusterError("The settings of the cluster must be an object")
    for key in given:
        if key not in ("server", "user", "workdir"):
            raise ClusterError("The cluster has no setting '%s'" % (key,))
    server, user, workdir = (given.get(key) for key in ("server", "user", "workdir"))
    for name, value in (("name of the server", server), ("user name", user),
                        ("working directory", workdir)):
        if not isinstance(value, str) or not value.strip():
            raise ClusterError("Give the %s" % name)
    server, user, workdir = server.strip(), user.strip(), workdir.strip()
    if not _SERVER.match(server):
        raise ClusterError("'%s' cannot be the name of a server: use letters, digits, dots "
                           "and dashes, as in csb.example.org or a name from your ssh "
                           "configuration" % server)
    if not _USER.match(user):
        raise ClusterError("'%s' cannot be a user name on the server" % user)
    # In the home directory: with or without the tilde
    if workdir.startswith("~/"):
        workdir = workdir[2:]
    absolute = workdir.startswith("/")
    parts = [part for part in workdir.split("/") if part]
    if len(workdir) > MAX_WORKDIR or not parts:
        raise ClusterError("Give a working directory below your home directory or with its "
                           "full path, of at most %d characters" % MAX_WORKDIR)
    for part in parts:
        if not _PART.match(part):
            raise ClusterError("'%s' cannot be part of the working directory: use letters, "
                               "digits and . _ + @ -, and no spaces" % part)
    return ClusterSettings(server=server, user=user,
                           workdir=("/" if absolute else "") + "/".join(parts))


def default_config_file() -> Path:
    """!
    @brief Where the settings of the user are kept.
    @return The file settings.json in the directory act-gui of the
    configuration directory of the user.
    """
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "act-gui" / "settings.json"


def _read(path: Path) -> Dict[str, Any]:
    """!
    @brief Read the file with the settings.
    @param path The file.
    @return Its content, empty if there is no file.
    @throws ClusterError if the file is no JSON object.
    """
    if not path.is_file():
        return {}
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ClusterError("The settings in %s cannot be read: %s" % (path, exc)) from exc
    if not isinstance(content, dict):
        raise ClusterError("The settings in %s are not a JSON object" % path)
    return content


def load(path: Optional[Path]) -> Optional[ClusterSettings]:
    """!
    @brief Read the settings of the cluster when the interface starts.
    @param path The file, None for no file.
    @return The settings, None if there are none.
    @throws ClusterError if the file cannot be read or has settings that
    are not possible.
    """
    if path is None:
        return None
    content = _read(Path(path))
    if content.get(KEY) is None:
        return None
    try:
        return parse(content[KEY])
    except ClusterError as exc:
        raise ClusterError("The settings of the cluster in %s are not used. %s"
                           % (path, exc)) from exc


def save(path: Path, settings: Optional[ClusterSettings]) -> None:
    """!
    @brief Write the settings of the cluster, or take them out of the file.
    Other settings in the file are kept. The file is for the user only.
    @param path     The file. Its directory is made if needed.
    @param settings The settings, None to forget them.
    @throws ClusterError if the file cannot be written.
    """
    path = Path(path)
    try:
        try:
            content = _read(path)
        except ClusterError:
            # A file that cannot be read is replaced
            content = {}
        if settings is None:
            content.pop(KEY, None)
        else:
            content[KEY] = asdict(settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".part")
        handle = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(content, out, indent=2)
            out.write("\n")
        os.replace(partial, path)
    except OSError as exc:
        raise ClusterError("The settings cannot be written to %s: %s" % (path, exc)) from exc


def remote_command(words: Sequence[str]) -> str:
    """!
    @brief Make the text that a shell on the server reads as these words.
    @param words The words of the command.
    @return The words, each quoted, with spaces in between.
    """
    return " ".join(shlex.quote(word) for word in words)


def ssh_command(settings: ClusterSettings, remote: str, program: str = "ssh") -> List[str]:
    """!
    @brief The command that runs something on the server.
    @param settings The settings of the cluster.
    @param remote   The command for the shell on the server, made of quoted
    words, see remote_command().
    @param program  The ssh program.
    @return The words of the command, for a call without a shell.
    """
    return [program,
            # Never a question: a login that needs one fails with a message
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=%d" % CONNECT_TIMEOUT,
            "-l", settings.user,
            "--", settings.server, remote]


def check(settings: ClusterSettings, program: str = "ssh",
          run: Optional[Callable[..., Any]] = None,
          timeout: float = CHECK_TIMEOUT) -> Dict[str, Any]:
    """!
    @brief Log in once and see whether jobs can be sent: the working
    directory is there and the server has Slurm.
    @param settings The settings of the cluster.
    @param program  The ssh program.
    @param run      The function that runs a command, subprocess.run() if None.
    @param timeout  Seconds after which the check is given up.
    @return An object with "ok", a "message" for the user and, where they
    were found, the full path of the "directory" and the version of "slurm".
    """
    run = run or subprocess.run
    # Each step says what it found, so that the answer tells which one failed
    remote = " && ".join([
        "cd -- %s" % shlex.quote(settings.workdir),
        "echo ACT-DIRECTORY \"$(pwd)\"",
        "echo ACT-SLURM \"$(sbatch --version 2>/dev/null || echo none)\""])
    command = ssh_command(settings, remote, program)
    try:
        done = run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": "The server %s did not answer within %d seconds."
                % (settings.server, int(timeout))}
    except OSError as exc:
        return {"ok": False, "message": "The ssh program could not be started: %s" % exc}
    output = done.stdout.decode("utf-8", errors="replace")
    errors = done.stderr.decode("utf-8", errors="replace").strip()
    found: Dict[str, str] = {}
    for line in output.splitlines():
        for mark, key in (("ACT-DIRECTORY ", "directory"), ("ACT-SLURM ", "slurm")):
            if line.startswith(mark):
                found[key] = line[len(mark):].strip()[:300]
    if "directory" not in found:
        if done.returncode == 255 or not errors:
            # ssh itself failed: no connection or no login
            reason = errors[-400:] or "ssh ended with status %d" % done.returncode
            return {"ok": False, "message": "Logging in on %s as %s did not work without a "
                    "password. Set up a key for this server, or open a connection in a "
                    "terminal first if the server asks for a password or a code. ssh said: %s"
                    % (settings.server, settings.user, reason)}
        return {"ok": False, "message": "The working directory %s is not there on %s, or "
                "cannot be entered. Make it on the server first. The server said: %s"
                % (settings.workdir, settings.server, errors[-400:])}
    if found.get("slurm", "none") == "none":
        return {"ok": False, "directory": found["directory"],
                "message": "The login works and the directory is there, but the command "
                "sbatch of Slurm was not found on %s." % settings.server}
    return {"ok": True, "directory": found["directory"], "slurm": found["slurm"],
            "message": "The login works. Jobs go to %s on %s (%s)."
            % (found["directory"], settings.server, found["slurm"])}
