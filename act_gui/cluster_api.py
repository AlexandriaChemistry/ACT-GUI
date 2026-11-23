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

import dataclasses
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from . import cluster, cluster_jobs, openmm_package, training
from .cluster import MAX_TASKS, ClusterError, ClusterSettings
from .cluster_queue import (ACTIVE, PROGRESS, ClusterJob, ClusterQueue, Plan, make_plan,
                            make_script_plan)
from .jobs import JobManager
from .tasks import OPENMM_RESULTS, TASKS, Prepared, Task

## What a request can say about the job itself
CHOICES = ("name", "tasks", "minutes", "gpu")
## The largest number of jobs that wait or run on the cluster
MAX_ACTIVE = 50
## The largest number of reports of its progress that are read of a job
MAX_REPORTS = 5000
## The largest part of a file of a job that is shown on the page, its end
MAX_SHOWN_BYTES = 1024 ** 2
## What is left out of a request when it is kept with its job
_NOT_KEPT = ("cluster", "send")


def _replace_value(words: List[str], option: str, value: str) -> bool:
    """!
    @brief Give an option of a command another value.
    @param words  The command, changed in place.
    @param option The option, with its dash.
    @param value  The new value.
    @return Whether the command has the option.
    """
    if option not in words[:-1]:
        return False
    words[words.index(option) + 1] = value
    return True


def next_name(name: str, taken: Callable[[str], bool]) -> str:
    """!
    @brief The name of a job that is sent again: the name with -r2, -r3 and
    so on, the first that is not taken.
    @param name  The name of the job that is sent again.
    @param taken Whether a name is taken.
    @return The name.
    """
    match = re.fullmatch(r"(.+)-r(\d+)", name)
    base, number = (match.group(1), int(match.group(2))) if match else (name, 1)
    while True:
        number += 1
        suffix = "-r%d" % number
        candidate = base[:64 - len(suffix)] + suffix
        if not taken(candidate):
            return candidate


