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
## The ways in which ACT is made available in a job: a module, a file that
## the shell reads (such as the ACTRC of an installation), a conda
## environment, or nothing because the program is found as it is
LOAD_KINDS = ("module", "source", "conda", "none")
## The name of a module, such as ACT or ACT/1.9, and of a conda environment
_MODULE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*(/[A-Za-z0-9][A-Za-z0-9._+-]*)*$")
_ENVIRONMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
## One or more partitions of Slurm, with commas in between
_PARTITIONS = re.compile(r"^[A-Za-z0-9_-]+(,[A-Za-z0-9_-]+)*$")
## What a job asks for to get a GPU, such as gpu:1 or gpu:a100:2
_GRES = re.compile(r"^[A-Za-z0-9_]+(:[A-Za-z0-9_.-]+){0,3}$")
## The name of an account for the accounting of Slurm
_ACCOUNT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
## The programs that start the processes of a parallel job
LAUNCHERS = ("srun", "mpirun")
## The name of the program of ACT on the cluster, such as alexandria or,
## for a build in double precision, alexandria_d. A name, not a path.
_PROGRAM = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]{0,63}$")
## The shortest and longest time between two questions for the state of a
## job, in seconds. A cluster may block an account that logs in often.
POLL_RANGE = (60, 3600)
## The largest number of processes and the longest time of a job, in minutes
MAX_TASKS = 4096
MAX_MINUTES = 30 * 24 * 60
## What the name of a job looks like
_JOB_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


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
    ## How ACT is made available in a job: one of LOAD_KINDS
    load: str = "module"
    ## What is loaded: the name of the module or of the conda environment,
    ## or the file that the shell reads; empty for the kind none
    load_name: str = "ACT"
    ## The partitions for jobs without a GPU
    partition: str = "CLUSTER,CLUSTER-AMD"
    ## The partitions for jobs that need a GPU
    gpu_partition: str = "GPU,CLUSTER-AMD"
    ## What a job that needs a GPU asks for with --gres
    gpu_gres: str = "gpu:1"
    ## The account for the accounting of Slurm, empty for the default one
    account: str = ""
    ## The program that starts the processes of a parallel job
    launcher: str = "srun"
    ## The name of the program of ACT on the cluster
    program: str = "alexandria"
    ## Seconds between two questions for the state of a job
    poll_seconds: int = 120


## The settings that the user has to give; the others have a proposal
REQUIRED = ("server", "user", "workdir")


def _directory(given: str, what: str) -> str:
    """!
    @brief Check the name of a directory or a file on the server.
    @param given The name: an absolute path, or a path in the home
    directory with or without a tilde in front.
    @param what  What it is, for the message.
    @return The name without a tilde and without a slash at the end.
    @throws ClusterError if it has a part that means something to a shell.
    """
    # In the home directory: with or without the tilde
    name = given[2:] if given.startswith("~/") else given
    absolute = name.startswith("/")
    parts = [part for part in name.split("/") if part]
    if len(name) > MAX_WORKDIR or not parts:
        raise ClusterError("Give %s below your home directory or with its full path, of at "
                           "most %d characters" % (what, MAX_WORKDIR))
    for part in parts:
        if not _PART.fullmatch(part):
            raise ClusterError("'%s' cannot be part of %s: use letters, digits and "
                               ". _ + @ -, and no spaces" % (part, what))
    return ("/" if absolute else "") + "/".join(parts)


