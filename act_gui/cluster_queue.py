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
@brief The jobs that were sent to the cluster of the user: how a
calculation becomes a job there, and what is kept about each job on this
machine.

A job on the cluster takes longer than the interface is open, so what is
known about it is kept in a file, in a directory of the job on this
machine. The results are fetched into the same directory.

The command for the cluster is made from the options of a calculation in
the same way as for this machine. The files it reads are sent along and
get a name in the directory of the job; no name of a file on this machine
goes to the cluster.

The cluster is asked as seldom as possible: one login tells the state of
all jobs, and not more often than the settings say.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from . import cluster, cluster_jobs, evaluation
from .cluster import ClusterError, ClusterSettings
from .jobs import InputFile, place_files

## The file with what is known about a job, in its directory on this machine
RECORD = "job.json"
## The option of a command for the file in which it reports its progress
PROGRESS = "progress"
## The states of a job on the cluster
WAITING, RUNNING, FINISHED, FAILED, LOST, CANCELLED = (
    "waiting", "running", "finished", "failed", "lost", "cancelled")
STATES = (WAITING, RUNNING, FINISHED, FAILED, LOST, CANCELLED)
## The states in which a job is not done yet
ACTIVE = (WAITING, RUNNING)
## Seconds that have to pass before the user can ask for the state again
MANUAL_SECONDS = 30
## The number of times in a row that Slurm must not know a job before it
## counts as lost: Slurm may not answer for a moment
LOST_AFTER = 2
## The number of characters of the output of a job that are kept as the
## reason why it failed
ERROR_TAIL = 2000
## The name of a file of a job, and the ending of the name of a file
_FILE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._+@#-]{0,199}$")
_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,10}$")


@dataclass(frozen=True)
class Plan:
    """!
    @brief A calculation as a job for the cluster, before it is sent.
    """
    ## The name of the job, which is the name of its directory
    name: str
    ## The name of the task and the command of alexandria
    task: str
    command: str
    ## The command on the cluster, the program first
    words: List[str]
    ## The script for Slurm
    script: str
    ## The files to send: for each name on the cluster the file on this machine
    files: Dict[str, Path]
    ## The files that the command writes, by option name
    outputs: Dict[str, List[str]]
    ## The files that were made for the job, by option name
    inputs: Dict[str, str]
    ## Files that the command writes without an option for them, by the
    ## name under which they are kept
    collect: Dict[str, str]
    ## The number of processes, the time limit in minutes and whether the
    ## job asks for a GPU
    tasks: int
    minutes: int
    gpu: bool
    ## The properties that a comparison compares, in the order they were chosen
    observables: Tuple[str, ...] = ()

    def describe(self, settings: ClusterSettings) -> Dict[str, Any]:
        """!
        @brief The job for the user to look at before it is sent.
        @param settings The settings of the cluster.
        @return The name, where the job goes, the command, the script and
        the files with their size.
        """
        return {"name": self.name, "task": self.task,
                "server": "%s@%s" % (settings.user, settings.server),
                "directory": "%s/%s" % (settings.workdir, self.name),
                "command": cluster.remote_command(self.words), "script": self.script,
                "files": [{"name": name, "size": path.stat().st_size}
                          for name, path in sorted(self.files.items())],
                "tasks": self.tasks, "minutes": self.minutes, "gpu": self.gpu,
                "fetch": self.fetched_names()}

    def fetched_names(self) -> List[str]:
        """!
        @brief The files that are fetched when the job is done, those that
        are there then.
        @return Their names in the directory of the job.
        """
        names = {name for listed in self.outputs.values() for name in listed}
        names.update(self.collect.values())
        names.add(cluster.OUTPUT_FILE)
        return sorted(names)


