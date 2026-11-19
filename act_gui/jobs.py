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
from typing import Any, Callable, Dict, List, Optional

## The states a job can be in
QUEUED, RUNNING, FINISHED, FAILED, CANCELLED = (
    "queued", "running", "finished", "failed", "cancelled")

## The states in which a job is not done yet
ACTIVE = (QUEUED, RUNNING)


class JobError(RuntimeError):
    """!
    @brief A job that cannot be started. The message is for the user.
    """


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
    ## The output files by option name
    files: Dict[str, Path] = field(default_factory=dict)
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
               outputs: Optional[Dict[str, str]] = None) -> Job:
        """!
        @brief Start a job, or put it in the queue.
        @param task     The name of the task, for the browser.
        @param command  The command of alexandria.
        @param options  The options of the command.
        @param progress Whether to follow the progress of the command.
        @param outputs  Output files with a name of our choice, by option
        name. They are written in the directory of the job.
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
        options = dict(options)
        for name, filename in (outputs or {}).items():
            # Only a file name, in the directory of the job
            options[name] = job.workdir / Path(filename).name
        self._pool.submit(self._execute, job, options, progress)
        return job

    def _execute(self, job: Job, options: Dict[str, Any], progress: bool) -> None:
        """!
        @brief Run a job. Called in a thread of the pool.
        @param job      The job.
        @param options  The options of the command.
        @param progress Whether to follow the progress.
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
            with self._lock:
                job.result = result.data
                job.files = {name: paths[0] for name, paths in result.files.items() if paths}
            self._finish(job, FINISHED)
        except Exception as exc:  # pylint: disable=broad-except
            # Whatever goes wrong ends this job, not the server
            cancelled = job.stop.is_set() or type(exc).__name__ == "ActCancelledError"
            with self._lock:
                job.error = "" if cancelled else str(exc)
            self._finish(job, CANCELLED if cancelled else FAILED)

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
