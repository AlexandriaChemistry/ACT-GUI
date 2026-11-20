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
@brief The job engine: starts calculations, follows them, stops them and
keeps their results.

Each job runs a command of alexandria in a directory of its own, through
the function act.run(). A few jobs run at the same time, the others wait.
The engine talks to a function with the signature of act.run(), such that
it can be tested without the program and such that another back end, for
instance Slurm, can take its place.
"""
from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

## The states a job can be in
QUEUED, RUNNING, FINISHED, FAILED, CANCELLED = (
    "queued", "running", "finished", "failed", "cancelled")

## The states in which a job is not done yet
ACTIVE = (QUEUED, RUNNING)

## The options of commands that take a list of files, also if there is one
MULTIPLE = ("vs", "mp")


class JobError(RuntimeError):
    """!
    @brief A job that cannot be started. The message is for the user.
    """


@dataclass(frozen=True)
class InputFile:
    """!
    @brief A file that the server makes for a job, in the directory of the
    job. The name and the content come from the server, not from the user.
    """
    ## The name of the file, without a directory
    name: str
    ## The content
    text: str


def place_files(workdir: Path, options: Mapping[str, Any],
                outputs: Optional[Mapping[str, str]] = None,
                inputs: Optional[Mapping[str, Sequence[InputFile]]] = None
                ) -> Tuple[Dict[str, Any], Dict[str, Path]]:
    """!
    @brief Give the files of a job their place in its directory: the
    output files with a name of our choice, and the input files that are
    made here, which are written.
    @param workdir The directory of the job. It is made if there are input files.
    @param options The options of the command.
    @param outputs Output files by option name, see JobManager.submit().
    @param inputs  Input files by option name, see JobManager.submit().
    @return The options with the files, and the first input file that was
    written for each option.
    """
    options = dict(options)
    written: Dict[str, Path] = {}
    for name, filename in (outputs or {}).items():
        # Only a file name, in the directory of the job
        options[name] = workdir / Path(filename).name
    if inputs:
        workdir.mkdir(parents=True, exist_ok=True)
        for name, files in inputs.items():
            paths = []
            for item in files:
                path = workdir / Path(item.name).name
                path.write_text(item.text, encoding="utf-8")
                paths.append(path)
            # One file for an option that takes one file
            options[name] = paths[0] if len(paths) == 1 and name not in MULTIPLE else paths
            written[name] = paths[0]
    return options, written


@dataclass
class Job:
    """!
    @brief A calculation and what is known about it.
    """
    ## The identifier
    id: str
    ## The name of the task
    task: str
    ## The command of alexandria
    command: str
    ## The directory the command runs in
    workdir: Path
    ## queued, running, finished, failed or cancelled
    status: str = QUEUED
    ## When the job was made, started and ended (seconds since the epoch)
    created: float = field(default_factory=time.time)
    started: Optional[float] = None
    ended: Optional[float] = None
    ## The results of the command in JSON format
    result: Any = None
    ## What went wrong, for the user
    error: str = ""
    ## The reports of the progress of the command
    progress: List[Any] = field(default_factory=list)
    ## The output files by option name, with the input files that were
    ## made for the job
    files: Dict[str, Path] = field(default_factory=dict)
    ## The input files that were made for the job, by option name
    inputs: Dict[str, Path] = field(default_factory=dict)
    ## Set to stop the job
    stop: threading.Event = field(default_factory=threading.Event)

    def summary(self, progress_from: int = 0) -> Dict[str, Any]:
        """!
        @brief The job as it is sent to the browser.
        @param progress_from The number of progress reports the browser
        already has; only the later ones are sent.
        @return A dict that can be written as JSON. Paths are not in it:
        files are referred to by their option name.
        """
        return {
            "id": self.id, "task": self.task, "status": self.status,
            "created": self.created, "started": self.started, "ended": self.ended,
            "error": self.error,
            "result": self.result if self.status == FINISHED else None,
            "progress": self.progress[progress_from:],
            "progress_total": len(self.progress),
            "files": sorted(self.files),
        }


class JobManager:
    """!
    @brief Starts jobs and keeps track of them.
    """

    def __init__(self, jobs_dir: Path, run: Callable[..., Any], *,
                 max_workers: int = 2, max_active: int = 8,
                 timeout: Optional[float] = None, max_progress: int = 5000):
        """!
        @brief Make the engine.
        @param jobs_dir     The directory in which each job gets a directory.
        @param run          The function that runs a command, act.run().
        @param max_workers  The number of jobs that run at the same time.
        @param max_active   The number of jobs that can wait or run.
        @param timeout      Seconds after which a job is stopped.
        @param max_progress The number of progress reports kept for a job.
        """
        self._dir = Path(jobs_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._run = run
        self._timeout = timeout
        self._max_active = max_active
        self._max_progress = max_progress
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers,
                                        thread_name_prefix="act-job")

    def submit(self, task: str, command: str, options: Dict[str, Any],
               progress: bool = False,
               outputs: Optional[Dict[str, str]] = None,
               inputs: Optional[Mapping[str, Sequence[InputFile]]] = None,
               collect: Optional[Mapping[str, str]] = None,
               digest: Optional[Callable[[Any], Any]] = None) -> Job:
        """!
        @brief Start a job, or put it in the queue.
        @param task     The name of the task, for the browser.
        @param command  The command of alexandria.
        @param options  The options of the command.
        @param progress Whether to follow the progress of the command.
        @param outputs  Output files with a name of our choice, by option
        name. They are written in the directory of the job.
        @param inputs   Input files that are made here, by option name: one
        file for an option that takes one, a list of files otherwise. They
        are written in the directory of the job before it starts.
        @param collect  Files that the command writes without an option for
        them, by the name under which they are kept with the output files:
        file names in the directory of the job. Those that exist are kept.
        @param digest   A function that makes what is kept of the result of
        the command, for results that are too large to send to the browser.
        @return The job.
        @throws JobError if too many jobs are waiting or running.
        """
        with self._lock:
            active = sum(1 for job in self._jobs.values() if job.status in ACTIVE)
            if active >= self._max_active:
                raise JobError("There are %d calculations waiting or running, "
                               "wait until one is done" % active)
            job_id = uuid.uuid4().hex
            job = Job(id=job_id, task=task, command=command, workdir=self._dir / job_id)
            self._jobs[job_id] = job
        options, job.inputs = place_files(job.workdir, options, outputs, inputs)
        self._pool.submit(self._execute, job, options, progress, dict(collect or {}), digest)
        return job

    def _execute(self, job: Job, options: Dict[str, Any], progress: bool,
                 collect: Optional[Mapping[str, str]] = None,
                 digest: Optional[Callable[[Any], Any]] = None) -> None:
        """!
        @brief Run a job. Called in a thread of the pool.
        @param job      The job.
        @param options  The options of the command.
        @param progress Whether to follow the progress.
        @param collect  Files to keep that the command writes unasked, see submit().
        @param digest   The function that makes what is kept of the result.
        """
        if job.stop.is_set():
            self._finish(job, CANCELLED)
            return
        with self._lock:
            job.status = RUNNING
            job.started = time.time()

        def on_progress(item: Any) -> None:
            """!
            @brief Keep a report of the progress.
            @param item The report.
            """
            with self._lock:
                if len(job.progress) < self._max_progress:
                    job.progress.append(item)

        try:
            extra: Dict[str, Any] = {"workdir": job.workdir, "timeout": self._timeout,
                                     "stop": job.stop}
            if progress:
                extra["on_progress"] = on_progress
            result = self._run(job.command, **extra, **options)
            data = digest(result.data) if digest is not None else result.data
            # The files that were made for the job, then those it wrote
            files = dict(job.inputs)
            files.update({name: paths[0] for name, paths in result.files.items() if paths})
            for name, filename in (collect or {}).items():
                path = job.workdir / Path(filename).name
                if path.is_file():
                    files[name] = path
            with self._lock:
                job.result = data
                job.files = files
            self._finish(job, FINISHED)
        except Exception as exc:  # pylint: disable=broad-except
            # Whatever goes wrong ends this job, not the server
            cancelled = job.stop.is_set() or type(exc).__name__ == "ActCancelledError"
            with self._lock:
                job.error = "" if cancelled else str(exc)
            self._finish(job, CANCELLED if cancelled else FAILED)

    def adopt(self, task: str, command: str, workdir: Path, result: Any,
              files: Mapping[str, Path], *, inputs: Optional[Mapping[str, Path]] = None,
              created: Optional[float] = None, ended: Optional[float] = None) -> Job:
        """!
        @brief Take in a job that ran elsewhere, such as on a cluster, and
        of which the results are on this machine. From here on it is a
        finished job as any other.
        @param task    The name of the task.
        @param command The command of alexandria.
        @param workdir The directory with the results.
        @param result  The results of the command, as they are kept.
        @param files   The files of the job by option name.
        @param inputs  Those of the files that were made for the job.
        @param created When the job was made, now if None.
        @param ended   When it ended, now if None.
        @return The job.
        """
        now = time.time()
        job = Job(id=uuid.uuid4().hex, task=task, command=command, workdir=Path(workdir),
                  status=FINISHED, created=now if created is None else created,
                  ended=now if ended is None else ended, result=result,
                  files=dict(files), inputs=dict(inputs or {}))
        with self._lock:
            self._jobs[job.id] = job
        return job

    def _finish(self, job: Job, status: str) -> None:
        """!
        @brief Mark a job as done.
        @param job    The job.
        @param status The final state.
        """
        with self._lock:
            job.status = status
            job.ended = time.time()

    def get(self, job_id: str) -> Optional[Job]:
        """!
        @brief Look up a job.
        @param job_id The identifier.
        @return The job, None if there is none.
        """
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> List[Job]:
        """!
        @brief All jobs.
        @return The jobs, the newest first.
        """
        with self._lock:
            return sorted(self._jobs.values(), key=lambda job: job.created, reverse=True)

    def cancel(self, job_id: str) -> bool:
        """!
        @brief Stop a job that waits or runs.
        @param job_id The identifier.
        @return Whether there is such a job. A job that is done stays as it is.
        """
        job = self.get(job_id)
        if job is None:
            return False
        job.stop.set()
        return True

    def summary(self, job: Job, progress_from: int = 0) -> Dict[str, Any]:
        """!
        @brief The state of a job for the browser, read under the lock.
        @param job           The job.
        @param progress_from The number of progress reports already sent.
        @return See Job.summary().
        """
        with self._lock:
            return job.summary(progress_from)

    def shutdown(self) -> None:
        """!
        @brief Stop all jobs and the threads.
        """
        with self._lock:
            for job in self._jobs.values():
                job.stop.set()
        self._pool.shutdown(wait=True)