def make_plan(name: str, task: str, command: str, options: Mapping[str, Any],
              staging: Path, arguments: Callable[..., Tuple[List[str], Dict[str, List[Path]]]],
              settings: ClusterSettings, *,
              outputs: Optional[Mapping[str, str]] = None,
              inputs: Optional[Mapping[str, Sequence[InputFile]]] = None,
              collect: Optional[Mapping[str, str]] = None,
              tasks: int = 1, minutes: int = 60, gpu: bool = False,
              observables: Sequence[str] = ()) -> Plan:
    """!
    @brief Turn a calculation into a job for the cluster.
    @param name      The name of the job.
    @param task      The name of the task.
    @param command   The command of alexandria.
    @param options   The options of the command, as for a run on this
    machine: files are paths on this machine.
    @param staging   A directory on this machine that stands for the
    directory of the job. It has to exist; the input files that are made
    for the job are written there.
    @param arguments The function that makes the arguments of a command
    from its options and the directory it runs in, and tells which files
    the command writes: act.build_arguments() with the command looked up.
    @param settings  The settings of the cluster.
    @param outputs   Output files with a name of our choice, by option name.
    @param inputs    Input files that are made here, by option name.
    @param collect   Files that the command writes without an option.
    @param tasks     The number of processes.
    @param minutes   The longest time the job may take.
    @param gpu       Whether the job needs a GPU.
    @param observables The properties that a comparison compares.
    @return The plan.
    @throws ClusterError if the job cannot be made: a name, a number or a
    file that is not possible.
    """
    staging = Path(os.path.normpath(os.path.abspath(os.fspath(staging))))
    prefix = str(staging) + os.sep
    placed, written = place_files(staging, options, outputs, inputs)
    # The files on this machine that the command reads get a name in the
    # directory of the job, after the option they are for
    remote: Dict[str, str] = {}
    files: Dict[str, Path] = {}
    for key, value in placed.items():
        items = list(value) if isinstance(value, (list, tuple)) else [value]
        for index, item in enumerate(items):
            if not isinstance(item, os.PathLike):
                continue
            local = os.path.normpath(os.path.abspath(os.fspath(item)))
            if local.startswith(prefix):
                # In the directory of the job already: sent if it is there
                if os.path.isfile(local):
                    files[local[len(prefix):]] = Path(local)
                continue
            if local in remote:
                continue
            if not os.path.isfile(local):
                raise ClusterError("The file for option '%s' is not there any more" % key)
            suffix = Path(local).suffix
            sent = "%s%s%s" % (key, "" if len(items) == 1 else "-%d" % (index + 1),
                               suffix if _SUFFIX.fullmatch(suffix) else "")
            if sent in files:
                raise ClusterError("Two files of the job would be called %s" % sent)
            remote[local] = sent
            files[sent] = Path(local)
    args, expected = arguments(command, placed, staging)
    words = [settings.program, command]
    for arg in args:
        if arg in remote:
            words.append(remote[arg])
        elif arg.startswith(prefix):
            words.append(arg[len(prefix):])
        elif os.path.isabs(arg):
            # A file that would not be there on the cluster
            raise ClusterError("The job needs the file %s of this machine, which cannot "
                               "be sent" % arg)
        else:
            words.append(arg)
    produced: Dict[str, List[str]] = {}
    for key, paths in expected.items():
        names = [str(path)[len(prefix):] for path in paths if str(path).startswith(prefix)]
        if len(names) != len(paths):
            raise ClusterError("The file of option '%s' is not in the directory of the job"
                               % key)
        produced[key] = names
    kept = {key: Path(filename).name for key, filename in (collect or {}).items()}
    for filename in list(files) + [n for names in produced.values() for n in names] \
            + list(kept.values()):
        if not _FILE_NAME.fullmatch(filename):
            raise ClusterError("'%s' cannot be the name of a file of a job" % filename)
    script = cluster.batch_script(settings, name, words, tasks=tasks, minutes=minutes, gpu=gpu)
    return Plan(name=name, task=task, command=command, words=words, script=script,
                files=files, outputs=produced,
                inputs={key: path.name for key, path in written.items()},
                collect=kept, tasks=tasks, minutes=minutes, gpu=gpu,
                observables=tuple(observables))


