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

import hmac
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__, plots
from .jobs import JobError, JobManager
from .settings import PACKAGE_DIR, Settings
from .tasks import TASKS, RequestError, build_request, describe_task, find_task
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
                ".json": "application/json", ".gro": "text/plain", ".xml": "application/xml"}


def create_app(settings: Settings, run: Optional[Callable[..., Any]] = None,
               program_info: Optional[Callable[[], Any]] = None,
               plotter: Optional[Callable[..., Any]] = None,
               allowed_hosts: Optional[list] = None) -> FastAPI:
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
    @return The application, to be served by uvicorn.
    """
    if run is None or program_info is None:
        import act  # pylint: disable=import-outside-toplevel
        run = run or act.run
        program_info = program_info or act.program_info

    # Without plotXVG the page does not offer to save plots
    can_plot = plotter is not None or plots.available()
    plotter = plotter or plots.make_pdf
    uploads = UploadStore(settings.uploads_dir, settings.max_upload_bytes)
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
            "limits": settings.limits,
            "pdf": can_plot,
        }

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
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > settings.max_upload_bytes:
                raise fail(413, "The file is larger than %d kB"
                           % (settings.max_upload_bytes // 1024))
        try:
            saved = uploads.save(name, kind, bytes(data))
        except UploadError as exc:
            raise fail(400, str(exc)) from exc
        return {"id": saved.id, "label": saved.label, "kind": saved.kind, "size": saved.size}

    @app.post("/api/jobs")
    async def submit(request: Request) -> Dict[str, Any]:
        """!
        @brief Start a calculation.
        @param request The request, with a JSON object: task, molecule,
        forcefield and options.
        @return The job.
        """
        try:
            body = await request.json()
        except ValueError as exc:
            raise fail(400, "The request is not valid JSON") from exc
        try:
            if not isinstance(body, dict):
                raise RequestError("The request must be an object")
            task = find_task(body.get("task"))
            options = build_request(task, body, settings, uploads)
            job = manager.submit(task.name, task.command, options,
                                 progress=task.progress, outputs=dict(task.outputs))
        except RequestError as exc:
            raise fail(400, str(exc)) from exc
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
        return FileResponse(path, media_type=media,
                            filename="%s-%s%s" % (job.task, option, path.suffix))

    @app.get("/api/jobs/{job_id}/plots/{option}")
    def job_plot(job_id: str, option: str, series: str = "") -> Response:
        """!
        @brief A plot of an output file of a calculation, as a PDF file
        made by plotXVG. Not an async function, so that the server does
        other things while the plot is made.
        @param job_id The identifier.
        @param option The option of the command that wrote the data. Only
        the plots that the task lists can be made.
        @param series The names of the series to plot, with commas between
        them; all series if empty.
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
        label = "-".join([option] + names)
        target = source.parent / ("plot-%s.pdf" % label)
        if not target.is_file():
            if not can_plot:
                raise fail(503, "plotXVG is not installed on the server, so plots cannot "
                           "be saved as PDF")
            try:
                plotter(source, target, series=names)
            except plots.PlotError as exc:
                raise fail(500, str(exc)) from exc
        return FileResponse(target, media_type="application/pdf",
                            filename="%s-%s.pdf" % (job.task, label))

    static_dir = PACKAGE_DIR / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")
    return app
