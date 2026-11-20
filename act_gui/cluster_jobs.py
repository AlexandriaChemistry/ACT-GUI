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
@brief Jobs on the cluster of the user: the four things the interface does
there, each with one login. A job is sent with its files and its batch
script, its state is asked for, its results are fetched and it is stopped.

Files travel as a tar archive over the connection of ssh, so that sending a
job and starting it is one login and not one for each file. What comes back
is unpacked with care: only plain files with a name that the interface
asked for end up in the directory of the job on this machine.

Every command for the shell on the server is made of words that are quoted,
see cluster.remote_command(). The name of a job and its number in Slurm are
checked before they are used.
"""
from __future__ import annotations

import io
import re
import subprocess
import tarfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Union

from .cluster import (STATUS_FILE, ClusterError, ClusterSettings, remote_command,
                      ssh_command)

## The name of the batch script in the directory of a job
SCRIPT_NAME = "job.sh"
## What the name of a job looks like: it is the name of its directory
_JOB_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
## The number that Slurm gives a job
_SLURM_ID = re.compile(r"^[0-9]{1,18}$")
## The name of a file of a job: no directory, nothing a shell reads as more
_FILE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._+@#-]{0,199}$")
## The states of Slurm in which a job is not done yet
WAITING = ("PENDING", "CONFIGURING", "SUSPENDED", "REQUEUED", "RESIZING")
RUNNING = ("RUNNING", "COMPLETING", "STAGE_OUT", "SIGNALING")
## Seconds that one login may take, for commands that move files and others
TRANSFER_TIMEOUT = 600.0
COMMAND_TIMEOUT = 60.0
## The largest result that is fetched, in bytes
MAX_FETCH_BYTES = 2 * 1024 ** 3


def _check_name(name: Any) -> str:
    """!
    @brief Check the name of a job.
    @param name The name.
    @return The name.
    @throws ClusterError if it cannot be the name of a directory.
    """
    if not isinstance(name, str) or not _JOB_NAME.match(name):
        raise ClusterError("'%s' cannot be the name of a job" % (name,))
    return name


def _check_id(slurm_id: Any) -> str:
    """!
    @brief Check the number of a job in Slurm.
    @param slurm_id The number, as text.
    @return The number.
    @throws ClusterError if it is no number.
    """
    if not isinstance(slurm_id, str) or not _SLURM_ID.match(slurm_id):
        raise ClusterError("'%s' is not the number of a job in Slurm" % (slurm_id,))
    return slurm_id


def _check_files(names: Sequence[str]) -> List[str]:
    """!
    @brief Check the names of files of a job.
    @param names The names.
    @return The names as a list.
    @throws ClusterError if one has a directory or a character that is not allowed.
    """
    for name in names:
        if not isinstance(name, str) or not _FILE_NAME.match(name):
            raise ClusterError("'%s' cannot be the name of a file of a job" % (name,))
    return list(names)


def _job_dir(settings: ClusterSettings, name: str) -> str:
    """!
    @brief The directory of a job on the server.
    @param settings The settings of the cluster.
    @param name     The name of the job.
    @return The path, below the working directory.
    """
    return "%s/%s" % (settings.workdir, _check_name(name))


def _run(settings: ClusterSettings, remote: str, *, program: str,
         run: Optional[Callable[..., Any]], timeout: float,
         data: Optional[bytes] = None) -> Any:
    """!
    @brief Log in once and run a command on the server.
    @param settings The settings of the cluster.
    @param remote   The command for the shell on the server.
    @param program  The ssh program.
    @param run      The function that runs a command, subprocess.run() if None.
    @param timeout  Seconds after which it is given up.
    @param data     What the command on the server reads, nothing if None.
    @return What subprocess.run() returns, with the output as bytes.
    @throws ClusterError if ssh cannot be started, does not finish in time
    or cannot log in.
    """
    run = run or subprocess.run
    extra: Dict[str, Any] = {"input": data} if data is not None else {"stdin": subprocess.DEVNULL}
    try:
        done = run(ssh_command(settings, remote, program), stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE, timeout=timeout, check=False, **extra)
    except subprocess.TimeoutExpired as exc:
        raise ClusterError("The server %s did not answer within %d seconds"
                           % (settings.server, int(timeout))) from exc
    except OSError as exc:
        raise ClusterError("The ssh program could not be started: %s" % exc) from exc
    if done.returncode == 255:
        raise ClusterError("Logging in on %s as %s did not work: %s"
                           % (settings.server, settings.user,
                              done.stderr.decode("utf-8", errors="replace").strip()[-400:]))
    return done


def _text(done: Any) -> str:
    """!
    @brief What the server said went wrong.
    @param done What _run() returned.
    @return The end of the error output, or of the output if that is empty.
    """
    said = done.stderr.decode("utf-8", errors="replace").strip() \
        or done.stdout.decode("utf-8", errors="replace").strip()
    return said[-400:]


def submit(settings: ClusterSettings, name: str, script: str,
           files: Mapping[str, Union[Path, bytes]], *, program: str = "ssh",
           run: Optional[Callable[..., Any]] = None) -> str:
    """!
    @brief Send a job with its files and put it in the queue, in one login.
    @param settings The settings of the cluster.
    @param name     The name of the job. A directory with this name is made
    in the working directory; it must not be there yet.
    @param script   The batch script, see cluster.batch_script().
    @param files    The input files: for each name in the directory of the
    job the file on this machine, or its content.
    @param program  The ssh program.
    @param run      The function that runs a command, subprocess.run() if None.
    @return The number that Slurm gave the job.
    @throws ClusterError if the job could not be sent or was not accepted.
    """
    directory = _job_dir(settings, name)
    names = _check_files(list(files))
    if SCRIPT_NAME in names:
        raise ClusterError("A file of a job cannot be called %s" % SCRIPT_NAME)
    # The files and the script in one archive, which the server unpacks
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        contents = [(SCRIPT_NAME, script.encode("utf-8"))]
        for filename in names:
            source = files[filename]
            contents.append((filename, source if isinstance(source, bytes)
                             else Path(source).read_bytes()))
        for filename, content in contents:
            info = tarfile.TarInfo(filename)
            info.size = len(content)
            info.mode = 0o755 if filename == SCRIPT_NAME else 0o644
            archive.addfile(info, io.BytesIO(content))
    remote = " && ".join([
        # Without -p: a directory that is there already belongs to another job
        remote_command(["mkdir", "--", directory]),
        remote_command(["cd", "--", directory]),
        "tar xf -",
        remote_command(["sbatch", "--parsable", SCRIPT_NAME])])
    done = _run(settings, remote, program=program, run=run, timeout=TRANSFER_TIMEOUT,
                data=buffer.getvalue())
    if done.returncode != 0:
        raise ClusterError("The job could not be sent to %s: %s" % (settings.server, _text(done)))
    # With --parsable sbatch prints the number, and the cluster after a semicolon
    words = done.stdout.decode("utf-8", errors="replace").split()
    slurm_id = words[-1].split(";")[0] if words else ""
    if not _SLURM_ID.match(slurm_id):
        raise ClusterError("Slurm on %s did not give the job a number: %s"
                           % (settings.server, _text(done)))
    return slurm_id


def status(settings: ClusterSettings, name: str, slurm_id: str, *, program: str = "ssh",
           run: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    """!
    @brief Ask once how a job is doing.
    @param settings The settings of the cluster.
    @param name     The name of the job.
    @param slurm_id The number that Slurm gave the job.
    @param program  The ssh program.
    @param run      The function that runs a command, subprocess.run() if None.
    @return An object with "state": waiting, running, finished, failed or
    lost (Slurm has the job no more and it left no exit status: it was
    stopped or ran out of time); "slurm" with the state as Slurm names it,
    and "exit" with the exit status of the program if it ended.
    @throws ClusterError if the server could not be asked.
    """
    directory = _job_dir(settings, name)
    remote = "; ".join([
        'echo ACT-STATE "$(%s)"' % remote_command(["squeue", "-h", "-j", _check_id(slurm_id),
                                                    "-o", "%T"]) + " 2>/dev/null",
        'echo ACT-EXIT "$(cat %s 2>/dev/null)"' % remote_command(
            ["%s/%s" % (directory, STATUS_FILE)])])
    done = _run(settings, remote, program=program, run=run, timeout=COMMAND_TIMEOUT)
    found = {"ACT-STATE": None, "ACT-EXIT": None}
    for line in done.stdout.decode("utf-8", errors="replace").splitlines():
        words = line.split()
        if words and words[0] in found:
            found[words[0]] = words[1] if len(words) > 1 else ""
    if found["ACT-STATE"] is None or found["ACT-EXIT"] is None:
        raise ClusterError("The state of the job could not be read on %s: %s"
                           % (settings.server, _text(done)))
    slurm = (found["ACT-STATE"] or "").upper()
    answer: Dict[str, Any] = {"slurm": slurm}
    if re.match(r"^[0-9]{1,3}$", found["ACT-EXIT"] or ""):
        # The program ended, whatever Slurm still says about the job
        answer["exit"] = int(found["ACT-EXIT"])
        answer["state"] = "finished" if answer["exit"] == 0 else "failed"
    elif slurm in WAITING:
        answer["state"] = "waiting"
    elif slurm in RUNNING:
        answer["state"] = "running"
    else:
        answer["state"] = "lost"
    return answer


def fetch(settings: ClusterSettings, name: str, names: Sequence[str], target: Path, *,
          program: str = "ssh", run: Optional[Callable[..., Any]] = None) -> List[Path]:
    """!
    @brief Fetch files of a job, in one login.
    @param settings The settings of the cluster.
    @param name     The name of the job.
    @param names    The names of the files to fetch. Those that are not
    there are left out.
    @param target   The directory on this machine for the files.
    @param program  The ssh program.
    @param run      The function that runs a command, subprocess.run() if None.
    @return The files that were fetched.
    @throws ClusterError if the server could not be asked, or sent
    something else than the files that were asked for.
    """
    directory = _job_dir(settings, name)
    wanted = _check_files(names)
    if not wanted:
        return []
    # Only the files that are there, so that tar does not stop at the first
    # one that is missing
    listed = " ".join(remote_command([filename]) for filename in wanted)
    remote = remote_command(["cd", "--", directory]) + " && { " \
        "found=$(for f in %s; do test -f \"$f\" && echo \"$f\"; done); " \
        "tar cf - -T /dev/null $found; }" % listed
    done = _run(settings, remote, program=program, run=run, timeout=TRANSFER_TIMEOUT)
    if done.returncode != 0:
        raise ClusterError("The results could not be fetched from %s: %s"
                           % (settings.server, _text(done)))
    if len(done.stdout) > MAX_FETCH_BYTES:
        raise ClusterError("The results are larger than %d MB" % (MAX_FETCH_BYTES // 1024 ** 2))
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    fetched: List[Path] = []
    try:
        with tarfile.open(fileobj=io.BytesIO(done.stdout), mode="r:") as archive:
            for member in archive:
                # Nothing but plain files with a name that was asked for
                if not member.isfile() or member.name not in wanted:
                    raise ClusterError("The server sent '%s', which was not asked for"
                                       % member.name[:80])
                source = archive.extractfile(member)
                if source is None:
                    continue
                path = target / member.name
                path.write_bytes(source.read())
                fetched.append(path)
    except tarfile.TarError as exc:
        raise ClusterError("What the server sent could not be read: %s" % exc) from exc
    return fetched


def cancel(settings: ClusterSettings, slurm_id: str, *, program: str = "ssh",
           run: Optional[Callable[..., Any]] = None) -> None:
    """!
    @brief Stop a job that waits or runs.
    @param settings The settings of the cluster.
    @param slurm_id The number that Slurm gave the job.
    @param program  The ssh program.
    @param run      The function that runs a command, subprocess.run() if None.
    @throws ClusterError if the server could not be asked or refused.
    """
    done = _run(settings, remote_command(["scancel", _check_id(slurm_id)]), program=program,
                run=run, timeout=COMMAND_TIMEOUT)
    if done.returncode != 0:
        raise ClusterError("The job could not be stopped on %s: %s"
                           % (settings.server, _text(done)))