def make_script_plan(name: str, task: str, words: Sequence[str], contents: Mapping[str, bytes],
                     fetch: Mapping[str, str], staging: Path, settings: ClusterSettings, *,
                     minutes: int = 60, gpu: bool = False) -> Plan:
    """!
    @brief A job for the cluster that runs another program than alexandria,
    such as a Python script for OpenMM, with files that the interface made.
    @param name     The name of the job.
    @param task     The name of the task.
    @param words    The command, a fixed one with the names of files of the job.
    @param contents The files of the job by name, as bytes.
    @param fetch    The files that the command writes, by the name under
    which they are kept, to fetch when the job is done.
    @param staging  The directory on this machine that stands for that of the job.
    @param settings The settings of the cluster.
    @param minutes  The longest time the job may take.
    @param gpu      Whether the job needs a GPU.
    @return The plan.
    @throws ClusterError for a name of a file that is not possible.
    """
    staging = Path(staging)
    files: Dict[str, Path] = {}
    for filename, data in contents.items():
        if not _FILE_NAME.fullmatch(filename):
            raise ClusterError("'%s' cannot be the name of a file of a job" % filename)
        path = staging / filename
        path.write_bytes(data)
        files[filename] = path
    for filename in fetch.values():
        if not _FILE_NAME.fullmatch(filename):
            raise ClusterError("'%s' cannot be the name of a file of a job" % filename)
    script = cluster.batch_script(settings, name, list(words), tasks=1, minutes=minutes, gpu=gpu)
    return Plan(name=name, task=task, command=words[0], words=list(words), script=script,
                files=files, outputs={}, inputs={}, collect=dict(fetch), tasks=1,
                minutes=minutes, gpu=gpu)


@dataclass
class ClusterJob:
    """!
    @brief A job that was sent to the cluster, and what is known about it.
    """
    ## The name of the job: the name of its directory, here and there
    name: str
    ## The name of the task and the command of alexandria
    task: str
    command: str
    ## The settings of the cluster with which the job was sent
    cluster: ClusterSettings
    ## The number that Slurm gave the job
    slurm: str
    ## waiting, running, finished, failed, lost or cancelled
    state: str = WAITING
    ## The state as Slurm named it when it was last asked
    slurm_state: str = ""
    ## The exit status of the program, if it ended
    exit: Optional[int] = None
    ## When the job was sent, when its state was last asked and when it
    ## was seen to have ended (seconds since the epoch)
    created: float = 0.0
    checked: float = 0.0
    ended: Optional[float] = None
    ## The number of processes, the time limit in minutes, whether it asked for a GPU
    tasks: int = 1
    minutes: int = 60
    gpu: bool = False
    ## The files that the command writes, by option name
    outputs: Dict[str, List[str]] = field(default_factory=dict)
    ## The files that were made for the job, by option name
    inputs: Dict[str, str] = field(default_factory=dict)
    ## Files that the command writes without an option, by the name under
    ## which they are kept
    collect: Dict[str, str] = field(default_factory=dict)
    ## Whether the results are on this machine
    fetched: bool = False
    ## What went wrong, for the user
    error: str = ""
    ## The number of times in a row that Slurm did not know the job
    missing: int = 0
    ## The properties that a comparison compares, in the order they were chosen
    observables: List[str] = field(default_factory=list)
    ## How far the job was when the cluster was last asked, for a job that
    ## reports its progress: what cluster_jobs.progress_summary() gives
    progress: Optional[Dict[str, Any]] = None
    ## The identifier of the job in the job engine, once its results are
    ## shown. Not kept in the file.
    job: str = ""

    @property
    def progress_file(self) -> str:
        """!
        @brief The file in which the job reports its progress.
        @return The name of the file in the directory of the job, empty if
        the job does not report its progress.
        """
        names = self.outputs.get(PROGRESS) or []
        return names[0] if names else ""

    def summary(self) -> Dict[str, Any]:
        """!
        @brief The job as it is sent to the browser.
        @return A dict that can be written as JSON, without the names of files.
        """
        return {"name": self.name, "task": self.task, "state": self.state,
                "slurm": self.slurm, "slurm_state": self.slurm_state, "exit": self.exit,
                "created": self.created, "checked": self.checked, "ended": self.ended,
                "tasks": self.tasks, "minutes": self.minutes, "gpu": self.gpu,
                "server": "%s@%s" % (self.cluster.user, self.cluster.server),
                "directory": "%s/%s" % (self.cluster.workdir, self.name),
                "fetched": self.fetched, "error": self.error, "job": self.job,
                "progress": self.progress}


def _names(given: Any, lists: bool) -> Dict[str, Any]:
    """!
    @brief Check the names of files in what was read from a file.
    @param given An object with for each option a name, or a list of names.
    @param lists Whether the values are lists.
    @return The object.
    @throws ValueError if it is something else.
    """
    if not isinstance(given, dict):
        raise ValueError("not an object")
    for key, value in given.items():
        names = value if lists else [value]
        if not isinstance(key, str) or not isinstance(names, list) \
                or not all(isinstance(n, str) and _FILE_NAME.fullmatch(n) for n in names):
            raise ValueError("not the name of a file")
    return dict(given)


