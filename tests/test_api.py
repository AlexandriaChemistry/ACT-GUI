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
@brief Tests of the web interface, with a function in the place of act.run().
"""
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from act_gui.app import TOKEN_COOKIE, TOKEN_HEADER, create_app
from act_gui.plots import PlotError
from act_gui.settings import Settings
from tests.test_jobs import FakeRun
from tests.test_uploads import FF, SDF


def fake_program_info():
    """!
    @brief Stands in for act.program_info().
    @return Commands with a few options, as far as the tasks need them.
    """
    def option(name, otype, default=None, values=()):
        return SimpleNamespace(name=name, type=otype, default=default, values=values,
                               description="Help for " + name)

    def command(*options):
        table = {o.name: o for o in options}
        return SimpleNamespace(options=table, has_option=lambda name: name in table)

    common = [option("qalg", "string"), option("maxiter", "int", 1000),
              option("minalg", "enum", "LBFGS", ("LBFGS", "Newton", "Steep")),
              option("toler", "real", 1e-6)]
    return SimpleNamespace(commands={
        "minimize": command(*common), "nma": command(*common, option("linewidth", "real", 24)),
        "gentop": command(option("qalg", "string")),
        "simulate": command(option("qalg", "string"), option("nsteps", "int", 0),
                            option("temp", "real", 0)),
    })


class ApiTest(unittest.TestCase):
    """!
    @brief Requests to the interface.
    """

    def setUp(self):
        """!
        @brief Make an application with a known token and a client for it.
        """
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        ff = self.dir / "default.xml"
        ff.write_bytes(FF)
        self.settings = Settings(data_dir=self.dir / "data", token="secret-token",
                                 default_forcefield=ff, max_upload_bytes=4096,
                                 max_active_jobs=3)
        self.run = FakeRun()
        self.app = create_app(self.settings, run=self.run, program_info=fake_program_info)
        self.client = TestClient(self.app)
        self.headers = {TOKEN_HEADER: "secret-token"}

    def tearDown(self):
        """!
        @brief Stop the jobs and remove the files.
        """
        self.run.release.set()
        self.app.state.jobs.shutdown()
        self._tmp.cleanup()

    def wait(self, job_id, states=("finished", "failed", "cancelled")):
        """!
        @brief Ask for a job until it is in one of the states.
        @param job_id The identifier.
        @param states The states.
        @return The last answer.
        """
        for _ in range(500):
            job = self.client.get("/api/jobs/" + job_id, headers=self.headers).json()
            if job["status"] in states:
                return job
            time.sleep(0.01)
        self.fail("The job did not get to %s" % (states,))
        return None

    def upload(self, name="water.sdf", kind="molecule", data=SDF):
        """!
        @brief Upload a file.
        @param name The name.
        @param kind The kind.
        @param data The content.
        @return The answer.
        """
        return self.client.post("/api/uploads", params={"kind": kind, "name": name},
                                content=data, headers=self.headers)

    # --- who may ask ---

    def test_interface_needs_the_token(self):
        """!
        @brief Without the token nothing is answered.
        """
        self.assertEqual(401, self.client.get("/api/session").status_code)
        self.assertEqual(401, self.client.get("/api/jobs").status_code)
        self.assertEqual(401, self.client.post("/api/jobs", json={}).status_code)
        self.assertEqual(401, self.client.get("/api/session",
                                              headers={TOKEN_HEADER: "wrong"}).status_code)
        self.assertEqual(200, self.client.get("/api/session", headers=self.headers).status_code)

    def test_address_with_token_gives_a_cookie(self):
        """!
        @brief Opening the address with the token once is enough to read.
        """
        answer = self.client.get("/", params={"token": "secret-token"}, follow_redirects=False)
        self.assertEqual(303, answer.status_code)
        self.assertEqual("/", answer.headers["location"])
        cookie = answer.headers["set-cookie"]
        self.assertIn(TOKEN_COOKIE + "=secret-token", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie)
        self.assertEqual(401, self.client.get("/", params={"token": "wrong"},
                                              follow_redirects=False).status_code)
        # The cookie is enough to read, and gives the token for the header
        client = TestClient(self.app, cookies={TOKEN_COOKIE: "secret-token"})
        session = client.get("/api/session")
        self.assertEqual(200, session.status_code)
        self.assertEqual("secret-token", session.json()["token"])

    def test_cookie_alone_cannot_change_anything(self):
        """!
        @brief A page of another site can make the browser send the cookie,
        but that does not start, stop or upload anything.
        """
        client = TestClient(self.app, cookies={TOKEN_COOKIE: "secret-token"})
        self.assertEqual(401, client.post("/api/jobs", json={"task": "minimize"}).status_code)
        self.assertEqual(401, client.post("/api/uploads", params={"kind": "molecule",
                                                                  "name": "a.sdf"},
                                          content=SDF).status_code)
        self.assertEqual(401, client.delete("/api/jobs/abc").status_code)
        self.assertEqual([], self.run.calls)

    def test_other_host_names_are_refused(self):
        """!
        @brief A request under another host name is not answered.
        """
        answer = self.client.get("/api/session", headers={**self.headers, "host": "evil.example"})
        self.assertEqual(400, answer.status_code)

    def test_security_headers(self):
        """!
        @brief Every answer tells the browser to load nothing from elsewhere.
        """
        for answer in (self.client.get("/api/session", headers=self.headers),
                       self.client.get("/api/session"),
                       self.client.get("/nothing")):
            policy = answer.headers["content-security-policy"]
            self.assertIn("default-src 'none'", policy)
            self.assertIn("script-src 'self'", policy)
            self.assertNotIn("unsafe", policy)
            self.assertEqual("nosniff", answer.headers["x-content-type-options"])
            self.assertEqual("DENY", answer.headers["x-frame-options"])
        self.assertEqual("no-store", self.client.get("/api/session",
                                                     headers=self.headers).headers["cache-control"])

    def test_no_generated_documentation_pages(self):
        """!
        @brief The pages that FastAPI can make of the interface are off.
        """
        for path in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual(404, self.client.get(path, headers=self.headers).status_code)

    # --- what the server offers ---

    def test_session_and_tasks(self):
        """!
        @brief The page learns the limits and the tasks with their options.
        """
        session = self.client.get("/api/session", headers=self.headers).json()
        self.assertEqual("desktop", session["mode"])
        self.assertEqual(4096, session["max_upload_bytes"])
        self.assertIn("ACS-pg1", session["default_forcefield"])
        tasks = self.client.get("/api/tasks", headers=self.headers).json()["tasks"]
        names = [task["name"] for task in tasks]
        self.assertEqual(["charges", "minimize", "nma", "simulate", "openmm"], names)
        minimize = tasks[1]
        minalg = [o for o in minimize["options"] if o["name"] == "minalg"][0]
        self.assertEqual("enum", minalg["type"])
        self.assertEqual(["LBFGS", "Newton", "Steep"], minalg["values"])
        self.assertTrue(tasks[3]["progress"])

    # --- uploads ---

    def test_upload(self):
        """!
        @brief A file is taken and gets an identifier; wrong files are refused.
        """
        answer = self.upload()
        self.assertEqual(200, answer.status_code)
        self.assertEqual("water.sdf", answer.json()["label"])
        self.assertEqual(32, len(answer.json()["id"]))
        self.assertEqual(400, self.upload(name="water.exe").status_code)
        self.assertEqual(400, self.upload(data=b"nothing").status_code)
        self.assertEqual(413, self.upload(data=SDF + b" " * 5000).status_code)
        self.assertIn("larger", self.upload(data=SDF + b" " * 5000).json()["detail"])

    # --- jobs ---

    def test_job_from_smiles(self):
        """!
        @brief A calculation is started and its result is fetched.
        """
        answer = self.client.post("/api/jobs", headers=self.headers, json={
            "task": "minimize", "molecule": {"smiles": " CC(=O)C "},
            "options": {"maxiter": 50, "minalg": "Newton"}})
        self.assertEqual(200, answer.status_code)
        job = self.wait(answer.json()["id"])
        self.assertEqual("finished", job["status"])
        self.assertEqual(0.02, job["result"]["rmsd"]["value"])
        self.assertEqual(["c"], job["files"])
        call = self.run.calls[0]
        self.assertEqual("minimize", call["command"])
        self.assertEqual("CC(=O)C", call["options"]["smiles"])
        self.assertEqual(50, call["options"]["maxiter"])
        self.assertEqual(self.settings.default_forcefield, call["options"]["ff"])
        self.assertIs(True, call["options"]["json"])
        # The output file is in the directory of the job
        self.assertEqual(call["workdir"] / "minimized.pdb", call["options"]["c"])
        self.assertEqual(self.settings.job_timeout, call["timeout"])

    def test_job_from_uploaded_files(self):
        """!
        @brief Uploaded files are passed by the path the server gave them.
        """
        molecule = self.upload().json()["id"]
        forcefield = self.upload("my.xml", "forcefield", FF).json()["id"]
        answer = self.client.post("/api/jobs", headers=self.headers, json={
            "task": "nma", "molecule": {"file": molecule}, "forcefield": {"file": forcefield}})
        self.assertEqual(200, answer.status_code)
        self.wait(answer.json()["id"])
        options = self.run.calls[0]["options"]
        self.assertEqual(self.settings.uploads_dir / (molecule + ".sdf"), options["f"])
        self.assertEqual(self.settings.uploads_dir / (forcefield + ".xml"), options["ff"])
        self.assertIs(True, options["ir"])

    def test_requests_that_are_refused(self):
        """!
        @brief Unknown tasks, options and files, and values outside the
        limits, are refused and nothing is run.
        """
        molecule = self.upload().json()["id"]
        smiles = {"smiles": "CCO"}
        wrong = [
            {"task": "rm", "molecule": smiles},
            {"task": "minimize"},
            {"task": "minimize", "molecule": {}},
            {"task": "minimize", "molecule": {"smiles": "CCO", "file": molecule}},
            {"task": "minimize", "molecule": {"smiles": "C" * 3000}},
            {"task": "minimize", "molecule": {"smiles": "   "}},
            {"task": "minimize", "molecule": {"file": "a" * 32}},
            {"task": "minimize", "molecule": {"file": "../../etc/passwd"}},
            # A molecule is not a force field
            {"task": "minimize", "molecule": smiles, "forcefield": {"file": molecule}},
            # Options of the program that the task does not list
            {"task": "minimize", "molecule": smiles, "options": {"ff": "/etc/passwd"}},
            {"task": "minimize", "molecule": smiles, "options": {"c": "/tmp/x.pdb"}},
            {"task": "minimize", "molecule": smiles, "options": {"g": "/tmp/x.log"}},
            {"task": "minimize", "molecule": smiles, "options": {"workdir": "/tmp"}},
            {"task": "minimize", "molecule": smiles, "options": {"processes": 64}},
            {"task": "minimize", "molecule": smiles, "options": {"nsteps": 5}},
            # Outside the limits
            {"task": "simulate", "molecule": smiles, "options": {"nsteps": 10**9}},
            {"task": "simulate", "molecule": smiles, "options": {"nsteps": "many"}},
            {"task": "simulate", "molecule": smiles, "options": {"temp": -5}},
            ["not", "an", "object"],
        ]
        for body in wrong:
            answer = self.client.post("/api/jobs", headers=self.headers, json=body)
            self.assertEqual(400, answer.status_code, body)
            self.assertTrue(answer.json()["detail"])
        answer = self.client.post("/api/jobs", headers=self.headers, content=b"{not json")
        self.assertEqual(400, answer.status_code)
        self.assertEqual([], self.run.calls)

    def test_failed_job_tells_why(self):
        """!
        @brief The message of a failure reaches the page.
        """
        # behaviour is not an option of the task, so make the run fail itself
        self.run.calls.clear()
        original = self.run.__class__.__call__

        def failing(run, command, **kwargs):
            raise RuntimeError("alexandria minimize failed with exit status 1: no parameters")

        self.run.__class__.__call__ = failing
        try:
            answer = self.client.post("/api/jobs", headers=self.headers, json={
                "task": "minimize", "molecule": {"smiles": "CCO"}})
            job = self.wait(answer.json()["id"])
        finally:
            self.run.__class__.__call__ = original
        self.assertEqual("failed", job["status"])
        self.assertIn("no parameters", job["error"])
        self.assertIsNone(job["result"])

    def test_progress_and_list(self):
        """!
        @brief Progress is fetched in parts, and the list has no results.
        """
        answer = self.client.post("/api/jobs", headers=self.headers, json={
            "task": "simulate", "molecule": {"smiles": "CCO"},
            "options": {"nsteps": 100, "temp": 300}})
        job_id = answer.json()["id"]
        job = self.wait(job_id)
        self.assertEqual([0, 1, 2], [p["step"] for p in job["progress"]])
        later = self.client.get("/api/jobs/" + job_id, params={"progress_from": 2},
                                headers=self.headers).json()
        self.assertEqual([2], [p["step"] for p in later["progress"]])
        self.assertEqual(3, later["progress_total"])
        listed = self.client.get("/api/jobs", headers=self.headers).json()["jobs"]
        self.assertEqual(1, len(listed))
        self.assertNotIn("result", listed[0])
        self.assertEqual([], listed[0]["progress"])

    def test_download(self):
        """!
        @brief Only the files that the task lists can be fetched.
        """
        answer = self.client.post("/api/jobs", headers=self.headers, json={
            "task": "minimize", "molecule": {"smiles": "CCO"}})
        job_id = answer.json()["id"]
        self.wait(job_id)
        good = self.client.get("/api/jobs/%s/files/c" % job_id, headers=self.headers)
        self.assertEqual(200, good.status_code)
        self.assertEqual("ATOM\n", good.text)
        self.assertIn("minimize-c.pdb", good.headers["content-disposition"])
        for option in ("ff", "json", "nothere", "..%2F..%2Fetc%2Fpasswd"):
            self.assertEqual(404, self.client.get("/api/jobs/%s/files/%s" % (job_id, option),
                                                  headers=self.headers).status_code)
        self.assertEqual(404, self.client.get("/api/jobs/nosuchjob/files/c",
                                              headers=self.headers).status_code)
        self.assertEqual(401, self.client.get("/api/jobs/%s/files/c" % job_id).status_code)

    def plot_app(self, plotter="fake"):
        """!
        @brief An application whose jobs write an xvg file and a force
        field for OpenMM, with a stand-in for plotXVG.
        @param plotter "fake" for a stand-in that records its calls, None
        for no plotter at all.
        @return The client and the list of calls of the stand-in.
        """
        calls = []

        def run(command, *, workdir, **kwargs):
            result = self.run(command, workdir=workdir, **kwargs)
            xvg = Path(workdir) / "data.xvg"
            xvg.write_text('@ s0 legend "EPOT"\n@ s1 legend "TEMPERATURE"\n0 1 2\n1 2 3\n')
            xml = Path(workdir) / "openmm.xml"
            xml.write_text("<ForceField/>\n")
            result.files.update({"ir": [xvg], "e": [xvg], "openmm": [xml]})
            return result

        def fake(xvg, pdf, series=()):
            calls.append((Path(xvg).name, Path(pdf).name, list(series)))
            Path(pdf).write_bytes(b"%PDF-1.4 fake")
            return Path(pdf)

        def broken(xvg, pdf, series=()):
            raise PlotError("matplotlib is unhappy")

        chosen = {"fake": fake, "broken": broken}.get(plotter)
        app = create_app(self.settings, run=run, program_info=fake_program_info, plotter=chosen)
        self.addCleanup(app.state.jobs.shutdown)
        return TestClient(app), calls

    def finished_job(self, client, task):
        """!
        @brief Run a job to its end.
        @param client The client of the application.
        @param task   The name of the task.
        @return The identifier of the job.
        """
        answer = client.post("/api/jobs", headers=self.headers, json={
            "task": task, "molecule": {"smiles": "CCO"}})
        job_id = answer.json()["id"]
        for _ in range(500):
            if client.get("/api/jobs/" + job_id, headers=self.headers).json()["status"] == "finished":
                return job_id
            time.sleep(0.01)
        self.fail("The job did not finish")
        return job_id

    def test_plot_as_pdf(self):
        """!
        @brief A plot of a task that has one comes as a PDF file, is made
        once, and can be limited to series of the file.
        """
        client, calls = self.plot_app()
        self.assertTrue(client.get("/api/session", headers=self.headers).json()["pdf"])
        tasks = {t["name"]: t for t in client.get("/api/tasks", headers=self.headers).json()["tasks"]}
        self.assertEqual(["ir"], tasks["nma"]["plots"])
        self.assertEqual(["e"], tasks["simulate"]["plots"])
        self.assertEqual([], tasks["minimize"]["plots"])

        job_id = self.finished_job(client, "simulate")
        url = "/api/jobs/%s/plots/e" % job_id
        answer = client.get(url, headers=self.headers)
        self.assertEqual(200, answer.status_code)
        self.assertEqual("application/pdf", answer.headers["content-type"])
        self.assertTrue(answer.content.startswith(b"%PDF"))
        self.assertIn("simulate-e.pdf", answer.headers["content-disposition"])
        # The second time the same file is sent
        self.assertEqual(200, client.get(url, headers=self.headers).status_code)
        self.assertEqual([("data.xvg", "plot-e.pdf", [])], calls)
        # One series
        answer = client.get(url, headers=self.headers, params={"series": "TEMPERATURE"})
        self.assertEqual(200, answer.status_code)
        self.assertIn("simulate-e-TEMPERATURE.pdf", answer.headers["content-disposition"])
        self.assertEqual(("data.xvg", "plot-e-TEMPERATURE.pdf", ["TEMPERATURE"]), calls[-1])
        # The cookie is enough to fetch a plot, nothing at all is not
        self.assertEqual(401, client.get(url).status_code)

    def test_plots_that_are_refused(self):
        """!
        @brief Only the plots of the task, only series of the file, and
        nothing that could become part of a path or a command line.
        """
        client, calls = self.plot_app()
        job_id = self.finished_job(client, "simulate")
        base = "/api/jobs/%s/plots/" % job_id
        for option in ("c", "o", "ir", "nothere", "..%2F..%2Fetc%2Fpasswd"):
            self.assertEqual(404, client.get(base + option, headers=self.headers).status_code,
                             option)
        for series in ("ETOT", "-save", "../../x", "EPOT,..", "EPOT/TEMPERATURE"):
            answer = client.get(base + "e", headers=self.headers, params={"series": series})
            self.assertEqual(400, answer.status_code, series)
            self.assertIn("no series", answer.json()["detail"])
        self.assertEqual(404, client.get("/api/jobs/nosuchjob/plots/e",
                                         headers=self.headers).status_code)
        # A task without plots
        job_id = self.finished_job(client, "minimize")
        self.assertEqual(404, client.get("/api/jobs/%s/plots/e" % job_id,
                                         headers=self.headers).status_code)
        self.assertEqual([], calls)

    def test_plotter_that_fails_tells_why(self):
        """!
        @brief A plot that cannot be made gives a message for the user.
        """
        client, _ = self.plot_app("broken")
        job_id = self.finished_job(client, "nma")
        answer = client.get("/api/jobs/%s/plots/ir" % job_id, headers=self.headers)
        self.assertEqual(500, answer.status_code)
        self.assertIn("matplotlib is unhappy", answer.json()["detail"])

    def test_openmm_package(self):
        """!
        @brief The files of gentop come as an archive with the settings
        that were asked for.
        """
        import io  # pylint: disable=import-outside-toplevel
        import zipfile  # pylint: disable=import-outside-toplevel
        client, _ = self.plot_app()
        tasks = {t["name"]: t for t in client.get("/api/tasks", headers=self.headers).json()["tasks"]}
        self.assertTrue(tasks["openmm"]["package"])
        self.assertFalse(tasks["minimize"]["package"])
        job_id = self.finished_job(client, "openmm")
        url = "/api/jobs/%s/package" % job_id
        answer = client.get(url, headers=self.headers,
                            params={"steps": 500, "dt": 1, "temperature": 280})
        self.assertEqual(200, answer.status_code)
        self.assertEqual("application/zip", answer.headers["content-type"])
        self.assertIn("act-openmm.zip", answer.headers["content-disposition"])
        with zipfile.ZipFile(io.BytesIO(answer.content)) as archive:
            self.assertEqual(5, len(archive.namelist()))
            self.assertEqual(b"ATOM\n", archive.read("act-openmm/conf.pdb"))
            self.assertEqual(b"<ForceField/>\n", archive.read("act-openmm/openmm.xml"))
            settings = archive.read("act-openmm/simulation.dat").decode()
            self.assertIn("= 500\n", settings)
            self.assertIn("= 0.001*picoseconds", settings)
            self.assertIn("= 280*kelvin", settings)
        # The defaults, and the output files one by one
        self.assertEqual(200, client.get(url, headers=self.headers).status_code)
        self.assertEqual(200, client.get("/api/jobs/%s/files/openmm" % job_id,
                                         headers=self.headers).status_code)
        # Settings that are refused
        for params in ({"steps": 0}, {"steps": "1e99"}, {"dt": "fast"}, {"temperature": -3},
                       {"steps": "10; rm -rf"}):
            answer = client.get(url, headers=self.headers, params=params)
            self.assertEqual(400, answer.status_code, params)
            self.assertIn("must be", answer.json()["detail"])
        self.assertEqual(401, client.get(url).status_code)
        # Only for the task that makes the files
        other = self.finished_job(client, "minimize")
        self.assertEqual(404, client.get("/api/jobs/%s/package" % other,
                                         headers=self.headers).status_code)
        self.assertEqual(404, client.get("/api/jobs/nosuchjob/package",
                                         headers=self.headers).status_code)

    def test_cancel(self):
        """!
        @brief A calculation can be stopped.
        """
        original = self.run.__class__.__call__

        def blocking(run, command, *, stop=None, **kwargs):
            while not stop.is_set():
                time.sleep(0.01)
            raise type("ActCancelledError", (RuntimeError,), {})("stopped")

        self.run.__class__.__call__ = blocking
        try:
            answer = self.client.post("/api/jobs", headers=self.headers, json={
                "task": "simulate", "molecule": {"smiles": "CCO"}})
            job_id = answer.json()["id"]
            self.wait(job_id, states=("running",))
            self.assertEqual(200, self.client.delete("/api/jobs/" + job_id,
                                                     headers=self.headers).status_code)
            job = self.wait(job_id)
        finally:
            self.run.__class__.__call__ = original
        self.assertEqual("cancelled", job["status"])
        self.assertEqual(404, self.client.delete("/api/jobs/nosuchjob",
                                                 headers=self.headers).status_code)


if __name__ == "__main__":
    unittest.main()