def register(app: FastAPI, manager: JobManager, queue: ClusterQueue,
             prepare: Callable[..., Tuple[Task, Prepared]],
             arguments: Callable[..., Any],
             fail: Callable[[int, str], HTTPException],
             written: Optional[Callable[[str], Dict[str, str]]] = None) -> None:
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
    @param written   The function that tells which files a command writes
    under their default names when their option is not given, by option
    name; they are fetched as well. None for none.
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
        @return The jobs with the title of their task and "auto", whether
        their results are shown as soon as they are done, the time between
        two questions, the seconds until the cluster can be asked, why it
        is not asked by itself if so, and the directories that are no job.
        """
        jobs: List[Dict[str, Any]] = []
        for summary in queue.summaries():
            task = TASKS.get(summary["task"])
            jobs.append(dict(summary, title=task.title if task else summary["task"],
                             auto=bool(task and task.deliver)))
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
        if isinstance(body, Mapping) and body.get("task") == "openmm_run":
            return openmm_plan(body, staging, name, chosen)
        tasks = chosen.get("tasks", 1)
        if isinstance(tasks, bool) or not isinstance(tasks, int) or not 1 <= tasks <= MAX_TASKS:
            raise fail(400, "The number of processes is between 1 and %d" % MAX_TASKS)
        # A command with an individual for each process gets as many as the
        # job has, and a training the time of the job to stop in
        minutes = chosen.get("minutes", 60)
        timed = isinstance(minutes, int) and not isinstance(minutes, bool) and minutes > 0
        task, prepared = prepare(body, tasks, 60.0 * minutes if timed else None)
        if not task.cluster:
            raise fail(400, "%s runs on this computer only" % task.title)
        options = dict(prepared.options)
        if task.progress:
            # In a file in the directory of the job, which is read when
            # the cluster is asked for the state of the job
            options[PROGRESS] = True
        # Also the files that the command writes without being asked, such
        # as the log file of a training, unless the job names them otherwise
        collect = dict(task.collect)
        named = set(collect.values()) | set(task.outputs.values())
        for key, filename in (written(task.command) if written else {}).items():
            if key not in options and key not in task.outputs and key not in collect \
                    and filename not in named:
                collect[key] = filename
        try:
            return make_plan(name, task.name, task.command, options, staging,
                             arguments, app.state.cluster, outputs=dict(task.outputs),
                             inputs=prepared.inputs, collect=collect,
                             tasks=tasks, minutes=minutes,
                             gpu=chosen.get("gpu", False), observables=prepared.observables)
        except (ValueError, OSError) as exc:
            # Also an option that the command does not take
            raise fail(400, str(exc)) from exc

    def openmm_plan(body: Mapping[str, Any], staging: Path, name: str,
                    chosen: Mapping[str, Any]) -> Plan:
        """!
        @brief A simulation with OpenMM of the files that were made for it
        here, run on the cluster with the script of the package.
        @param body    The request: job, the calculation that made the files
        for OpenMM, and simulation, its steps, dt and temperature.
        @param staging The directory that stands for that of the job.
        @param name    The name of the job.
        @param chosen  What the request says about the job: minutes, gpu.
        @return The plan.
        """
        source = body.get("job")
        made = manager.get(source) if isinstance(source, str) else None
        pdb = made.files.get("c") if made is not None else None
        xml = made.files.get("openmm") if made is not None else None
        if made is None or made.task != "openmm" or made.status != "finished" \
                or pdb is None or xml is None:
            raise fail(400, "Make the files for OpenMM of the molecule first")
        minutes, gpu = chosen.get("minutes", 60), chosen.get("gpu", False)
        if not isinstance(gpu, bool):
            raise fail(400, "Whether the job needs a GPU is yes or no")
        simulation = body.get("simulation") or {}
        if not isinstance(simulation, Mapping) or set(simulation) - {"steps", "dt", "temperature"}:
            raise fail(400, "The simulation has steps, dt and temperature")
        try:
            settings = openmm_package.parse_settings(**simulation)
            contents = {
                openmm_package.PDB_NAME: Path(pdb).read_bytes(),
                openmm_package.XML_NAME: Path(xml).read_bytes(),
                # On a node with a GPU OpenMM computes there
                openmm_package.DAT_NAME: openmm_package.simulation_dat(
                    settings, "CUDA" if gpu else "CPU").encode("utf-8"),
                openmm_package.RUN_NAME: openmm_package.run_script().encode("utf-8")}
            return make_script_plan(name, "openmm_run", ["python3", openmm_package.RUN_NAME],
                                    contents, OPENMM_RESULTS, staging, app.state.cluster,
                                    minutes=minutes, gpu=gpu)
        except (openmm_package.PackageError, ClusterError, OSError) as exc:
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
            plan = dataclasses.replace(plan_for(body, staging, name), request={
                key: value for key, value in body.items() if key not in _NOT_KEPT})
            job = queue.send(plan, current)
        except ClusterError as exc:
            queue.release(name)
            raise fail(502, str(exc)) from exc
        except BaseException:
            queue.release(name)
            raise
        return listing(sent=True, name=job.name, slurm=job.slurm)

    def taken(name: str) -> bool:
        """!
        @brief Whether there is a job with a name, here or noted.
        @param name The name.
        @return True if the name is taken.
        """
        return queue.get(name) is not None or queue.directory(name).exists()

    def again_plan(job: ClusterJob, chosen: Mapping[str, Any], name: str,
                   staging: Path) -> Plan:
        """!
        @brief A job that is done, made again with other processes, time or
        GPU and the settings of the cluster as they are now. Its files are
        copied from its directory on the cluster; what follows from the
        processes and the time is made anew: the individuals of a training
        and the time it stops in.
        @param job     The job that is sent again.
        @param chosen  What the request says about the new job.
        @param name    The name of the new job.
        @param staging The directory that stands for that of the new job.
        @return The plan.
        """
        current = app.state.cluster
        here = (current.server, current.user, current.workdir)
        if here != (job.cluster.server, job.cluster.user, job.cluster.workdir):
            raise fail(409, "The files of %s are on %s@%s in %s, and the settings name "
                       "another cluster now. Send the calculation as a new job from its tab."
                       % (job.name, job.cluster.user, job.cluster.server, job.cluster.workdir))
        tasks = chosen.get("tasks", job.tasks)
        minutes = chosen.get("minutes", job.minutes)
        gpu = chosen.get("gpu", job.gpu)
        if isinstance(tasks, bool) or not isinstance(tasks, int) or not 1 <= tasks <= MAX_TASKS:
            raise fail(400, "The number of processes is between 1 and %d" % MAX_TASKS)
        if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 1:
            raise fail(400, "The time of a job is a whole number of minutes")
        task = TASKS.get(job.task)
        words = list(job.words)
        contents: Dict[str, bytes] = {}
        request = job.request or {}
        if job.task == "openmm_run":
            tasks = 1
            # The platform of OpenMM follows from the GPU
            simulation = request.get("simulation") or {}
            try:
                settings = openmm_package.parse_settings(**simulation)
            except (openmm_package.PackageError, TypeError) as exc:
                raise fail(400, "The simulation of %s cannot be made again: %s"
                           % (job.name, exc)) from exc
            contents[openmm_package.DAT_NAME] = openmm_package.simulation_dat(
                settings, "CUDA" if gpu else "CPU").encode("utf-8")
        else:
            # The program as the settings name it now
            words[0] = current.program
        if task is not None and task.parallel and "-pop_size" in words:
            optimizer = words[words.index("-optimizer") + 1] if "-optimizer" in words[:-1] \
                else training.DEFAULT_OPTIMIZER
            before = words[words.index("-pop_size") + 1]
            spec = request.get("training")
            if job.request is None and before != str(job.tasks):
                # A job of an older version: a number of individuals that
                # was not one for each process was given by the user
                spec = {"population": int(before)} if before.isdigit() else None
            try:
                training.processes(tasks, optimizer, tasks)
                individuals = training.population(spec, optimizer, tasks)
            except training.TrainingError as exc:
                raise fail(400, str(exc)) from exc
            if "-gpin" in words and str(individuals) != before:
                raise fail(400, "The training continues one with %s individuals, which it "
                           "keeps: give it processes for %s individuals" % (before, before))
            _replace_value(words, "-pop_size", str(individuals))
        _replace_value(words, "-maxh", repr(float(training.max_hours(60.0 * minutes))))
        for filename, data in contents.items():
            (staging / filename).write_bytes(data)
        try:
            script = cluster.batch_script(current, name, words, tasks=tasks, minutes=minutes,
                                          gpu=gpu)
        except ClusterError as exc:
            raise fail(400, str(exc)) from exc
        return Plan(name=name, task=job.task, command=job.command, words=words, script=script,
                    files={filename: staging / filename for filename in contents},
                    outputs=dict(job.outputs), inputs=dict(job.inputs),
                    collect=dict(job.collect), tasks=tasks, minutes=minutes, gpu=gpu,
                    observables=tuple(job.observables), previous=job.name,
                    copied=tuple(n for n in job.sent if n not in contents),
                    request=job.request)

    def handle_again(previous: str, body: Any) -> Dict[str, Any]:
        """!
        @brief Show a job that is sent again, or send it.
        @param previous The name of the job that is sent again.
        @param body     The request: "cluster" with what the new job asks
        for, and "send".
        @return The job as it would be sent, or the jobs after it was sent.
        """
        job = queue.get(previous)
        if job is None:
            raise fail(404, "There is no such job")
        if app.state.cluster is None:
            raise fail(400, "Give the server, your user name and a working directory in the "
                       "tab Settings first")
        if job.state in ACTIVE:
            raise fail(409, "The job %s is not done" % previous)
        if not job.can_send_again:
            raise fail(409, "The job %s was sent by an older version of the interface, which "
                       "did not note its files. Send the calculation as a new job from its tab."
                       % previous)
        if not isinstance(body, dict):
            raise fail(400, "The request must be an object")
        chosen = body.get("cluster") or {}
        if not isinstance(chosen, dict) or any(key not in CHOICES for key in chosen):
            raise fail(400, "A job has a name, a number of processes (tasks), a time in "
                       "minutes and a GPU or not")
        if not isinstance(chosen.get("gpu", False), bool):
            raise fail(400, "Whether the job needs a GPU is true or false")
        send = body.get("send", False)
        if not isinstance(send, bool):
            raise fail(400, "Whether the job is sent is true or false")
        name = chosen.get("name") or next_name(previous, taken)
        try:
            cluster_jobs.check_name(name)
        except ClusterError as exc:
            raise fail(400, "%s: use letters, digits and . _ -, and no spaces" % exc) from exc
        current = app.state.cluster
        if not send:
            with tempfile.TemporaryDirectory(prefix="act-gui-job-") as tmp:
                staging = Path(tmp) / name
                staging.mkdir()
                return dict(again_plan(job, chosen, name, staging).describe(current),
                            sent=False, taken=taken(name))
        if sum(1 for other in queue.list() if other.state in ACTIVE) >= MAX_ACTIVE:
            raise fail(429, "There are %d jobs waiting or running on the cluster, wait until "
                       "one is done" % MAX_ACTIVE)
        try:
            staging = queue.reserve(name)
        except ClusterError as exc:
            raise fail(409, str(exc)) from exc
        try:
            plan = again_plan(job, chosen, name, staging)
            # The files that were made for the job here, for its results
            for filename in job.inputs.values():
                source = queue.directory(previous) / filename
                if source.is_file() and not (staging / filename).exists():
                    shutil.copyfile(source, staging / filename)
            sent = queue.send(plan, current)
        except ClusterError as exc:
            queue.release(name)
            raise fail(502, str(exc)) from exc
        except BaseException:
            queue.release(name)
            raise
        return listing(sent=True, name=sent.name, slurm=sent.slurm)

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

    @app.post("/api/cluster/jobs/{name}/again")
    async def cluster_again(name: str, request: Request) -> Dict[str, Any]:
        """!
        @brief Show a job that is done as it would be sent again, or send
        it again: one login. Its files are copied on the cluster.
        @param name    The name of the job that is sent again.
        @param request The request, with a JSON object: "cluster" with the
        "name" of the new job, its "tasks", "minutes" and "gpu", each the
        same as before if not given; and "send", true to send it.
        @return Without "send" the job to look at, as for a new job, with
        the files that are copied and the job they come from ("previous").
        With it the jobs, see listing(), and the name and number of the new one.
        """
        try:
            body = await request.json()
        except ValueError as exc:
            raise fail(400, "The request is not valid JSON") from exc
        return await run_in_threadpool(handle_again, name, body)

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
        @return The identifier of the job in the job engine, its task,
        "delivered", the directory in the working directory of the user
        with the files that the job wrote, empty if they are put nowhere,
        and "problem", why they could not be put there, if so.
        """
        job = queue.get(name)
        if job is None:
            raise fail(404, "There is no such job")
        problem = ""
        try:
            # For a job that was fetched before the files were put there
            queue.deliver(name)
        except ClusterError as exc:
            problem = str(exc)
        if job.job and manager.get(job.job) is not None:
            return {"job": job.job, "task": job.task, "delivered": job.delivered,
                    "problem": problem}
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
                                progress=queue.progress_reports(name, MAX_REPORTS),
                                observables=job.observables)
        queue.adopted(name, adopted.id)
        return {"job": adopted.id, "task": task.name, "delivered": job.delivered,
                "problem": problem}

    @app.get("/api/cluster/jobs/{name}/files")
    def cluster_files(name: str) -> Dict[str, Any]:
        """!
        @brief The output files of a job that were fetched, to be saved.
        @param name The name of the job.
        @return "files": for each its name and size in bytes.
        """
        try:
            files = queue.output_files(name)
        except ClusterError as exc:
            raise fail(404, str(exc)) from exc
        return {"files": [{"name": item, "size": path.stat().st_size}
                          for item, path in files.items()]}

    @app.get("/api/cluster/jobs/{name}/files/{filename}")
    def cluster_file(name: str, filename: str) -> FileResponse:
        """!
        @brief An output file of a job that was fetched.
        @param name     The name of the job.
        @param filename The name of the file, one of cluster_files().
        @return The file, to be saved.
        """
        try:
            files = queue.output_files(name)
        except ClusterError as exc:
            raise fail(404, str(exc)) from exc
        if filename not in files:
            raise fail(404, "The job has no such file")
        return FileResponse(files[filename], media_type="application/octet-stream",
                            filename=filename)

    @app.get("/api/cluster/jobs/{name}/text/{filename}")
    def cluster_text(name: str, filename: str) -> Dict[str, Any]:
        """!
        @brief An output file of a job that was fetched, as text to show on
        the page: its end, if it is long.
        @param name     The name of the job.
        @param filename The name of the file, one of cluster_files().
        @return "name", "text", "size" in bytes, "first", the number of the
        first line that is shown, and "cut", whether the start is left out.
        """
        try:
            files = queue.output_files(name)
        except ClusterError as exc:
            raise fail(404, str(exc)) from exc
        if filename not in files:
            raise fail(404, "The job has no such file")
        path = files[filename]
        size = path.stat().st_size
        start = max(0, size - MAX_SHOWN_BYTES)
        skipped = 0
        with open(path, "rb") as handle:
            # The lines that are left out are counted, so that the numbers
            # of the lines are those of the file
            left = start
            while left > 0:
                block = handle.read(min(left, 1024 ** 2))
                if not block:
                    break
                skipped += block.count(b"\n")
                left -= len(block)
            data = handle.read()
        if start:
            # From the start of a line
            end = data.find(b"\n")
            if end >= 0:
                data = data[end + 1:]
                skipped += 1
        return {"name": filename, "text": data.decode("utf-8", errors="replace"), "size": size,
                "first": skipped + 1, "cut": start > 0}

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