def _from_record(content: Any) -> ClusterJob:
    """!
    @brief Make a job of what its file says. Everything is checked again:
    the name, the number and the settings become part of commands.
    @param content What was read from the file.
    @return The job.
    @throws ValueError, KeyError or TypeError if it is no job.
    """
    if not isinstance(content, dict):
        raise ValueError("not an object")
    name, slurm, state = content["name"], content["slurm"], content["state"]
    cluster_jobs.check_name(name)
    if not isinstance(slurm, str) or not re.fullmatch(r"^[0-9]{1,18}$", slurm):
        raise ValueError("no number of Slurm")
    if state not in STATES:
        raise ValueError("unknown state")
    for key in ("task", "command", "slurm_state", "error"):
        if not isinstance(content.get(key, ""), str):
            raise ValueError("%s is no text" % key)
    numbers = {}
    for key, default in (("created", 0.0), ("checked", 0.0)):
        numbers[key] = float(content.get(key, default))
    ended = content.get("ended")
    exit_status = content.get("exit")
    return ClusterJob(
        name=name, task=content["task"], command=content["command"],
        cluster=cluster.parse(content["cluster"]), slurm=slurm, state=state,
        slurm_state=content.get("slurm_state", ""),
        exit=None if exit_status is None else int(exit_status),
        ended=None if ended is None else float(ended),
        tasks=int(content.get("tasks", 1)), minutes=int(content.get("minutes", 60)),
        gpu=bool(content.get("gpu", False)),
        outputs=_names(content.get("outputs", {}), True),
        inputs=_names(content.get("inputs", {}), False),
        collect=_names(content.get("collect", {}), False),
        fetched=bool(content.get("fetched", False)), error=content.get("error", ""),
        missing=int(content.get("missing", 0)),
        observables=_observables(content.get("observables", [])),
        progress=cluster_jobs.progress_summary(content.get("progress")), **numbers)


def _observables(value: Any) -> List[str]:
    """!
    @brief The properties of a comparison as the file of a job has them.
    @param value What the file has.
    @return The names of the properties that are known, in their order.
    """
    if not isinstance(value, list):
        return []
    return [name for name in value if isinstance(name, str) and name in evaluation.OBSERVABLES]