def parse(given: Any) -> ClusterSettings:
    """!
    @brief Check settings that the user gave or that were read from a file.
    @param given An object with server, user and workdir, and optionally
    load, load_name, partition, gpu_partition, gpu_gres, account, launcher,
    program and poll_seconds. What is left out gets the value that is
    proposed.
    @return The settings.
    @throws ClusterError if one is missing or not possible.
    """
    if not isinstance(given, dict):
        raise ClusterError("The settings of the cluster must be an object")
    default = ClusterSettings("", "", "")
    known = tuple(asdict(default))
    for key in given:
        if key not in known:
            raise ClusterError("The cluster has no setting '%s'" % (key,))
    text: Dict[str, str] = {}
    for key in known:
        if key == "poll_seconds":
            continue
        value = given.get(key, None if key in REQUIRED else getattr(default, key))
        if not isinstance(value, str):
            raise ClusterError("Give the %s" % {"server": "name of the server",
                                               "user": "user name",
                                               "workdir": "working directory"}.get(key, key))
        text[key] = value.strip()
    for key, name in (("server", "name of the server"), ("user", "user name"),
                      ("workdir", "working directory")):
        if not text[key]:
            raise ClusterError("Give the %s" % name)
    if not _SERVER.fullmatch(text["server"]):
        raise ClusterError("'%s' cannot be the name of a server: use letters, digits, dots "
                           "and dashes, as in csb.example.org or a name from your ssh "
                           "configuration" % text["server"])
    if not _USER.fullmatch(text["user"]):
        raise ClusterError("'%s' cannot be a user name on the server" % text["user"])
    text["workdir"] = _directory(text["workdir"], "the working directory")

    # How ACT is made available in a job
    kind, name = text["load"], text["load_name"]
    if kind not in LOAD_KINDS:
        raise ClusterError("ACT is loaded as one of: %s" % ", ".join(LOAD_KINDS))
    if kind == "none":
        name = ""
    elif kind == "source":
        name = _directory(name, "the file that is read") if name else ""
    elif len(name) > 100 or not (_MODULE if kind == "module" else _ENVIRONMENT).fullmatch(name):
        raise ClusterError("'%s' cannot be the name of a %s" % (
            name, "module" if kind == "module" else "conda environment"))
    if kind != "none" and not name:
        raise ClusterError("Give what is loaded to make ACT available")
    text["load_name"] = name

    for key, what in (("partition", "The partitions"),
                      ("gpu_partition", "The partitions for jobs with a GPU")):
        if len(text[key]) > 200 or not _PARTITIONS.fullmatch(text[key]):
            raise ClusterError("%s are names with commas in between, as in "
                               "CLUSTER,CLUSTER-AMD" % what)
    if not _GRES.fullmatch(text["gpu_gres"]) or len(text["gpu_gres"]) > 100:
        raise ClusterError("What a job asks for to get a GPU looks like gpu:1")
    if text["account"] and not _ACCOUNT.fullmatch(text["account"]):
        raise ClusterError("'%s' cannot be the name of an account" % text["account"])
    if text["launcher"] not in LAUNCHERS:
        raise ClusterError("Parallel jobs are started with one of: %s" % ", ".join(LAUNCHERS))
    if not _PROGRAM.fullmatch(text["program"]):
        raise ClusterError("'%s' cannot be the name of the program of ACT on the cluster: "
                           "give a name such as alexandria or alexandria_d, without a "
                           "directory" % text["program"])
    poll = given.get("poll_seconds", default.poll_seconds)
    if isinstance(poll, bool) or not isinstance(poll, int) \
            or not POLL_RANGE[0] <= poll <= POLL_RANGE[1]:
        raise ClusterError("The time between two questions for the state of a job is a whole "
                           "number of seconds between %d and %d" % POLL_RANGE)
    return ClusterSettings(poll_seconds=poll, **text)


def load_lines(settings: ClusterSettings) -> List[str]:
    """!
    @brief The lines of a batch script that make ACT available.
    @param settings The settings of the cluster.
    @return The lines, none if the program is found as it is.
    """
    name = shlex.quote(settings.load_name)
    if settings.load == "module":
        return ["module load %s" % name]
    if settings.load == "source":
        return ["source %s" % name]
    if settings.load == "conda":
        # A batch job does not read the files that make conda known to a shell
        return ['eval "$(conda shell.bash hook)"', "conda activate %s" % name]
    return []


## The file in the directory of a job in which the script leaves the exit
## status of the program, and the file with what Slurm and the program print
STATUS_FILE = "act-exit-status"
OUTPUT_FILE = "slurm.out"


