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
@brief The web application: the pages for the browser and the interface
(API) through which the pages start calculations and fetch results.

Rules that hold for every request:
- The interface under /api needs the token that was made when the server
  started. A browser gets it by opening the address with the token once.
  Requests that change something must send it in a header, which a page
  from another site cannot do.
- Requests must be addressed to one of the host names of the server, such
  that a page from another site cannot reach it under another name.
- Answers carry headers that keep the page from loading scripts or data
  from anywhere else.
"""
from __future__ import annotations

import dataclasses
import hmac
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__, cluster, cluster_api, evaluation, ffgen, openmm_package, plots
from . import training
from .cluster_queue import ClusterQueue
from .jobs import FINISHED, JobError, JobManager
from .settings import PACKAGE_DIR, Settings
from .tasks import (TASKS, Prepared, RequestError, Task, build_request, describe_task,
                    find_task, program_lacks)
from .uploads import UploadError, UploadStore

## The name of the cookie and of the header with the token
TOKEN_COOKIE = "act_token"
TOKEN_HEADER = "x-act-token"

## What the page may load: only what this server gives it
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
    "font-src 'self'; connect-src 'self'; worker-src 'self' blob:; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")

## The media types of the files of jobs that can be downloaded
_MEDIA_TYPES = {".pdb": "chemical/x-pdb", ".xvg": "text/plain", ".log": "text/plain",
                ".json": "application/json", ".gro": "text/plain", ".xml": "application/xml",
                ".csv": "text/csv"}


## The title and the label of the y-axis of series that are not what the
## header of their file says: the temperature is in the file with energies
_SERIES_LABELS = {"TEMPERATURE": ("Temperature", "Temperature (K)")}


def create_app(settings: Settings, run: Optional[Callable[..., Any]] = None,
               program_info: Optional[Callable[[], Any]] = None,
               plotter: Optional[Callable[..., Any]] = None,
               allowed_hosts: Optional[list] = None,
               arguments: Optional[Callable[..., Any]] = None) -> FastAPI:
    """!
    @brief Make the web application.
    @param settings      The settings.
    @param run           The function that runs a command, act.run() if None.
    @param program_info  The function that describes the commands,
    act.program_info() if None.
    @param plotter       The function that makes a PDF file of an xvg file,
    plots.make_pdf() if None.
    @param allowed_hosts The host names under which the server may be
    reached, the local machine if None.
    @param arguments     The function that makes the arguments of a command
    from its name, its options and the directory it runs in, and tells
    which files it writes: act.build_arguments() if None. Used for jobs on
    the cluster, which are not started by act.run().
    @return The application, to be served by uvicorn.
    """
    if run is None or program_info is None:
        import act  # pylint: disable=import-outside-toplevel
        run = run or act.run
        program_info = program_info or act.program_info

    if arguments is None:
        def arguments(command: str, options: Dict[str, Any], workdir: Path) -> Any:
            """!
            @brief The arguments of a command as act makes them.
            @param command The command.
            @param options The options.
            @param workdir The directory the command runs in.
            @return See act.build_arguments().
            """
            import act  # pylint: disable=import-outside-toplevel
            return act.build_arguments(act.program_info().find_command(command), options,
                                       workdir)

    # Without plotXVG the page does not offer to save plots
    can_plot = plotter is not None or plots.available()
    plotter = plotter or plots.make_pdf
    uploads = UploadStore(settings.uploads_dir, settings.max_upload_bytes,
                          limits={"molprop": settings.max_molprop_bytes})
    manager = JobManager(settings.jobs_dir, run, max_workers=settings.max_workers,
                         max_active=settings.max_active_jobs, timeout=settings.job_timeout,
                         max_progress=settings.max_progress_points)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """!
        @brief Stop the calculations when the server stops.
        @param _app The application.
        """
        yield
        manager.shutdown()

    app = FastAPI(title="ACT", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.settings = settings
    app.state.jobs = manager
    app.state.uploads = uploads
    # The cluster for long jobs, from the file with the settings of the
    # user. A file that cannot be used does not stop the interface.
    try:
        app.state.cluster = cluster.load(settings.config_file)
        app.state.cluster_error = ""
    except cluster.ClusterError as exc:
        app.state.cluster = None
        app.state.cluster_error = str(exc)
    app.add_middleware(TrustedHostMiddleware,
                       allowed_hosts=allowed_hosts or ["127.0.0.1", "localhost", "testserver"])

    def token_ok(candidate: Optional[str]) -> bool:
        """!
        @brief Compare a token with that of the server, in constant time.
        @param candidate What the request sent.
        @return Whether it is the token.
        """
        return bool(candidate) and hmac.compare_digest(candidate.encode(),
                                                       settings.token.encode())

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[..., Any]) -> Response:
        """!
        @brief Check the token of requests to the interface and add the
        security headers to every answer.
        @param request   The request.
        @param call_next The rest of the application.
        @return The answer.
        """
        path = request.url.path
        response: Response
        if path.startswith("/api/"):
            header = request.headers.get(TOKEN_HEADER)
            cookie = request.cookies.get(TOKEN_COOKIE)
            # Reading needs the token in the header or the cookie, changing
            # something needs the header: another site can make the browser
            # send a cookie, but it cannot read the token to put in a header.
            if request.method in ("GET", "HEAD"):
                allowed = token_ok(header) or token_ok(cookie)
            else:
                allowed = token_ok(header)
            if not allowed:
                response = JSONResponse({"detail": "Open the address that the program "
                                         "printed when it started, it has the key."},
                                        status_code=401)
            else:
                response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
        else:
            response = await call_next(request)
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def fail(status: int, message: str) -> HTTPException:
        """!
        @brief An answer that tells the user what is wrong.
        @param status  The HTTP status.
        @param message The message.
        @return The exception to raise.
        """
        return HTTPException(status_code=status, detail=message)

    @app.get("/")
    async def index(request: Request) -> Response:
        """!
        @brief The page. With the token in the address, remember it in a
        cookie and go to the address without it.
        @param request The request.
        @return The page or a redirect.
        """
        token = request.query_params.get("token")
        if token is not None:
            if not token_ok(token):
                raise fail(401, "This key is not valid. Use the address that the "
                           "program printed when it started.")
            response: Response = RedirectResponse("/", status_code=303)
            response.set_cookie(TOKEN_COOKIE, settings.token, httponly=True,
                                samesite="strict", path="/")
            return response
        return FileResponse(PACKAGE_DIR / "static" / "index.html",
                            headers={"Cache-Control": "no-store"})

    @app.get("/api/session")
    async def session() -> Dict[str, Any]:
        """!
        @brief What the page needs to start: the token for requests that
        change something, and what the server offers.
        @return Token, version, mode, limits, the default force field and
        whether plots can be saved as PDF files.
        """
        return {
            "token": settings.token,
            "version": __version__,
            "mode": settings.mode,
            "default_forcefield": settings.default_forcefield_label,
            "max_upload_bytes": settings.max_upload_bytes,
            "max_smiles_length": settings.max_smiles_length,
            "max_molprop_bytes": settings.max_molprop_bytes,
            "limits": settings.limits,
            "pdf": can_plot,
            "observables": evaluation.describe(),
            "training": training.describe(),
            "max_processes": settings.max_processes,
            "cluster": settings.mode == "desktop",
        }

    def cluster_state() -> Dict[str, Any]:
        """!
        @brief The settings of the cluster, for the browser.
        @return Whether there are settings, the settings, where they are
        kept and what was wrong with the file, if anything.
        """
        current = app.state.cluster
        return {"configured": current is not None,
                "cluster": None if current is None else dataclasses.asdict(current),
                # What is proposed for the settings that the user need not give
                "proposed": {key: value for key, value in dataclasses.asdict(
                    cluster.ClusterSettings("", "", "")).items()
                             if key not in cluster.REQUIRED},
                "load_kinds": list(cluster.LOAD_KINDS), "launchers": list(cluster.LAUNCHERS),
                "file": str(settings.config_file) if settings.config_file else "",
                "problem": app.state.cluster_error}

    def desktop_only() -> None:
        """!
        @brief Refuse a request that is for the interface of one user: on a
        server for several users nobody logs in elsewhere as somebody else.
        """
        if settings.mode != "desktop":
            raise fail(404, "This server sends no jobs to a cluster for its users")

    @app.get("/api/cluster")
    async def get_cluster() -> Dict[str, Any]:
        """!
        @brief The cluster on which long jobs run.
        @return See cluster_state().
        """
        desktop_only()
        return cluster_state()

    @app.put("/api/cluster")
    async def put_cluster(request: Request) -> Dict[str, Any]:
        """!
        @brief Set the cluster on which long jobs run, and keep it in the
        file with the settings of the user.
        @param request The request, with a JSON object: server, user and workdir.
        @return See cluster_state().
        """
        desktop_only()
        try:
            chosen = cluster.parse(await request.json())
            if settings.config_file is not None:
                cluster.save(settings.config_file, chosen)
        except ValueError as exc:
            # Also what is no JSON
            raise fail(400, str(exc)) from exc
        app.state.cluster = chosen
        app.state.cluster_error = ""
        return cluster_state()

    @app.delete("/api/cluster")
    async def delete_cluster() -> Dict[str, Any]:
        """!
        @brief Forget the cluster, also in the file.
        @return See cluster_state().
        """
        desktop_only()
        try:
            if settings.config_file is not None:
                cluster.save(settings.config_file, None)
        except cluster.ClusterError as exc:
            raise fail(500, str(exc)) from exc
        app.state.cluster = None
        app.state.cluster_error = ""
        return cluster_state()

    @app.post("/api/cluster/check")
    def check_cluster() -> Dict[str, Any]:
        """!
        @brief Log in on the cluster once, with the ssh program of this
        machine, and see whether jobs can be sent. Not an async function,
        so that the server does other things while ssh waits.
        @return Whether it worked and a message for the user, see
        cluster.check().
        """
        desktop_only()
        current = app.state.cluster
        if current is None:
            raise fail(400, "Give the server, your user name and a working directory first")
        return cluster.check(current, program=settings.ssh_program)

    @app.get("/api/generator")
    async def generator() -> Dict[str, Any]:
        """!
        @brief What a new force field can be made from.
        @return The atom types, the potentials, the charge models and the
        predefined virtual sites that can be chosen, see ffgen.describe().
        """
        try:
            return ffgen.describe(ffgen.read_atom_types())
        except (OSError, ffgen.GeneratorError) as exc:
            raise fail(500, "The atom types cannot be read: %s" % exc) from exc

    @app.get("/api/tasks")
    async def tasks() -> Dict[str, Any]:
        """!
        @brief The calculations that can be done, with their options.
        @return For each task its options with type, default and help text.
        """
        try:
            info = program_info()
        except Exception as exc:  # pylint: disable=broad-except
            raise fail(503, "The alexandria program is not available: %s" % exc) from exc
        described = []
        for task in TASKS.values():
            command = info.commands.get(task.command)
            described.append(describe_task(task, command))
        return {"tasks": described}

    @app.post("/api/uploads")
    async def upload(request: Request, kind: str, name: str) -> Dict[str, Any]:
        """!
        @brief Take a file from the user.
        @param request The request, of which the body is the file.
        @param kind    molecule, forcefield or molprop.
        @param name    The name of the file, used as a label only.
        @return The identifier with which the file is referred to.
        """
        # Stop reading as soon as the file is too large
        limit = uploads.limit(kind)
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > limit:
                raise fail(413, "The file is larger than %d kB" % (limit // 1024))
        try:
            saved = uploads.save(name, kind, bytes(data))
        except UploadError as exc:
            raise fail(400, str(exc)) from exc
        return {"id": saved.id, "label": saved.label, "kind": saved.kind, "size": saved.size}

    def written_forcefield(job_id: str) -> Optional[Path]:
        """!
        @brief The force field that a job wrote, for use in another job.
        @param job_id The identifier of the job.
        @return The file, None if there is no such job, if it is not done
        or if its task writes no force field.
        """
        job = manager.get(job_id)
        task = TASKS.get(job.task) if job is not None else None
        if job is None or task is None or task.forcefield is None or job.status != FINISHED:
            return None
        path = job.files.get(task.forcefield)
        return path if path is not None and path.is_file() else None

    def described_molprop(job_id: str) -> Optional[Any]:
        """!
        @brief The compounds of a molprop file as a job described them.
        @param job_id The identifier of the job.
        @return The compounds, None if there is no such job, if it is not
        done or if it did not describe a molprop file.
        """
        job = manager.get(job_id)
        if job is None or job.task != "mp_describe" or job.status != FINISHED:
            return None
        result = job.result
        return result.get("molecules") if isinstance(result, dict) else None

    def prepare(body: Any, processes: Optional[int] = None) -> Tuple[Task, Prepared]:
        """!
        @brief Check a request for a calculation.
        @param body      The request.
        @param processes The number of processes that the command will run
        on when that is not up to the request, as for a job on a cluster.
        @return The task and the options of its command.
        """
        try:
            if not isinstance(body, dict):
                raise RequestError("The request must be an object")
            task = find_task(body.get("task"))
            try:
                lacking = program_lacks(task, program_info())
            except Exception:  # pylint: disable=broad-except
                # A program that cannot be asked is reported when it is started
                lacking = None
            if lacking:
                raise fail(409, lacking)
            return task, build_request(task, body, settings, uploads, written_forcefield,
                                       described_molprop,
                                       processes if task.parallel else None)
        except RequestError as exc:
            raise fail(400, str(exc)) from exc

    @app.post("/api/selection")
    async def selection_file(request: Request) -> Response:
        """!
        @brief Write a selection file: which compounds of a molprop file
        are for training and which for testing.
        @param request The request, with a JSON object: "described", the
        job that read the molprop file, and the choices: "sets" with the
        compounds for testing and those that are not used, "monomers" to
        choose compounds by what they consist of, "test" for a part that
        is drawn for testing and "seed" for that draw.
        @return The file, with a line for each compound that is used.
        """
        try:
            body = await request.json()
        except ValueError as exc:
            raise fail(400, "The request is not valid JSON") from exc
        if not isinstance(body, dict):
            raise fail(400, "The request must be an object")
        job_id = body.get("described")
        molecules = described_molprop(job_id) if isinstance(job_id, str) else None
        if molecules is None:
            raise fail(400, "The molprop file has to be read before it is used, open it again")
        try:
            text = evaluation.selection_file(molecules, body, training.test_fraction(body))
        except (evaluation.EvaluationError, training.TrainingError) as exc:
            raise fail(400, str(exc)) from exc
        return Response(text, media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": "attachment"})

    @app.post("/api/jobs")
    async def submit(request: Request) -> Dict[str, Any]:
        """!
        @brief Start a calculation.
        @param request The request, with a JSON object: task and what the
        task needs of molecule, forcefield, molprop, generator and options.
        @return The job.
        """
        try:
            body = await request.json()
        except ValueError as exc:
            raise fail(400, "The request is not valid JSON") from exc
        task, prepared = prepare(body)
        try:
            job = manager.submit(task.name, task.command, prepared.options,
                                 progress=task.progress, outputs=dict(task.outputs),
                                 inputs=prepared.inputs, collect=task.collect,
                                 digest=task.digest, processes=prepared.processes)
        except JobError as exc:
            raise fail(429, str(exc)) from exc
        return manager.summary(job)

    @app.get("/api/jobs")
    async def list_jobs() -> Dict[str, Any]:
        """!
        @brief The calculations of this session.
        @return The jobs without their results, the newest first.
        """
        jobs = []
        for job in manager.list():
            summary = manager.summary(job, progress_from=len(job.progress))
            summary.pop("result")
            jobs.append(summary)
        return {"jobs": jobs}

    def find_job(job_id: str) -> Any:
        """!
        @brief Look up a job or answer that it does not exist.
        @param job_id The identifier.
        @return The job.
        """
        job = manager.get(job_id)
        if job is None:
            raise fail(404, "There is no such calculation")
        return job

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str, progress_from: int = 0) -> Dict[str, Any]:
        """!
        @brief The state of a calculation, with its results when it is done.
        @param job_id        The identifier.
        @param progress_from The number of progress reports the page has.
        @return The job.
        """
        return manager.summary(find_job(job_id), max(0, progress_from))

    @app.delete("/api/jobs/{job_id}")
    async def cancel_job(job_id: str) -> Dict[str, Any]:
        """!
        @brief Stop a calculation.
        @param job_id The identifier.
        @return The job.
        """
        job = find_job(job_id)
        manager.cancel(job_id)
        return manager.summary(job)

    @app.get("/api/jobs/{job_id}/files/{option}")
    async def job_file(job_id: str, option: str) -> Response:
        """!
        @brief An output file of a calculation.
        @param job_id The identifier.
        @param option The option of the command that wrote the file. Only
        the files that the task lists can be fetched.
        @return The file.
        """
        job = find_job(job_id)
        task = TASKS.get(job.task)
        path: Optional[Path] = job.files.get(option)
        if task is None or option not in task.downloads or path is None or not path.is_file():
            raise fail(404, "There is no such file")
        media = _MEDIA_TYPES.get(path.suffix.lower(), "text/plain")
        # A force field and the files that were made for the job keep the
        # name the server gave them, the others are named after the task
        named = option == task.forcefield or option in job.inputs
        return FileResponse(path, media_type=media,
                            filename=path.name if named
                            else "%s-%s%s" % (job.task, option, path.suffix))

    @app.get("/api/jobs/{job_id}/compounds/{index}")
    def job_compound(job_id: str, index: int) -> Dict[str, Any]:
        """!
        @brief The energies of one compound of a comparison or a training,
        from the file in which the program wrote its whole result.
        @param job_id The identifier of the calculation.
        @param index  The place of the compound in the list of the result.
        @return See evaluation.compound().
        """
        job = find_job(job_id)
        task = TASKS.get(job.task)
        path: Optional[Path] = job.files.get("json")
        if task is None or task.digest not in (evaluation.digest, training.digest) \
                or job.status != FINISHED or path is None or not path.is_file():
            raise fail(404, "This calculation has no energies of compounds")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            # As act does: the file has one key, the name of the command
            if isinstance(data, dict) and len(data) == 1:
                data = next(iter(data.values()))
            return evaluation.compound(data, index)
        except evaluation.EvaluationError as exc:
            raise fail(404, str(exc)) from exc
        except (OSError, ValueError) as exc:
            raise fail(500, "The result of the calculation cannot be read") from exc

    @app.get("/api/jobs/{job_id}/plots/{option}")
    def job_plot(job_id: str, option: str, series: str = "",
                 reverse: bool = False) -> Response:
        """!
        @brief A plot of an output file of a calculation, as a PDF file
        made by plotXVG. Not an async function, so that the server does
        other things while the plot is made.
        @param job_id The identifier.
        @param option The option of the command that wrote the data. Only
        the plots that the task lists can be made.
        @param series  The names of the series to plot, with commas between
        them; all series if empty.
        @param reverse Whether the x-axis runs from large to small values.
        @return The PDF file.
        """
        job = find_job(job_id)
        task = TASKS.get(job.task)
        source: Optional[Path] = job.files.get(option)
        if task is None or option not in task.plots or source is None or not source.is_file():
            raise fail(404, "There is no such plot")
        try:
            # Checked before the names become part of a file name
            names = plots.check_series(source, [name for name in series.split(",") if name])
        except plots.PlotError as exc:
            raise fail(400, str(exc)) from exc
        label = "-".join([option] + names + (["reversed"] if reverse else []))
        target = source.parent / ("plot-%s.pdf" % label)
        if not target.is_file():
            if not can_plot:
                raise fail(503, "plotXVG is not installed on the server, so plots cannot "
                           "be saved as PDF")
            try:
                # A series with another unit than the file says
                labels = {_SERIES_LABELS.get(name) for name in names}
                extra = {}
                if len(labels) == 1 and None not in labels:
                    title, ylabel = labels.pop()
                    extra = {"title": title, "ylabel": ylabel}
                if task.scatter:
                    extra["scatter"] = True
                plotter(source, target, series=names, reverse_x=reverse, **extra)
            except plots.PlotError as exc:
                raise fail(500, str(exc)) from exc
        return FileResponse(target, media_type="application/pdf",
                            filename="%s-%s.pdf" % (job.task, label))

    @app.get("/api/jobs/{job_id}/package")
    async def job_package(job_id: str, steps: Optional[str] = None, dt: Optional[str] = None,
                          temperature: Optional[str] = None) -> Response:
        """!
        @brief The archive with which the molecule of a job is simulated
        with OpenMM: the files that gentop wrote, the settings of the
        simulation, a script and an explanation.
        @param job_id      The identifier of a job of a task with a package.
        @param steps       The number of steps of the simulation.
        @param dt          The time step in fs.
        @param temperature The temperature in K.
        @return The zip archive.
        """
        job = find_job(job_id)
        task = TASKS.get(job.task)
        pdb: Optional[Path] = job.files.get("c")
        xml: Optional[Path] = job.files.get("openmm")
        if task is None or not task.package or pdb is None or xml is None:
            raise fail(404, "There is no such package")
        try:
            chosen = openmm_package.parse_settings(steps, dt, temperature)
            data = openmm_package.build(pdb, xml, chosen, __version__)
        except openmm_package.PackageError as exc:
            raise fail(400, str(exc)) from exc
        return Response(content=data, media_type="application/zip",
                        headers={"Content-Disposition":
                                 'attachment; filename="%s.zip"' % openmm_package.FOLDER})

    if settings.mode == "desktop":
        # Jobs on the cluster of the user. On a server for several users
        # nobody logs in elsewhere as somebody else.
        app.state.cluster_queue = ClusterQueue(settings.cluster_jobs_dir,
                                               program=settings.ssh_program)
        cluster_api.register(app, manager, app.state.cluster_queue, prepare, arguments, fail)

    static_dir = PACKAGE_DIR / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")
    return app