class ClusterQueue:
    """!
    @brief The jobs on the cluster and their directories on this machine.
    """

    def __init__(self, directory: Path, *, program: str = "ssh",
                 run: Optional[Callable[..., Any]] = None,
                 clock: Callable[[], float] = time.time):
        """!
        @brief Read the jobs that were sent before.
        @param directory The directory in which each job has a directory.
        It is made when the first job is sent.
        @param program   The ssh program.
        @param run       The function that runs a command, subprocess.run() if None.
        @param clock     The function that tells the time.
        """
        self._dir = Path(directory)
        self._program = program
        self._run = run
        self._clock = clock
        self._lock = threading.Lock()
        # One question to the cluster at a time
        self._busy = threading.Lock()
        self._jobs: Dict[str, ClusterJob] = {}
        self._asked = 0.0
        # Why the cluster is not asked by itself any more, if so
        self._paused = ""
        ## The directories that have no job that can be read, by name
        self.unread: List[str] = []
        self._load()

    def _load(self) -> None:
        """!
        @brief Read the files of the jobs. What cannot be read is left alone.
        """
        if not self._dir.is_dir():
            return
        for directory in sorted(self._dir.iterdir()):
            record = directory / RECORD
            if not record.is_file():
                continue
            try:
                job = _from_record(json.loads(record.read_text(encoding="utf-8")))
                if job.name != directory.name:
                    raise ValueError("the name is not that of the directory")
            except (OSError, ValueError, KeyError, TypeError):
                self.unread.append(directory.name)
                continue
            self._jobs[job.name] = job
            self._asked = max(self._asked, job.checked)

    def _write(self, job: ClusterJob) -> None:
        """!
        @brief Keep what is known about a job in its file.
        @param job The job.
        @throws ClusterError if the file cannot be written.
        """
        content = asdict(job)
        content.pop("job")
        path = self._dir / job.name / RECORD
        try:
            partial = path.with_name(path.name + ".part")
            partial.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")
            os.replace(partial, path)
        except OSError as exc:
            raise ClusterError("The job cannot be noted in %s: %s" % (path, exc)) from exc

    @property
    def root(self) -> Path:
        """!
        @brief Where the jobs are kept on this machine.
        @return The directory in which each job has a directory.
        """
        return self._dir

    def output_files(self, name: str) -> Dict[str, Path]:
        """!
        @brief The output files of a job that were fetched: those that its
        command wrote and the output of Slurm, not the files that were
        sent along.
        @param name The name of the job.
        @return The files by their name.
        @throws ClusterError if there is no such job.
        """
        job = self.get(name)
        if job is None:
            raise ClusterError("There is no job %s" % name)
        names = {item for listed in job.outputs.values() for item in listed}
        names.update(job.collect.values())
        names.add(cluster.OUTPUT_FILE)
        directory = self._dir / job.name
        return {item: directory / item for item in sorted(names)
                if _FILE_NAME.fullmatch(item) and (directory / item).is_file()}

    def directory(self, name: str) -> Path:
        """!
        @brief The directory of a job on this machine.
        @param name The name of the job.
        @return The path.
        """
        return self._dir / name

    def get(self, name: str) -> Optional[ClusterJob]:
        """!
        @brief Look up a job.
        @param name The name.
        @return The job, None if there is none.
        """
        with self._lock:
            return self._jobs.get(name)

    def list(self) -> List[ClusterJob]:
        """!
        @brief All jobs.
        @return The jobs, the newest first.
        """
        with self._lock:
            return sorted(self._jobs.values(), key=lambda job: (job.created, job.name),
                          reverse=True)

    def summaries(self) -> List[Dict[str, Any]]:
        """!
        @brief All jobs for the browser.
        @return See ClusterJob.summary(), the newest first.
        """
        jobs = self.list()
        with self._lock:
            return [job.summary() for job in jobs]

    @property
    def paused(self) -> str:
        """!
        @brief Why the cluster is not asked by itself any more.
        @return The reason, empty if it is asked.
        """
        return self._paused

    def wait(self, poll_seconds: int, manual: bool = False) -> float:
        """!
        @brief How long until the cluster can be asked again.
        @param poll_seconds Seconds between two questions.
        @param manual       Whether it is the user who asks.
        @return The seconds, zero if it can be asked now.
        """
        floor = MANUAL_SECONDS if manual else poll_seconds
        return max(0.0, floor - (self._clock() - self._asked))

    def reserve(self, name: str) -> Path:
        """!
        @brief Make the directory of a new job on this machine.
        @param name The name of the job.
        @return The directory.
        @throws ClusterError if the name is not possible or taken.
        """
        directory = self._dir / cluster_jobs.check_name(name)
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            directory.mkdir()
        except FileExistsError as exc:
            raise ClusterError("There is a job with the name %s already, choose another name"
                               % name) from exc
        except OSError as exc:
            raise ClusterError("The directory %s cannot be made: %s" % (directory, exc)) from exc
        return directory

    def release(self, name: str) -> None:
        """!
        @brief Remove the directory of a job that was not sent after all.
        @param name The name of the job.
        """
        directory = self._dir / name
        if self.get(name) is None and not (directory / RECORD).exists():
            shutil.rmtree(directory, ignore_errors=True)

    def send(self, plan: Plan, settings: ClusterSettings) -> ClusterJob:
        """!
        @brief Send a job to the cluster, in one login, and note it.
        @param plan     The job, made with the directory from reserve().
        @param settings The settings of the cluster.
        @return The job.
        @throws ClusterError if it could not be sent.
        """
        slurm = cluster_jobs.submit(settings, plan.name, plan.script, plan.files,
                                    program=self._program, run=self._run)
        job = ClusterJob(name=plan.name, task=plan.task, command=plan.command,
                         cluster=settings, slurm=slurm, created=self._clock(),
                         tasks=plan.tasks, minutes=plan.minutes, gpu=plan.gpu,
                         outputs=dict(plan.outputs), inputs=dict(plan.inputs),
                         collect=dict(plan.collect), observables=list(plan.observables))
        with self._lock:
            self._jobs[job.name] = job
        try:
            # The script as it was sent, to look at later
            (self._dir / job.name / cluster_jobs.SCRIPT_NAME).write_text(plan.script,
                                                                         encoding="utf-8")
        except OSError:
            pass
        self._write(job)
        return job

    def check(self, poll_seconds: int, manual: bool = False) -> Dict[str, Any]:
        """!
        @brief Ask the cluster how the jobs are doing that are not done,
        with one login for each cluster, and fetch the results of those
        that ended. Nothing is asked if it was asked a short time ago.
        @param poll_seconds Seconds between two questions.
        @param manual       Whether it is the user who asks.
        @return An object with "asked", whether the cluster was asked;
        "wait", the seconds until it can be asked again; "problem", what
        went wrong, if anything; and "paused", whether the cluster is not
        asked by itself any more. That is so after a login that did not
        work, until the user asks: a cluster may block an account that
        keeps trying.
        """
        answer: Dict[str, Any] = {"asked": False, "wait": 0.0, "problem": "", "paused": False}
        if not self._busy.acquire(blocking=False):
            # Another request is asking right now
            return answer
        try:
            with self._lock:
                active = [job for job in self._jobs.values() if job.state in ACTIVE]
            if not active:
                self._paused = ""
                return answer
            if self._paused and not manual:
                answer.update(problem=self._paused, paused=True)
                return answer
            answer["wait"] = self.wait(poll_seconds, manual)
            if answer["wait"] > 0:
                answer.update(problem=self._paused, paused=bool(self._paused))
                return answer
            self._paused = ""
            self._asked = self._clock()
            answer["asked"] = True
            answer["wait"] = float(poll_seconds)
            groups: Dict[ClusterSettings, List[ClusterJob]] = {}
            for job in active:
                groups.setdefault(job.cluster, []).append(job)
            problems = []
            for settings, jobs in groups.items():
                try:
                    states = cluster_jobs.status_all(
                        settings, [(job.name, job.slurm, job.progress_file)
                                   for job in jobs],
                        program=self._program, run=self._run)
                except ClusterError as exc:
                    problems.append(str(exc))
                    self._paused = str(exc)
                    continue
                for job in jobs:
                    problem = self._update(job, states[job.name])
                    if problem:
                        problems.append(problem)
            answer.update(problem=" ".join(problems), paused=bool(self._paused))
            return answer
        finally:
            self._busy.release()

    def _update(self, job: ClusterJob, said: Mapping[str, Any]) -> str:
        """!
        @brief Note what the cluster said about a job, and fetch its
        results if it ended.
        @param job  The job.
        @param said What cluster_jobs.status_all() gave for it.
        @return What went wrong, empty if nothing.
        """
        now = self._clock()
        state = said["state"]
        with self._lock:
            job.checked = now
            job.slurm_state = said.get("slurm", "")
            if state == LOST:
                job.missing += 1
                if job.missing < LOST_AFTER:
                    # Slurm may not have answered: ask once more
                    state = job.state
            else:
                job.missing = 0
            job.state = state
            if "exit" in said:
                job.exit = int(said["exit"])
            if said.get("progress") is not None:
                job.progress = said["progress"]
            if state not in ACTIVE:
                job.ended = now
        problem = ""
        if state not in ACTIVE:
            problem = self._fetch(job)
        try:
            self._write(job)
        except ClusterError as exc:
            problem = problem or str(exc)
        return problem

    def _fetch(self, job: ClusterJob) -> str:
        """!
        @brief Fetch the files of a job that ended, in one login, and note
        why it failed if it did.
        @param job The job.
        @return What went wrong with the fetching, empty if nothing.
        """
        names = [name for listed in job.outputs.values() for name in listed]
        names += [name for name in job.collect.values() if name not in names]
        names.append(cluster.OUTPUT_FILE)
        directory = self._dir / job.name
        try:
            cluster_jobs.fetch(job.cluster, job.name, names, directory,
                               program=self._program, run=self._run)
        except ClusterError as exc:
            with self._lock:
                job.fetched = False
                job.error = "The results could not be fetched. %s" % exc
            return job.error
        said = ""
        try:
            said = (directory / cluster.OUTPUT_FILE).read_text(
                encoding="utf-8", errors="replace").strip()[-ERROR_TAIL:]
        except OSError:
            pass
        with self._lock:
            job.fetched = True
            job.error = ""
            if job.state == FAILED:
                job.error = "The program ended with status %s. %s" % (job.exit, said)
            elif job.state == LOST:
                job.error = ("The job ended without a result: it was stopped, or it took "
                             "longer than the %d minutes it asked for. %s" % (job.minutes, said))
        return ""

    def fetch(self, name: str) -> ClusterJob:
        """!
        @brief Fetch the results of a job that ended once more, for a job
        of which they could not be fetched before.
        @param name The name of the job.
        @return The job.
        @throws ClusterError if there is no such job, if it is not done or
        if the results could not be fetched.
        """
        job = self.get(name)
        if job is None:
            raise ClusterError("There is no job %s" % name)
        if job.state in ACTIVE or job.state == CANCELLED:
            raise ClusterError("The job %s has no results" % name)
        with self._busy:
            problem = self._fetch(job)
            self._write(job)
        if problem:
            raise ClusterError(problem)
        return job

    def results(self, name: str) -> Tuple[Any, Dict[str, Path], Dict[str, Path]]:
        """!
        @brief The results of a job that finished, from the files that
        were fetched.
        @param name The name of the job.
        @return What the command wrote in JSON format, without the outer
        key; the files of the job by option name; and those of the files
        that were made for the job.
        @throws ClusterError if there is no such job, if it did not finish
        or if its results cannot be read.
        """
        job = self.get(name)
        if job is None:
            raise ClusterError("There is no job %s" % name)
        if job.state != FINISHED or not job.fetched:
            raise ClusterError("The job %s has no results on this machine" % name)
        directory = self._dir / job.name
        inputs = {key: directory / filename for key, filename in job.inputs.items()
                  if (directory / filename).is_file()}
        files = dict(inputs)
        for key, names in job.outputs.items():
            if names and (directory / names[0]).is_file():
                files[key] = directory / names[0]
        for key, filename in job.collect.items():
            if (directory / filename).is_file():
                files[key] = directory / filename
        data = None
        if "json" in files:
            try:
                data = json.loads(files["json"].read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ClusterError("The results of the job %s cannot be read: %s"
                                   % (name, exc)) from exc
            # As act does: the file has one key, the name of the command
            if isinstance(data, dict) and len(data) == 1:
                data = next(iter(data.values()))
        return data, files, inputs

    def progress_reports(self, name: str, limit: int) -> List[Any]:
        """!
        @brief The progress of a job as it reported it, from the file that
        was fetched with its results.
        @param name  The name of the job.
        @param limit The largest number of reports.
        @return The lines of the file that are objects, the first ones if
        there are more than the limit. Empty if the job did not report.
        """
        job = self.get(name)
        if job is None or not job.progress_file:
            return []
        reports: List[Any] = []
        try:
            with open(self._dir / job.name / job.progress_file, encoding="utf-8",
                      errors="replace") as lines:
                for line in lines:
                    if len(reports) >= limit:
                        break
                    try:
                        report = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(report, dict):
                        reports.append(report)
        except OSError:
            return []
        return reports

    def adopted(self, name: str, job_id: str) -> None:
        """!
        @brief Note that the results of a job are in the job engine.
        @param name   The name of the job.
        @param job_id The identifier in the job engine.
        """
        with self._lock:
            if name in self._jobs:
                self._jobs[name].job = job_id

    def cancel(self, name: str) -> ClusterJob:
        """!
        @brief Stop a job that waits or runs, in one login.
        @param name The name of the job.
        @return The job.
        @throws ClusterError if there is no such job, if it is done or if
        the cluster could not be asked.
        """
        job = self.get(name)
        if job is None:
            raise ClusterError("There is no job %s" % name)
        if job.state not in ACTIVE:
            raise ClusterError("The job %s is not waiting or running" % name)
        with self._busy:
            cluster_jobs.cancel(job.cluster, job.slurm, program=self._program, run=self._run)
            with self._lock:
                job.state = CANCELLED
                job.ended = self._clock()
            self._write(job)
        return job

    def forget(self, name: str) -> None:
        """!
        @brief Remove a job that is done from the list, with its files on
        this machine. What is on the cluster stays there.
        @param name The name of the job.
        @throws ClusterError if there is no such job or if it is not done.
        """
        job = self.get(name)
        if job is None:
            raise ClusterError("There is no job %s" % name)
        if job.state in ACTIVE:
            raise ClusterError("The job %s is not done, stop it first" % name)
        with self._lock:
            self._jobs.pop(name, None)
        shutil.rmtree(self._dir / name, ignore_errors=True)