def batch_script(settings: ClusterSettings, name: str, words: Sequence[str], *,
                 tasks: int = 1, minutes: int = 60, gpu: bool = False) -> str:
    """!
    @brief The script that Slurm runs for a job. It is made from the
    settings and from checked values only; every word of the command is
    quoted for the shell.
    @param settings The settings of the cluster.
    @param name     The name of the job in the queue.
    @param words    The command with its arguments, the program first. The
    names of files are those in the directory of the job.
    @param tasks    The number of processes.
    @param minutes  The longest time the job may take.
    @param gpu      Whether the job needs a GPU.
    @return The text of the script.
    @throws ClusterError for a name, a number of processes or a time that
    is not possible, or an empty command.
    """
    if not isinstance(name, str) or not _JOB_NAME.fullmatch(name):
        raise ClusterError("'%s' cannot be the name of a job" % (name,))
    if isinstance(tasks, bool) or not isinstance(tasks, int) or not 1 <= tasks <= MAX_TASKS:
        raise ClusterError("The number of processes is between 1 and %d" % MAX_TASKS)
    if isinstance(minutes, bool) or not isinstance(minutes, int) \
            or not 1 <= minutes <= MAX_MINUTES:
        raise ClusterError("The time of a job is between 1 minute and %d days"
                           % (MAX_MINUTES // 1440))
    if not words or not all(isinstance(word, str) and word and "\n" not in word
                            and "\0" not in word for word in words):
        raise ClusterError("The job has no command")
    # A login shell: the job is sent from a login without a terminal, in
    # which the commands module and conda need not be known
    lines = ["#!/bin/bash -l",
             "#SBATCH --job-name=%s" % name,
             "#SBATCH --partition=%s" % (settings.gpu_partition if gpu else settings.partition),
             # The short form: some installations of Slurm do not take --ntasks
             "#SBATCH -n %d" % tasks,
             "#SBATCH --time=%d-%02d:%02d:00" % (minutes // 1440, minutes % 1440 // 60,
                                                 minutes % 60),
             "#SBATCH --output=%s" % OUTPUT_FILE]
    if gpu:
        lines.append("#SBATCH --gres=%s" % settings.gpu_gres)
    if settings.account:
        lines.append("#SBATCH --account=%s" % settings.account)
    lines += ["", "# Made by the user interface of ACT. The exit status of the program is",
              "# left in a file, so that the interface can tell how the job ended."]
    lines += load_lines(settings)
    command = remote_command(words)
    if tasks > 1:
        launcher = "srun" if settings.launcher == "srun" else "mpirun -n %d" % tasks
        command = "%s %s" % (launcher, command)
    lines += [command, "status=$?", "echo $status > %s" % STATUS_FILE, "exit $status", ""]
    return "\n".join(lines)


def default_config_file() -> Path:
    """!
    @brief Where the settings of the user are kept.
    @return The file settings.json in the directory act-gui of the
    configuration directory of the user.
    """
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "act-gui" / "settings.json"


def default_jobs_dir() -> Path:
    """!
    @brief Where the jobs on the cluster are noted and their results are kept.
    @return The directory cluster-jobs in the directory act-gui of the data
    directory of the user.
    """
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "act-gui" / "cluster-jobs"


def read_settings(path: Path) -> Dict[str, Any]:
    """!
    @brief Read the file with the settings of the user: those of the
    cluster and the preferences of the page, each under its own key.
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
    content = read_settings(Path(path))
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
    update_settings(Path(path), KEY, None if settings is None else asdict(settings))


def update_settings(path: Path, key: str, value: Optional[Dict[str, Any]]) -> None:
    """!
    @brief Write one part of the file with the settings of the user, or
    take it out. The other parts are kept. The file is for the user only.
    @param path  The file. Its directory is made if needed.
    @param key   The part, such as cluster.
    @param value Its content, None to take it out.
    @throws ClusterError if the file cannot be written.
    """
    try:
        try:
            content = read_settings(path)
        except ClusterError:
            # A file that cannot be read is replaced
            content = {}
        if value is None:
            content.pop(key, None)
        else:
            content[key] = value
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
