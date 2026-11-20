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
@brief The part of the interface (API) for jobs on the cluster of the user:
showing a job before it is sent, sending it, following it, stopping it and
handing its results to the job engine.

A request for a job on the cluster is the request for the same calculation
on this machine, with the name of the job, the number of processes, the
time limit and whether a GPU is needed. It goes through the same checks.

The functions that log in are no async functions, or they run in a thread,
so that the server does other things while ssh waits.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from fastapi import FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from . import cluster_jobs
from .cluster import MAX_TASKS, ClusterError, ClusterSettings
from .cluster_queue import ACTIVE, PROGRESS, ClusterQueue, Plan, make_plan
from .jobs import JobManager
from .tasks import TASKS, Prepared, Task

## What a request can say about the job itself
CHOICES = ("name", "tasks", "minutes", "gpu")
## The largest number of jobs that wait or run on the cluster
MAX_ACTIVE = 50
## The largest number of reports of its progress that are read of a job
MAX_REPORTS = 5000


def register(app: FastAPI, manager: JobManager, queue: ClusterQueue,
             prepare: Callable[..., Tuple[Task, Prepared]],
             arguments: Callable[..., Any],
             fail: Callable[[int, str], HTTPException]) -> None:
    """!
    @brief Add the routes for jobs on the cluster to the application.
    @param app       The application. Its state has the settings of the cluster.
    @param manager   The job engine, which takes in the results.
    @param queue     The jobs on the cluster.
    @param prepare   The function that checks a request for a calculation
    and gives the task and the options of its command, for the request and
    the number of processes of the job. It raises the answer for a request
    that is not accepted.
    @param arguments The function that makes the arguments of a command,
    see cluster_queue.make_plan().
    @param fail      The function that makes the answer for an error.
    """

    def poll_seconds() -> int:
        """!
        @brief The time between two questions to the cluster.
        @return The seconds of the settings, those that are proposed if
        there are no settings.
        """
        current = app.state.cluster
        return (current or ClusterSettings("", "", "")).poll_seconds

    def listing(**more: Any) -> Dict[str, Any]:
        """!
        @brief The jobs for the browser.
        @param more What else to tell.
        @return The jobs with the title of their task, the time between
        two questions, the seconds until the cluster can be asked, why it
        is not asked by itself if so, and the directories that are no job.
        """
        jobs: List[Dict[str, Any]] = []
        for summary in queue.summaries():
            task = TASKS.get(summary["task"])
            jobs.append(dict(summary, title=task.title if task else summary["task"]))
        answer = {"jobs": jobs, "poll_seconds": poll_seconds(),
                  "wait": queue.wait(poll_seconds()), "paused": queue.paused,
                  "unread": list(queue.unread), "directory": str(queue.root)}
        answer.update(more)
        return answer

    def plan_for(body: Any, staging: Path, name: str) -> Plan:
        """!
        @brief Make the job for a request.
        @param body    The request.
        @param staging The directory that stands for that of the job.
        @param name    The name of the job.
        @return The plan.
        """
        chosen = body.get("cluster") or {}
        tasks = chosen.get("tasks", 1)
        if isinstance(tasks, bool) or not isinstance(tasks, int) or not 1 <= tasks <= MAX_TASKS:
            raise fail(400, "The number of processes is between 1 and %d" % MAX_TASKS)
        # A command with an individual for each process gets as many as the job has
        task, prepared = prepare(body, tasks)
        if not task.cluster:
            raise fail(400, "%s runs on this computer only" % task.title)
        options = dict(prepared.options)
        if task.progress:
            # In a file in the directory of the job, which is read when
            # the cluster is asked for the state of the job
            options[PROGRESS] = True
        try:
            return make_plan(name, task.name, task.command, options, staging,
                             arguments, app.state.cluster, outputs=dict(task.outputs),
                             inputs=prepared.inputs, collect=dict(task.collect),
                             tasks=tasks, minutes=chosen.get("minutes", 60),
                             gpu=chosen.get("gpu", False))
        except (ValueError, OSError) as exc:
            # Also an option that the command does not take
            raise fail(400, str(exc)) from exc

    def handle(body: Any) -> Dict[str, Any]:
        """!
        @brief Show a job, or send it.
        @param body The request.
        @return The job as it would be sent, or as it was sent.
        """
        if not isinstance(body, dict):
            raise fail(400, "The request must be an object")
        if app.state.cluster is None:
            raise fail(400, "Give the server, your user name and a working directory in the "
                       "tab Cluster first")
        chosen = body.get("cluster") or {}
        if not isinstance(chosen, dict) or any(key not in CHOICES for key in chosen):
            raise fail(400, "A job has a name, a number of processes (tasks), a time in "
                       "minutes and a GPU or not")
        if not isinstance(chosen.get("gpu", False), bool):
            raise fail(400, "Whether the job needs a GPU is true or false")
        send = body.get("send", False)
        if not isinstance(send, bool):
            raise fail(400, "Whether the job is sent is true or false")
        task_name = body.get("task") if isinstance(body.get("task"), str) else "job"
        name = chosen.get("name") or "%s-%s" % (task_name, time.strftime("%Y%m%d-%H%M%S"))
        try:
            cluster_jobs.check_name(name)
        except ClusterError as exc:
            raise fail(400, "%s: use letters, digits and . _ -, and no spaces" % exc) from exc
        current = app.state.cluster
        if not send:
            with tempfile.TemporaryDirectory(prefix="act-gui-job-") as tmp:
                staging = Path(tmp) / name
                staging.mkdir()
                taken = queue.get(name) is not None or queue.directory(name).exists()
                return dict(plan_for(body, staging, name).describe(current), sent=False,
                            taken=taken)
        if sum(1 for job in queue.list() if job.state in ACTIVE) >= MAX_ACTIVE:
            raise fail(429, "There are %d jobs waiting or running on the cluster, wait until "
                       "one is done" % MAX_ACTIVE)
        try:
            staging = queue.reserve(name)
        except ClusterError as exc:
            raise fail(409, str(exc)) from exc
        try:
            job = queue.send(plan_for(body, staging, name), current)
        except ClusterError as exc:
            queue.release(name)
            raise fail(502, str(exc)) from exc
        except BaseException:
            queue.release(name)
            raise
        return listing(sent=True, name=job.name, slurm=job.slurm)

    @app.get("/api/cluster/jobs")
    async def cluster_jobs_list() -> Dict[str, Any]:
        """!
        @brief The jobs on the cluster, as they were when the cluster was
        last asked. Nobody logs in for this.
        @return See listing().
        """
        return listing()

    @app.post("/api/cluster/jobs")
    async def cluster_job(request: Request) -> Dict[str, Any]:
        """!
        @brief Show the job that a calculation would be on the cluster, or
        send it: one login.
        @param request The request, with a JSON object: what the task
        needs, as for a calculation on this machine; "cluster" with the
        "name" of the job, the number of processes "tasks", the time limit
        in "minutes" and "gpu"; and "send", true to send the job.
        @return Without "send" the job to look at: the script, the command,
        the files with their size and where it goes. With it the jobs, see
        listing(), and the name and the number of the new one.
        """
        try:
            body = await request.json()
        except ValueError as exc:
            raise fail(400, "The request is not valid JSON") from exc
        return await run_in_threadpool(handle, body)

    @app.post("/api/cluster/jobs/check")
    def cluster_check(manual: bool = False) -> Dict[str, Any]:
        """!
        @brief Ask the cluster how the jobs are doing: one login for all,
        and one for each job that ended, to fetch its results. Nothing is
        asked if it was asked a short time ago.
        @param manual Whether it is the user who asks, who can ask sooner.
        @return The jobs, see listing(), with "asked" and "problem".
        """
        answer = queue.check(poll_seconds(), manual=manual)
        return listing(asked=answer["asked"], problem=answer["problem"],
                       wait=answer["wait"] if not answer["asked"] and answer["wait"] > 0
                       else queue.wait(poll_seconds()))

    @app.post("/api/cluster/jobs/{name}/fetch")
    def cluster_fetch(name: str) -> Dict[str, Any]:
        """!
        @brief Fetch the results of a job that ended once more: one login.
        @param name The name of the job.
        @return The jobs, see listing().
        """
        if queue.get(name) is None:
            raise fail(404, "There is no such job")
        try:
            queue.fetch(name)
        except ClusterError as exc:
            raise fail(502, str(exc)) from exc
        return listing()

    @app.post("/api/cluster/jobs/{name}/open")
    def cluster_open(name: str) -> Dict[str, Any]:
        """!
        @brief Hand the results of a job that finished to the job engine,
        so that they are shown as those of a calculation on this machine.
        @param name The name of the job.
        @return The identifier of the job in the job engine, and its task.
        """
        job = queue.get(name)
        if job is None:
            raise fail(404, "There is no such job")
        if job.job and manager.get(job.job) is not None:
            return {"job": job.job, "task": job.task}
        task = TASKS.get(job.task)
        if task is None:
            raise fail(400, "This version of the interface does not know the calculation "
                       "%s" % job.task)
        try:
            data, files, inputs = queue.results(name)
        except ClusterError as exc:
            raise fail(400, str(exc)) from exc
        if task.digest is not None:
            data = task.digest(data)
        adopted = manager.adopt(task.name, task.command, queue.directory(name), data, files,
                                inputs=inputs, created=job.created, ended=job.ended,
                                progress=queue.progress_reports(name, MAX_REPORTS))
        queue.adopted(name, adopted.id)
        return {"job": adopted.id, "task": task.name}

    @app.delete("/api/cluster/jobs/{name}")
    def cluster_stop(name: str, forget: bool = False) -> Dict[str, Any]:
        """!
        @brief Stop a job that waits or runs: one login. Or take a job
        that is done from the list, with its results on this machine; what
        is on the cluster stays there.
        @param name   The name of the job.
        @param forget Whether to take the job from the list.
        @return The jobs, see listing().
        """
        job = queue.get(name)
        if job is None:
            raise fail(404, "There is no such job")
        active = job.state in ACTIVE
        if forget and active:
            raise fail(409, "The job %s is not done, stop it first" % name)
        if not forget and not active:
            raise fail(409, "The job %s is not waiting or running" % name)
        try:
            if forget:
                queue.forget(name)
            else:
                queue.cancel(name)
        except ClusterError as exc:
            raise fail(502, str(exc)) from exc
        return listing()
