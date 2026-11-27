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
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from act_gui.app import TOKEN_COOKIE, TOKEN_HEADER, create_app
from act_gui.plots import PlotError
from act_gui.settings import Settings
from act_gui.tasks import TASKS
from tests.test_jobs import FakeRun
from tests.test_evaluation import ION_WATER
from tests.test_uploads import FF, MP, SDF


def fake_program_info():
    """!
    @brief Stands in for act.program_info().
    @return Commands with a few options, as far as the tasks need them.
    """
    def option(name, otype, default=None, values=(), **more):
        return SimpleNamespace(name=name, type=otype, default=default, values=values,
                               description="Help for " + name, **more)

    def written(name, default):
        # A file that the program writes under its default name when the
        # option is not given
        return option(name, "file", default, is_file=True, is_output=True, required=True)

    def command(*options):
        table = {o.name: o for o in options}
        return SimpleNamespace(options=table, has_option=lambda name: name in table)

    common = [option("qalg", "string"), option("maxiter", "int", 1000),
              option("minalg", "enum", "LBFGS", ("LBFGS", "Newton", "Steep")),
              option("toler", "real", 1e-6)]
    known = {
        "minimize": list(common), "nma": common + [option("linewidth", "real", 24)],
        "gentop": [option("qalg", "string")],
        "simulate": [option("qalg", "string"), option("nsteps", "int", 0),
                     option("temp", "real", 0), option("nbeads", "int", 0),
                     option("rwall", "real", 0), option("monomers", "bool", False),
                     option("thermo", "bool", False), option("nquad", "int", 4),
                     written("hist", "distance.xvg")],
        "train_ff": [written("g", "train_ff.log"), written("sensout", "sensitivity.json"),
                     written("gpout", "genepool.csv"), written("o", "ff_train.xml")],
    }
    # What the interface gives with every run of a task, the program has
    for task in TASKS.values():
        options = known.setdefault(task.command, [])
        for name in list(task.fixed) + list(task.outputs) + list(task.optional):
            if all(name != o.name for o in options):
                options.append(option(name, "file"))
    return SimpleNamespace(commands={name: command(*options)
                                     for name, options in known.items()})


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
                                 max_active_jobs=3, max_processes=4)
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

    def test_logo_and_icon(self):
        """!
        @brief The page shows the logo of ACT, and the browser gets it as the
        icon of the page, also at /favicon.ico where it asks without being
        told; neither needs the key.
        """
        page = self.client.get("/").text
        self.assertIn('<link rel="icon" href="/favicon.ico" sizes="any">', page)
        self.assertIn('src="/static/img/act-logo.png"', page)
        logo = self.client.get("/static/img/act-logo.png")
        self.assertEqual(200, logo.status_code)
        self.assertEqual("image/png", logo.headers["content-type"])
        self.assertTrue(logo.content.startswith(b"\x89PNG"))
        icon = self.client.get("/favicon.ico")
        self.assertEqual(200, icon.status_code)
        self.assertIn(icon.headers["content-type"], ("image/vnd.microsoft.icon", "image/x-icon"))
        # An icon file with the sizes that browsers use
        self.assertEqual(b"\x00\x00\x01\x00", icon.content[:4])
        count = int.from_bytes(icon.content[4:6], "little")
        sizes = sorted(icon.content[6 + 16 * i] or 256 for i in range(count))
        self.assertEqual([16, 32, 48, 64], sizes)
        self.assertIn("content-security-policy", icon.headers)

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
        self.assertEqual(["charges", "minimize", "nma", "simulate", "openmm", "dimer_scan",
                          "b2", "openmm_run", "ff_charges", "gen_ff", "geometry_ff", "ff_describe",
                          "ff_edit", "ff_shells", "ff_repair", "mp_describe", "mp_particles",
                          "mp_clean", "evaluate", "train"],
                         names)
        self.assertEqual(["molecule"] * 9 + ["forcefield"] * 6 + ["molprop"] * 5,
                         [task["workspace"] for task in tasks])
        # Only the training runs on several processes; the simulation, the
        # scan and the training tell how far they are
        self.assertEqual(["train"], [task["name"] for task in tasks if task["parallel"]])
        self.assertEqual(["simulate", "dimer_scan", "train"],
                         [task["name"] for task in tasks if task["progress"]])
        self.assertEqual(self.settings.max_processes, session["max_processes"])
        # No working directory unless the interface is given one
        self.assertEqual("", session["work_dir"])
        self.assertEqual("MCMC", session["training"]["default_optimizer"])
        self.assertIn({"name": "inter", "label": "Interaction energy", "dimer": True,
                       "statistics": ["INTERACTION"], "plots": ["INTERACTION"]},
                      session["observables"])
        self.assertEqual(["molecule", "forcefield", "scan"], tasks[5]["needs"])
        self.assertEqual(["forcefield"], tasks[8]["needs"])
        self.assertEqual(["generator"], tasks[9]["needs"])
        self.assertEqual(["forcefield", "molprop", "chosen"], tasks[10]["needs"])
        # The simulation with OpenMM runs on the cluster only
        refused = self.client.post("/api/jobs", headers=self.headers, json={"task": "openmm_run"})
        self.assertEqual(400, refused.status_code)
        self.assertIn("cluster only", refused.json()["detail"])
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

    def test_simulation_at_constant_temperature(self):
        """!
        @brief The beads, the wall, the binding energy and the thermochemistry
        become options of simulate, and the distribution of the distance is
        asked for with beads only.
        """
        answer = self.client.post("/api/jobs", headers=self.headers, json={
            "task": "simulate", "molecule": {"smiles": "O"},
            "options": {"nsteps": 100, "temp": 300, "nbeads": 16, "rwall": 0.8, "monomers": True}})
        self.assertEqual(200, answer.status_code, answer.text)
        self.wait(answer.json()["id"])
        call = self.run.calls[0]
        self.assertEqual("simulate", call["command"])
        self.assertEqual(16, call["options"]["nbeads"])
        self.assertEqual(0.8, call["options"]["rwall"])
        self.assertIs(True, call["options"]["monomers"])
        self.assertEqual(call["workdir"] / "distance.xvg", call["options"]["hist"])
        # The thermochemistry of one compound
        answer = self.client.post("/api/jobs", headers=self.headers, json={
            "task": "simulate", "molecule": {"smiles": "O"},
            "options": {"nsteps": 100, "temp": 300, "nbeads": 1, "thermo": True, "nquad": 2}})
        self.assertEqual(200, answer.status_code, answer.text)
        self.wait(answer.json()["id"])
        call = self.run.calls[1]
        self.assertIs(True, call["options"]["thermo"])
        self.assertEqual(2, call["options"]["nquad"])
        # At constant energy no beads are given and no distribution is asked for
        for options in ({"nsteps": 100, "temp": 300}, {"nsteps": 100, "temp": 300, "nbeads": 0}):
            answer = self.client.post("/api/jobs", headers=self.headers, json={
                "task": "simulate", "molecule": {"smiles": "O"}, "options": options})
            self.assertEqual(200, answer.status_code, answer.text)
            self.wait(answer.json()["id"])
            call = self.run.calls[-1]
            self.assertNotIn("nbeads", call["options"])
            self.assertNotIn("hist", call["options"])
        tasks = {t["name"]: t for t in self.client.get("/api/tasks",
                                                       headers=self.headers).json()["tasks"]}
        self.assertEqual(["hist"], tasks["simulate"]["bead_outputs"])
        beads = [o for o in tasks["simulate"]["options"] if o["name"] == "nbeads"]
        self.assertEqual("int", beads[0]["type"])
        # An option that this stand-in for the program does not have, has no type:
        # the page then does not offer it
        tau = [o for o in tasks["simulate"]["options"] if o["name"] == "tau"]
        self.assertNotIn("type", tau[0])

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
            # A simulation at constant temperature: too many beads, beads
            # that are no number, no temperature, and what needs beads
            {"task": "simulate", "molecule": smiles, "options": {"nbeads": 1000, "temp": 300}},
            {"task": "simulate", "molecule": smiles, "options": {"nbeads": 2.5, "temp": 300}},
            {"task": "simulate", "molecule": smiles, "options": {"nbeads": 8}},
            {"task": "simulate", "molecule": smiles, "options": {"nbeads": 8, "temp": 0}},
            {"task": "simulate", "molecule": smiles, "options": {"rwall": 1.0, "temp": 300}},
            {"task": "simulate", "molecule": smiles, "options": {"monomers": True, "temp": 300}},
            {"task": "simulate", "molecule": smiles, "options": {"thermo": True, "temp": 300}},
            {"task": "simulate", "molecule": smiles,
             "options": {"nbeads": 0, "thermo": True, "temp": 300}},
            # The thermochemistry is that of one compound
            {"task": "simulate", "molecule": smiles,
             "options": {"nbeads": 8, "temp": 300, "thermo": True, "rwall": 1.0}},
            {"task": "simulate", "molecule": smiles,
             "options": {"nbeads": 8, "temp": 300, "thermo": True, "monomers": True}},
            {"task": "simulate", "molecule": smiles,
             "options": {"nbeads": 8, "temp": 300, "thermo": "yes"}},
            {"task": "simulate", "molecule": smiles,
             "options": {"nbeads": 8, "temp": 300, "constrain": "h-bonds"}},
            {"task": "simulate", "molecule": smiles,
             "options": {"nbeads": 8, "temp": 300, "nsteps": 100, "equil": 100}},
            {"task": "simulate", "molecule": smiles,
             "options": {"nbeads": 8, "temp": 300, "nquad": 99}},
            ["not", "an", "object"],
        ]
        for body in wrong:
            answer = self.client.post("/api/jobs", headers=self.headers, json=body)
            self.assertEqual(400, answer.status_code, body)
            self.assertTrue(answer.json()["detail"])
        answer = self.client.post("/api/jobs", headers=self.headers, content=b"{not json")
        self.assertEqual(400, answer.status_code)
        self.assertEqual([], self.run.calls)

    # --- the force field workspace ---

    def test_program_older_than_the_interface(self):
        """!
        @brief A program without an option that the interface always gives
        is reported as too old, before anything is started.
        """
        def old_program():
            info = fake_program_info()
            options = dict(info.commands["minimize"].options)
            del options["json"]
            info.commands["minimize"] = SimpleNamespace(
                options=options, has_option=lambda name: name in options)
            del info.commands["nma"]
            return info

        app = create_app(self.settings, run=self.run, program_info=old_program)
        client = TestClient(app)
        answer = client.post("/api/jobs", headers=self.headers,
                             json={"task": "minimize", "molecule": {"smiles": "CCO"}})
        self.assertEqual(409, answer.status_code)
        detail = answer.json()["detail"]
        self.assertIn("-json of minimize", detail)
        self.assertIn("older than this interface", detail)
        answer = client.post("/api/jobs", headers=self.headers,
                             json={"task": "nma", "molecule": {"smiles": "CCO"}})
        self.assertEqual(409, answer.status_code)
        self.assertIn("does not have the command nma", answer.json()["detail"])
        # What the program has, still runs
        answer = client.post("/api/jobs", headers=self.headers,
                             json={"task": "charges", "molecule": {"smiles": "CCO"}})
        self.assertEqual(200, answer.status_code)

    def test_generator_description(self):
        """!
        @brief The page learns what a force field can be made from.
        """
        self.assertEqual(401, self.client.get("/api/generator").status_code)
        described = self.client.get("/api/generator", headers=self.headers).json()
        self.assertGreater(len(described["atomtypes"]), 50)
        self.assertEqual("WANG_BUCKINGHAM", described["vdw"]["default"])
        self.assertIn("Geometric", described["rules"])
        self.assertEqual(7, len(described["vsites"]))

    def generate(self, **generator):
        """!
        @brief Start a job that makes a force field and wait for it.
        @param generator The choices, two atom types unless given.
        @return The job.
        """
        generator.setdefault("atomtypes", [{"name": "hc"}, {"name": "c3", "shell": True}])
        answer = self.client.post("/api/jobs", headers=self.headers,
                                  json={"task": "gen_ff", "generator": generator})
        self.assertEqual(200, answer.status_code, answer.text)
        return self.wait(answer.json()["id"])

    def test_generate_force_field(self):
        """!
        @brief The choices become the files and the options of gen_ff, in
        the directory of the job, and the files can be fetched.
        """
        job = self.generate(vdw={"potential": "LJ12_6"},
                            vsites={"files": ["vsite4_amine"]})
        self.assertEqual("finished", job["status"])
        call = self.run.calls[0]
        self.assertEqual("gen_ff", call["command"])
        options = call["options"]
        self.assertEqual(call["workdir"] / "atomtypes.csv", options["f"])
        self.assertEqual([call["workdir"] / "vsites.json"], options["vs"])
        self.assertEqual(call["workdir"] / "forcefield.xml", options["o"])
        self.assertEqual(("LJ12_6", "GAUSSIAN", True),
                         (options["vdwfn"], options["qdist"], options["json"]))
        # No molecule and no force field are given to gen_ff
        for absent in ("ff", "smiles", "mp"):
            self.assertNotIn(absent, options)
        table = options["f"].read_text().splitlines()
        self.assertTrue(table[0].startswith("#atomtype|"))
        self.assertEqual(["v4n", "hc", "c3", "c3_s"], [line.split("|")[0] for line in table[1:]])
        self.assertEqual(["c", "f", "o", "vs"], sorted(job["files"]))
        for option, start in (("o", "<alexandria"), ("f", "#atomtype"), ("vs", "{")):
            got = self.client.get("/api/jobs/%s/files/%s" % (job["id"], option),
                                  headers=self.headers)
            self.assertEqual(200, got.status_code, option)
            self.assertTrue(got.text.startswith(start), option)
        # The files have the names the server gave them
        names = {option: self.client.get("/api/jobs/%s/files/%s" % (job["id"], option),
                                         headers=self.headers).headers["content-disposition"]
                 for option in ("o", "f", "vs")}
        self.assertIn('"forcefield.xml"', names["o"])
        self.assertIn('"atomtypes.csv"', names["f"])
        self.assertIn('"vsites.json"', names["vs"])

    def test_generator_choices_that_are_refused(self):
        """!
        @brief Choices that are not in the lists are refused, and nothing
        is run or written.
        """
        hc = [{"name": "hc"}]
        wrong = [
            {"task": "gen_ff"},
            {"task": "gen_ff", "generator": {}},
            {"task": "gen_ff", "generator": {"atomtypes": [{"name": "xx"}]}},
            {"task": "gen_ff", "generator": {"atomtypes": hc, "vdw": {"potential": "-h"}}},
            {"task": "gen_ff", "generator": {"atomtypes": hc,
                                             "vsites": {"files": ["/etc/passwd"]}}},
            # gen_ff takes no options from the request, only choices
            {"task": "gen_ff", "generator": {"atomtypes": hc}, "options": {"o": "/tmp/x.xml"}},
            {"task": "gen_ff", "generator": {"atomtypes": hc}, "options": {"f": "/etc/passwd"}},
            {"task": "gen_ff", "generator": {"atomtypes": hc}, "options": {"cr": "x"}},
        ]
        for body in wrong:
            answer = self.client.post("/api/jobs", headers=self.headers, json=body)
            self.assertEqual(400, answer.status_code, body)
            self.assertTrue(answer.json()["detail"])
        self.assertEqual([], self.run.calls)
        self.assertEqual([], list(self.settings.jobs_dir.glob("*")))

    def test_force_field_of_a_job_is_used_by_the_next(self):
        """!
        @brief A force field that a job wrote is the input of a later job,
        in the force field workspace and for a molecule.
        """
        made = self.generate()
        written = self.run.calls[0]["options"]["o"]
        for body in ({"task": "ff_describe"},
                     {"task": "ff_repair"},
                     {"task": "minimize", "molecule": {"smiles": "CC"}},
                     {"task": "ff_edit", "options": {"p": "chi", "a": "hc_z", "val": 5.5}}):
            body["forcefield"] = {"job": made["id"]}
            answer = self.client.post("/api/jobs", headers=self.headers, json=body)
            self.assertEqual(200, answer.status_code, answer.text)
            self.wait(answer.json()["id"])
            call = self.run.calls[-1]
            self.assertEqual(written, call["options"]["ff"], body["task"])
            self.assertIs(True, call["options"]["json"])
        self.assertEqual(["gen_ff", "edit_ff", "edit_ff", "minimize", "edit_ff"],
                         [call["command"] for call in self.run.calls])
        # Describing writes no file, repairing and editing do
        self.assertNotIn("o", self.run.calls[1]["options"])
        self.assertIs(True, self.run.calls[2]["options"]["validate"])
        self.assertEqual(self.run.calls[2]["workdir"] / "forcefield.xml",
                         self.run.calls[2]["options"]["o"])
        # The force field of the edit can be used in turn
        edited = self.client.get("/api/jobs", headers=self.headers).json()["jobs"][0]
        self.assertEqual("ff_edit", edited["task"])
        answer = self.client.post("/api/jobs", headers=self.headers, json={
            "task": "ff_describe", "forcefield": {"job": edited["id"]}})
        self.assertEqual(200, answer.status_code, answer.text)
        self.wait(answer.json()["id"])
        self.assertEqual(self.run.calls[-2]["options"]["o"], self.run.calls[-1]["options"]["ff"])

    def test_force_fields_that_are_refused(self):
        """!
        @brief A force field of a job must be one that a finished job of a
        task that writes force fields made; the tasks of the force field
        workspace do not fall back on the default.
        """
        made = self.generate()
        minimized = self.client.post("/api/jobs", headers=self.headers, json={
            "task": "minimize", "molecule": {"smiles": "CC"}}).json()
        self.wait(minimized["id"])
        uploaded = self.upload("my.xml", "forcefield", FF).json()["id"]
        ncalls = len(self.run.calls)
        wrong = [
            {"task": "ff_describe"},
            {"task": "ff_describe", "forcefield": {}},
            {"task": "ff_describe", "forcefield": {"job": "nosuchjob"}},
            {"task": "ff_describe", "forcefield": {"job": "../" + made["id"]}},
            {"task": "ff_describe", "forcefield": {"job": 7}},
            # A job that wrote no force field
            {"task": "ff_describe", "forcefield": {"job": minimized["id"]}},
            {"task": "ff_describe", "forcefield": {"job": made["id"], "file": uploaded}},
            {"task": "ff_describe", "forcefield": "default"},
            {"task": "ff_describe", "forcefield": {"default": "yes"}},
            {"task": "minimize", "molecule": {"smiles": "CC"},
             "forcefield": {"job": minimized["id"]}},
            {"task": "ff_describe", "forcefield": {"file": uploaded}, "options": {"o": "x.xml"}},
            {"task": "ff_repair", "forcefield": {"file": uploaded},
             "options": {"validate": False}},
        ]
        for body in wrong:
            answer = self.client.post("/api/jobs", headers=self.headers, json=body)
            self.assertEqual(400, answer.status_code, body)
        self.assertEqual(ncalls, len(self.run.calls))
        # An uploaded force field is fine, and so is the default when it is asked for
        answer = self.client.post("/api/jobs", headers=self.headers, json={
            "task": "ff_describe", "forcefield": {"file": uploaded}})
        self.assertEqual(200, answer.status_code)
        self.wait(answer.json()["id"])
        answer = self.client.post("/api/jobs", headers=self.headers, json={
            "task": "ff_describe", "forcefield": {"default": True}})
        self.assertEqual(200, answer.status_code)
        self.wait(answer.json()["id"])
        self.assertEqual(self.settings.default_forcefield, self.run.calls[-1]["options"]["ff"])

    def test_force_field_of_a_job_that_is_not_done(self):
        """!
        @brief The force field of a job that still runs, or that wrote
        none, cannot be used.
        """
        blocked = []
        original = self.run.__class__.__call__

        def slow(run, command, **kwargs):
            if command == "gen_ff":
                kwargs["behaviour"] = blocked and "unchanged" or "block"
                blocked.append(command)
            return original(run, command, **kwargs)
        self.run.__class__.__call__ = slow
        try:
            body = {"task": "gen_ff", "generator": {"atomtypes": [{"name": "hc"}]}}
            running = self.client.post("/api/jobs", headers=self.headers, json=body).json()
            self.wait(running["id"], ("running",))
            use = {"task": "ff_describe", "forcefield": {"job": running["id"]}}
            self.assertEqual(400, self.client.post("/api/jobs", headers=self.headers,
                                                   json=use).status_code)
            self.run.release.set()
            self.wait(running["id"])
            self.assertEqual(200, self.client.post("/api/jobs", headers=self.headers,
                                                   json=use).status_code)
            # A job that finished without writing the file
            empty = self.client.post("/api/jobs", headers=self.headers, json=body).json()
            self.assertNotIn("o", self.wait(empty["id"])["files"])
            use["forcefield"]["job"] = empty["id"]
            answer = self.client.post("/api/jobs", headers=self.headers, json=use)
            self.assertEqual(400, answer.status_code)
            self.assertIn("make it again", answer.json()["detail"])
        finally:
            self.run.__class__.__call__ = original

    def test_edit_force_field(self):
        """!
        @brief A change names a parameter and the types it is changed for.
        Several types are given to edit_ff as one argument.
        """
        uploaded = {"file": self.upload("my.xml", "forcefield", FF).json()["id"]}
        answer = self.client.post("/api/jobs", headers=self.headers, json={
            "task": "ff_edit", "forcefield": uploaded,
            "options": {"p": "bondlength", "a": ["c3_b~h_b", "c3_b=o_b", "Na+", "Cl-"],
                        "val": 0, "min": -1.5, "max": 2, "mut": "Fixed", "nonneg": "no",
                        "force": True}})
        self.assertEqual(200, answer.status_code, answer.text)
        self.wait(answer.json()["id"])
        options = self.run.calls[0]["options"]
        self.assertEqual("c3_b~h_b c3_b=o_b Na+ Cl-", options["a"])
        self.assertEqual(("bondlength", 0, -1.5, 2, "Fixed", "no", True),
                         tuple(options[k] for k in ("p", "val", "min", "max", "mut", "nonneg",
                                                    "force")))
        self.assertEqual(self.run.calls[0]["workdir"] / "forcefield.xml", options["o"])
        # A change of the mutability is forced, since edit_ff does not touch
        # a fixed parameter otherwise; another change is not, unless asked
        for given, force in (({"mut": "Bounded"}, True), ({"mut": "Fixed", "val": 1}, True),
                             ({"mut": "Bounded", "force": False}, False), ({"val": 1}, None),
                             ({"min": 0, "max": 2}, None)):
            answer = self.client.post("/api/jobs", headers=self.headers, json={
                "task": "ff_edit", "forcefield": uploaded,
                "options": dict(given, p="chi", a="h_z")})
            self.assertEqual(200, answer.status_code, answer.text)
            self.wait(answer.json()["id"])
            self.assertEqual(force, self.run.calls[-1]["options"].get("force"), given)
        ncalls = len(self.run.calls)
        wrong = [
            {},
            {"p": "chi"},
            {"a": "h_z", "val": 1},
            {"p": "chi", "a": "h_z"},
            {"p": "chi", "val": 1},
            {"p": "-del", "a": "h_z", "val": 1},
            {"p": "chi zeta", "a": "h_z", "val": 1},
            {"p": "chi", "a": "-del", "val": 1},
            {"p": "chi", "a": "h_z -del", "val": 1},
            {"p": "chi", "a": ["h_z", "-force"], "val": 1},
            {"p": "chi", "a": "h_z;rm", "val": 1},
            {"p": "chi", "a": [], "val": 1},
            {"p": "chi", "a": ["h_z"] * 201, "val": 1},
            {"p": "chi", "a": 5, "val": 1},
            {"p": "chi", "a": "h_z", "val": "1"},
            {"p": "chi", "a": "h_z", "val": True},
            {"p": "chi", "a": "h_z", "min": "low"},
            {"p": "chi", "a": "h_z", "mut": "Free"},
            {"p": "chi", "a": "h_z", "nonneg": "maybe"},
            {"p": "chi", "a": "h_z", "val": 1, "force": "yes"},
            # Options of edit_ff that the task does not offer
            {"p": "chi", "a": "h_z", "val": 1, "del": True},
            {"p": "chi", "a": "h_z", "val": 1, "ff2": "/etc/passwd"},
            {"p": "chi", "a": "h_z", "val": 1, "o": "/tmp/x.xml"},
            {"p": "chi", "a": "h_z", "val": 1, "dump": "/tmp/x.dat"},
        ]
        for options in wrong:
            answer = self.client.post("/api/jobs", headers=self.headers, json={
                "task": "ff_edit", "forcefield": uploaded, "options": options})
            self.assertEqual(400, answer.status_code, options)
        self.assertEqual(ncalls, len(self.run.calls))

    def test_geometry(self):
        """!
        @brief geometry_ff gets a force field and an uploaded molprop file.
        """
        made = self.generate()
        molprop = self.upload("mols.xml", "molprop", MP).json()["id"]
        answer = self.client.post("/api/jobs", headers=self.headers, json={
            "task": "geometry_ff", "forcefield": {"job": made["id"]},
            "molprop": {"file": molprop},
            "options": {"gen_bcc": False, "bfactor": 0.9, "afactor": 0.95, "factor": 0.5}})
        self.assertEqual(200, answer.status_code, answer.text)
        job = self.wait(answer.json()["id"])
        call = self.run.calls[-1]
        self.assertEqual("geometry_ff", call["command"])
        options = call["options"]
        self.assertEqual([self.settings.uploads_dir / (molprop + ".xml")], options["mp"])
        self.assertEqual(self.run.calls[0]["options"]["o"], options["ff"])
        self.assertEqual(call["workdir"] / "forcefield.xml", options["o"])
        self.assertEqual((False, 0.9, 0.95, 0.5, True, 50),
                         tuple(options[k] for k in ("gen_bcc", "bfactor", "afactor", "factor",
                                                    "json", "samples")))
        # The distributions are analysed, since this program can
        self.assertIs(True, options["analyse"])
        # Its force field can be used in turn
        answer = self.client.post("/api/jobs", headers=self.headers, json={
            "task": "ff_describe", "forcefield": {"job": job["id"]}})
        self.assertEqual(200, answer.status_code)
        ncalls = len(self.run.calls)
        forcefield = self.upload("my.xml", "forcefield", FF).json()["id"]
        wrong = [
            {"task": "geometry_ff", "forcefield": {"job": made["id"]}},
            {"task": "geometry_ff", "molprop": {"file": molprop}},
            {"task": "geometry_ff", "forcefield": {"job": made["id"]},
             "molprop": {"file": forcefield}},
            {"task": "geometry_ff", "forcefield": {"job": made["id"]},
             "molprop": {"file": "../" + molprop}},
            {"task": "geometry_ff", "forcefield": {"job": made["id"]}, "molprop": molprop},
            {"task": "geometry_ff", "forcefield": {"job": made["id"]},
             "molprop": {"file": molprop}, "options": {"bfactor": 5}},
            {"task": "geometry_ff", "forcefield": {"job": made["id"]},
             "molprop": {"file": molprop}, "options": {"histo": True}},
            {"task": "geometry_ff", "forcefield": {"job": made["id"]},
             "molprop": {"file": molprop}, "options": {"sel": "/etc/passwd"}},
        ]
        for body in wrong:
            answer = self.client.post("/api/jobs", headers=self.headers, json=body)
            self.assertEqual(400, answer.status_code, body)
        self.wait(self.client.get("/api/jobs", headers=self.headers).json()["jobs"][0]["id"])
        self.assertEqual(ncalls, len(self.run.calls))

    def test_describe_molprop_file(self):
        """!
        @brief edit_mp gets the uploaded molprop file, and is asked to
        describe and to validate it. Nothing else can be asked for.
        """
        molprop = self.upload("mols.xml", "molprop", MP).json()["id"]
        answer = self.client.post("/api/jobs", headers=self.headers, json={
            "task": "mp_describe", "molprop": {"file": molprop}})
        self.assertEqual(200, answer.status_code, answer.text)
        self.wait(answer.json()["id"])
        call = self.run.calls[0]
        self.assertEqual("edit_mp", call["command"])
        self.assertEqual({"mp": [self.settings.uploads_dir / (molprop + ".xml")],
                          "json": True, "validate": True, "molinfo": False}, call["options"])
        forcefield = self.upload("my.xml", "forcefield", FF).json()["id"]
        wrong = [
            {"task": "mp_describe"},
            {"task": "mp_describe", "molprop": {}},
            {"task": "mp_describe", "molprop": {"file": forcefield}},
            {"task": "mp_describe", "molprop": {"file": molprop}, "options": {"o": "/tmp/x"}},
            {"task": "mp_describe", "molprop": {"file": molprop}, "options": {"db": "/tmp/x"}},
            {"task": "mp_describe", "molprop": {"file": molprop},
             "options": {"validate": False}},
        ]
        for body in wrong:
            answer = self.client.post("/api/jobs", headers=self.headers, json=body)
            self.assertEqual(400, answer.status_code, body)
        self.assertEqual(1, len(self.run.calls))

    def test_particles_of_a_calculation(self):
        """!
        @brief gentop gets a calculation of the molprop file as an SDF file
        with the force field of the comparison, to make its shells and
        virtual sites. The force field has to be given, and the calculation
        has to be in the file.
        """
        molprop = self.upload("mols.xml", "molprop", ION_WATER.encode()).json()["id"]
        forcefield = self.upload("my.xml", "forcefield", FF).json()["id"]
        calculation = {"molprop": molprop, "compound": "water#sodium", "datafile": "0719"}
        answer = self.client.post("/api/jobs", headers=self.headers, json={
            "task": "mp_particles", "forcefield": {"file": forcefield},
            "calculation": calculation})
        self.assertEqual(200, answer.status_code, answer.text)
        self.wait(answer.json()["id"])
        call = self.run.calls[-1]
        self.assertEqual("gentop", call["command"])
        options = call["options"]
        self.assertEqual(self.settings.uploads_dir / (forcefield + ".xml"), options["ff"])
        self.assertTrue(options["json"])
        self.assertEqual("calculation.sdf", options["f"].name)
        self.assertEqual("particles.pdb", options["c"].name)
        self.assertIn("M  CHG  1   4   1", options["f"].read_text())
        ncalls = len(self.run.calls)
        wrong = [
            {"task": "mp_particles", "calculation": calculation},
            {"task": "mp_particles", "forcefield": {"file": forcefield}},
            {"task": "mp_particles", "forcefield": {"file": forcefield},
             "calculation": dict(calculation, molprop=forcefield)},
            {"task": "mp_particles", "forcefield": {"file": forcefield},
             "calculation": dict(calculation, datafile="9999")},
            {"task": "mp_particles", "forcefield": {"file": forcefield},
             "calculation": dict(calculation, compound=None)},
            {"task": "mp_particles", "forcefield": {"file": forcefield},
             "calculation": dict(calculation, compound="x" * 301)},
            {"task": "mp_particles", "forcefield": {"file": forcefield},
             "calculation": "water#sodium"},
            {"task": "mp_particles", "forcefield": {"file": forcefield},
             "calculation": calculation, "options": {"db": "water"}},
        ]
        for body in wrong:
            answer = self.client.post("/api/jobs", headers=self.headers, json=body)
            self.assertEqual(400, answer.status_code, body)
        self.assertEqual(ncalls, len(self.run.calls))

    def test_geometry_with_an_older_program(self):
        """!
        @brief A program without geometry_ff -analyse extracts the
        geometries all the same, without the analysis of the distributions.
        """
        def older():
            info = fake_program_info()
            options = {name: option for name, option
                       in info.commands["geometry_ff"].options.items() if name != "analyse"}
            info.commands["geometry_ff"] = SimpleNamespace(
                options=options, has_option=lambda name: name in options)
            return info
        app = create_app(self.settings, run=self.run, program_info=older)
        self.addCleanup(app.state.jobs.shutdown)
        client = TestClient(app)
        forcefield = client.post("/api/uploads", params={"kind": "forcefield", "name": "f.xml"},
                                 content=FF, headers=self.headers).json()["id"]
        molprop = client.post("/api/uploads", params={"kind": "molprop", "name": "m.xml"},
                              content=MP, headers=self.headers).json()["id"]
        answer = client.post("/api/jobs", headers=self.headers, json={
            "task": "geometry_ff", "forcefield": {"file": forcefield}, "molprop": {"file": molprop}})
        self.assertEqual(200, answer.status_code, answer.text)
        for _ in range(500):
            job = client.get("/api/jobs/" + answer.json()["id"], headers=self.headers).json()
            if job["status"] not in ("queued", "running"):
                break
            time.sleep(0.01)
        self.assertEqual("finished", job["status"], job["error"])
        options = self.run.calls[-1]["options"]
        self.assertNotIn("analyse", options)
        self.assertEqual((True, 50), (options["json"], options["samples"]))

    def test_structure_from_a_molprop_file(self):
        """!
        @brief The structure of one calculation of a compound in an uploaded
        molprop file, found by the name or the IUPAC name, with its atoms in
        the order of the file; a compound, calculation or file that is not
        there is told.
        """
        from tests.test_evaluation import MOLPROP  # pylint: disable=import-outside-toplevel
        molprop = self.client.post("/api/uploads", params={"kind": "molprop", "name": "m.xml"},
                                   content=MOLPROP.encode(), headers=self.headers).json()["id"]

        def get(upload, **params):
            return self.client.get("/api/molprops/%s/structure" % upload, params=params,
                                   headers=self.headers)
        for name in ("w2", "water#water"):
            answer = get(molprop, molecule=name, calculation="0007")
            self.assertEqual(200, answer.status_code, answer.text)
            told = answer.json()
            self.assertEqual((name, "0007"), (told["molecule"], told["calculation"]))
            self.assertEqual(["O", "H", "H", "O", "H", "Cl"], [a["element"] for a in told["atoms"]])
            # In Angstrom, whatever the unit of the file
            self.assertAlmostEqual(3.0, told["atoms"][3]["x"])
            self.assertAlmostEqual(3.96, told["atoms"][4]["x"])
        for params in ({"molecule": "w2", "calculation": "0099"},
                       {"molecule": "nobody", "calculation": "0007"}):
            answer = get(molprop, **params)
            self.assertEqual(404, answer.status_code, params)
            self.assertIn("has no calculation", answer.json()["detail"])
        self.assertEqual(404, get("nosuchfile", molecule="w2", calculation="0007").status_code)
        self.assertEqual(422, get(molprop, molecule="w2").status_code)
        # Not another kind of upload
        forcefield = self.client.post("/api/uploads", params={"kind": "forcefield", "name": "f.xml"},
                                      content=FF, headers=self.headers).json()["id"]
        self.assertEqual(404, get(forcefield, molecule="w2", calculation="0007").status_code)
        # Only with the token
        self.assertEqual(401, self.client.get("/api/molprops/%s/structure" % molprop,
                                              params={"molecule": "w2", "calculation": "0007"}).status_code)

    def test_geometry_histograms(self):
        """!
        @brief The result of geometry_ff keeps which histograms there are;
        one histogram, with where its values come from, is read from the
        file with the whole result when the page asks for it.
        """
        from tests.test_geometry import RESULT  # pylint: disable=import-outside-toplevel

        def run(command, *, workdir, **kwargs):
            result = self.run(command, workdir=workdir, **kwargs)
            if command == "geometry_ff":
                whole = Path(workdir) / "geometry_ff.json"
                whole.write_text(json.dumps({"geometry_ff": RESULT}))
                result.data = RESULT
                result.files["json"] = [whole]
            return result
        app = create_app(self.settings, run=run, program_info=fake_program_info)
        self.addCleanup(app.state.jobs.shutdown)
        client = TestClient(app)
        forcefield = client.post("/api/uploads", params={"kind": "forcefield", "name": "f.xml"},
                                 content=FF, headers=self.headers).json()["id"]
        molprop = client.post("/api/uploads", params={"kind": "molprop", "name": "m.xml"},
                              content=MP, headers=self.headers).json()["id"]
        answer = client.post("/api/jobs", headers=self.headers, json={
            "task": "geometry_ff", "forcefield": {"file": forcefield}, "molprop": {"file": molprop}})
        self.assertEqual(200, answer.status_code, answer.text)
        for _ in range(500):
            job = client.get("/api/jobs/" + answer.json()["id"], headers=self.headers).json()
            if job["status"] not in ("queued", "running"):
                break
            time.sleep(0.01)
        self.assertEqual("finished", job["status"], job["error"])
        self.assertEqual(["c3_b~c3_b", "c3_b~h_b"],
                         [h["identifier"] for h in job["result"]["histograms"]["BONDS"]])
        self.assertNotIn("bins", job["result"]["histograms"]["BONDS"][0])
        self.assertNotIn("analysis", job["result"])

        def get(**params):
            return client.get("/api/jobs/%s/histograms" % job["id"], params=params,
                              headers=self.headers)
        one = get(type="BONDS", identifier="c3_b~h_b")
        self.assertEqual(200, one.status_code, one.text)
        self.assertEqual("C2-H5", one.json()["bins"][1]["samples"][0]["names"])
        for wrong in ({"type": "BONDS", "identifier": "x"}, {"type": "BONDS"}, {}):
            self.assertIn(get(**wrong).status_code, (404, 422), wrong)
        self.assertEqual(401, client.get("/api/jobs/%s/histograms" % job["id"],
                                         params={"type": "BONDS", "identifier": "c3_b~h_b"}
                                         ).status_code)

    def test_dimer_scan(self):
        """!
        @brief A scan of the distance in a dimer is one job: gentop, then a
        minimization for each distance, each point reported as progress;
        the structure of a point and the energies as xvg can be fetched.
        """
        from tests.test_dimerscan import FakeAlexandria  # pylint: disable=import-outside-toplevel
        alexandria = FakeAlexandria()
        app = create_app(self.settings, run=alexandria, program_info=fake_program_info)
        self.addCleanup(app.state.jobs.shutdown)
        client = TestClient(app)
        body = {"task": "dimer_scan", "molecule": {"smiles": "O.O"},
                "scan": {"atom1": 1, "atom2": 4, "rmin": 0.25, "rmax": 0.29, "step": 0.01},
                "options": {"qalg": "SQE"}}
        answer = client.post("/api/jobs", headers=self.headers, json=body)
        self.assertEqual(200, answer.status_code, answer.text)
        for _ in range(500):
            job = client.get("/api/jobs/" + answer.json()["id"], headers=self.headers).json()
            if job["status"] not in ("queued", "running"):
                break
            time.sleep(0.01)
        self.assertEqual("finished", job["status"], job["error"])
        self.assertEqual(["gentop"] + ["minimize"] * 5, [call[0] for call in alexandria.calls])
        self.assertEqual(5, len(job["result"]["points"]))
        self.assertEqual(job["result"]["points"], job["progress"])
        self.assertIn("xvg", job["files"])
        structure = client.get("/api/jobs/%s/scan/2" % job["id"], headers=self.headers)
        self.assertEqual(200, structure.status_code)
        self.assertIn("ATOM      4", structure.text)
        self.assertEqual(404, client.get("/api/jobs/%s/scan/7" % job["id"],
                                         headers=self.headers).status_code)
        xvg = client.get("/api/jobs/%s/files/xvg" % job["id"], headers=self.headers)
        self.assertEqual(200, xvg.status_code)
        self.assertIn('legend "EPOT"', xvg.text)
        for wrong in (dict(body, scan={"atom1": 1}), {k: v for k, v in body.items() if k != "scan"},
                      dict(body, scan=dict(body["scan"], step=0)),
                      dict(body, options={"freeze": "/etc/passwd"})):
            self.assertEqual(400, client.post("/api/jobs", headers=self.headers,
                                              json=wrong).status_code, wrong)
        other = client.post("/api/jobs", headers=self.headers,
                            json={"task": "minimize", "molecule": {"smiles": "C"}}).json()
        self.assertEqual(404, client.get("/api/jobs/%s/scan/0" % other["id"],
                                         headers=self.headers).status_code)

    def test_second_virial(self):
        """!
        @brief b2 gets the dimer, the force field, the temperatures and the
        number of orientations, on as many processes as asked for; the
        page gets the coefficients by temperature.
        """
        from tests.test_virial import RESULT  # pylint: disable=import-outside-toplevel

        def run(command, *, workdir, **kwargs):
            result = self.run(command, workdir=workdir, **kwargs)
            if command == "b2":
                result.data = RESULT
            return result
        app = create_app(self.settings, run=run, program_info=fake_program_info)
        self.addCleanup(app.state.jobs.shutdown)
        client = TestClient(app)
        body = {"task": "b2", "molecule": {"smiles": "O.O"}, "processes": 2,
                "options": {"T1": 300, "T2": 400, "dT": 50, "maxdimer": 64}}
        answer = client.post("/api/jobs", headers=self.headers, json=body)
        self.assertEqual(200, answer.status_code, answer.text)
        for _ in range(500):
            job = client.get("/api/jobs/" + answer.json()["id"], headers=self.headers).json()
            if job["status"] not in ("queued", "running"):
                break
            time.sleep(0.01)
        self.assertEqual("finished", job["status"], job["error"])
        call = self.run.calls[-1]
        self.assertEqual("b2", call["command"])
        self.assertEqual((300, 400, 50, 64, True, 2), tuple(call["options"][k] for k in (
            "T1", "T2", "dT", "maxdimer", "json", "processes")))
        self.assertEqual(call["workdir"] / "B2T.xvg", call["options"]["b2"])
        self.assertEqual([300.0, 350.0], job["result"]["virial"]["temperatures"])
        for wrong in (dict(body, options={"T1": 0}), dict(body, options={"maxdimer": 10 ** 9}),
                      dict(body, processes=0), dict(body, options={"b2": "/tmp/x"})):
            self.assertEqual(400, client.post("/api/jobs", headers=self.headers,
                                              json=wrong).status_code, wrong)

    def test_clean_molprop_file(self):
        """!
        @brief edit_mp writes the molprop file as it validated it: without
        the calculations that are there twice, with the names of the file.
        The file can be fetched; nothing else can be asked for.
        """
        molprop = self.upload("mols.xml", "molprop", MP).json()["id"]
        answer = self.client.post("/api/jobs", headers=self.headers, json={
            "task": "mp_clean", "molprop": {"file": molprop}})
        self.assertEqual(200, answer.status_code, answer.text)
        job = self.wait(answer.json()["id"])
        call = self.run.calls[0]
        self.assertEqual("edit_mp", call["command"])
        self.assertEqual({"mp": [self.settings.uploads_dir / (molprop + ".xml")],
                          "validate": True, "molinfo": False,
                          "o": call["workdir"] / "cleaned.xml"}, call["options"])
        self.assertEqual("finished", job["status"], job["error"])
        for body in ({"task": "mp_clean"},
                     {"task": "mp_clean", "molprop": {"file": molprop}, "options": {"o": "/tmp/x"}},
                     {"task": "mp_clean", "molprop": {"file": molprop},
                      "options": {"validate": False}}):
            answer = self.client.post("/api/jobs", headers=self.headers, json=body)
            self.assertEqual(400, answer.status_code, body)
        self.assertEqual(1, len(self.run.calls))

    def test_geometry_of_chosen_compounds(self):
        """!
        @brief geometry_ff gets a selection when the user left compounds
        out, and none otherwise: it then takes all compounds of the file.
        """
        client, _ = self.evaluation_app()

        def done(answer):
            self.assertEqual(200, answer.status_code, answer.text)
            for _ in range(500):
                job = client.get("/api/jobs/" + answer.json()["id"], headers=self.headers).json()
                if job["status"] in ("finished", "failed", "cancelled"):
                    return job
                time.sleep(0.01)
            self.fail("The job did not finish")
            return None

        def post(**molprop):
            return client.post("/api/jobs", headers=self.headers, json={
                "task": "geometry_ff", "forcefield": {"file": forcefield}, "molprop": molprop})
        molprop = client.post("/api/uploads", params={"kind": "molprop", "name": "mols.xml"},
                              content=MP, headers=self.headers).json()["id"]
        forcefield = client.post("/api/uploads", params={"kind": "forcefield", "name": "ff.xml"},
                                 content=FF, headers=self.headers).json()["id"]
        described = done(client.post("/api/jobs", headers=self.headers, json={
            "task": "mp_describe", "molprop": {"file": molprop}}))["id"]
        job = done(post(file=molprop, described=described,
                        sets={"test": [], "ignore": ["methanol#water"]}))
        self.assertEqual("finished", job["status"], job["error"])
        call = self.run.calls[-1]
        self.assertEqual("geometry_ff", call["command"])
        self.assertEqual(call["workdir"] / "selection.dat", call["options"]["sel"])
        self.assertEqual("water#water|Train\n", call["options"]["sel"].read_text())
        # All compounds: no selection, also when the file was read
        for request in ({"file": molprop}, {"file": molprop, "described": described}):
            job = done(post(**request))
            self.assertEqual("finished", job["status"], job["error"])
            self.assertNotIn("sel", self.run.calls[-1]["options"])
        ncalls = len(self.run.calls)
        for wrong in ({"file": molprop, "sets": {"ignore": ["methanol#water"]}},
                      {"file": molprop, "described": "nosuchjob", "sets": {"ignore": []}},
                      {"file": molprop, "described": described, "sets": {"ignore": ["acetone"]}},
                      {"file": molprop, "described": described,
                       "sets": {"ignore": ["water#water", "methanol#water"]}}):
            self.assertEqual(400, post(**wrong).status_code, wrong)
        self.assertEqual(ncalls, len(self.run.calls))

    def evaluation_app(self):
        """!
        @brief An application of which the jobs give what edit_mp and
        train_ff give: the compounds of a file, and statistics with a file
        that compares the values.
        @return The client and the list of calls of the stand-in for plotXVG.
        """
        plotted = []

        def run(command, *, workdir, **kwargs):
            result = self.run(command, workdir=workdir, **kwargs)
            if command == "edit_mp":
                result.data = {"summary": {"molecules": 2},
                               "molecules": [{"name": "w2", "iupac": "water#water"},
                                             {"name": "methanol#water", "iupac": ""}],
                               "validation": {"checked": True, "ok": True}}
            elif command == "train_ff":
                (Path(workdir) / "INTERACTION.xvg").write_text(
                    '@ s0 legend "Train"\n-20 -19\n-5 -6\n&\n')
                corr = Path(workdir) / "epot_corr.xvg"
                corr.write_text('@ s0 legend "Train"\n1 2\n&\n')
                result.files["epotcorr"] = [corr]
                result.data = {"molecules": {"0": {"name": "water#water", "dataset": "Train",
                                                   "atoms": {"0": {"fx": 1}},
                                                   "interaction_energies": {"c": {}}}},
                               "statistics": {"Train": {"INTERACTION": {"N": 2, "RMSD": 1.0}}},
                               "outliers": {}}
                # The whole result is in a file, as the program writes it
                whole = Path(workdir) / "train_ff.json"
                whole.write_text(json.dumps({"train_ff": {"molecules": {"0": {
                    "name": "water#water", "dataset": "Train", "atoms": {"0": {"fx": 1}},
                    "interaction_energies": {
                        "calculation-0007": {"INTERACTION": {"QM": -20, "ACT": -19}},
                        "calculation-0008": {"INTERACTION": {"QM": -5, "ACT": -6}}}}}}}))
                result.files["json"] = [whole]
                if kwargs.get("optimize") is not False \
                        and kwargs.get("optimizer") in ("GA", "HYBRID"):
                    # The individuals that the training ended with
                    pool = Path(workdir) / "genepool.csv"
                    pool.write_text(" 0.3 1.0\n" * kwargs.get("pop_size", 1))
                    result.files["gpout"] = [pool]
                if kwargs.get("optimize") is not False:
                    # A training
                    result.data["parameters"] = {"0": {"class": "sigma", "name": "ow",
                                                       "best": 0.3, "stdev": 0.01}}
                    result.data["sensitivity"] = {"mode": "1D", "minimum": "yes"}
            return result

        def plotter(xvg, pdf, series=(), reverse_x=False, **more):
            plotted.append((Path(xvg).name, more))
            Path(pdf).write_bytes(b"%PDF-1.4 fake")
            return Path(pdf)
        app = create_app(self.settings, run=run, program_info=fake_program_info,
                         plotter=plotter)
        self.addCleanup(app.state.jobs.shutdown)
        return TestClient(app), plotted

    def test_evaluate_force_field(self):
        """!
        @brief train_ff gets the force field, the molprop file, a selection
        of all its compounds and the weights of the properties, and does
        not optimize. The page gets the statistics and the files, not the
        atoms.
        """
        client, plotted = self.evaluation_app()

        def post(body):
            return client.post("/api/jobs", headers=self.headers, json=body)

        def done(answer):
            self.assertEqual(200, answer.status_code, answer.text)
            for _ in range(500):
                job = client.get("/api/jobs/" + answer.json()["id"], headers=self.headers).json()
                if job["status"] in ("finished", "failed", "cancelled"):
                    return job
                time.sleep(0.01)
            self.fail("The job did not finish")
            return None
        molprop = client.post("/api/uploads", params={"kind": "molprop", "name": "mols.xml"},
                              content=MP, headers=self.headers).json()["id"]
        described = done(post({"task": "mp_describe", "molprop": {"file": molprop}}))
        body = {"task": "evaluate", "forcefield": {"default": True},
                "molprop": {"file": molprop, "described": described["id"]},
                "observables": ["inter", "epot"]}
        job = done(post(body))
        self.assertEqual("finished", job["status"], job["error"])
        call = self.run.calls[-1]
        self.assertEqual("train_ff", call["command"])
        options = call["options"]
        self.assertEqual({"ff": self.settings.default_forcefield,
                          "mp": [self.settings.uploads_dir / (molprop + ".xml")],
                          "sel": call["workdir"] / "selection.dat",
                          "fc_inter": 1, "fc_epot": 1, "epotcorr": True,
                          "json": True, "optimize": False}, options)
        self.assertEqual("water#water|Train\nmethanol#water|Train\n", options["sel"].read_text())
        # What the page gets
        self.assertEqual({"Train": {"INTERACTION": {"N": 2, "RMSD": 1.0}}},
                         job["result"]["statistics"])
        self.assertEqual([{"name": "water#water", "dataset": "Train", "points": 1}],
                         job["result"]["compounds"])
        self.assertNotIn("molecules", job["result"])
        self.assertIn("INTERACTION", job["files"])
        self.assertIn("epotcorr", job["files"])
        data = client.get("/api/jobs/%s/files/INTERACTION" % job["id"], headers=self.headers)
        self.assertEqual(200, data.status_code)
        self.assertIn("-20 -19", data.text)
        # The energies of one compound, from the file with the whole result
        one = client.get("/api/jobs/%s/compounds/0" % job["id"], headers=self.headers)
        self.assertEqual(200, one.status_code, one.text)
        self.assertEqual({"name": "water#water", "dataset": "Train", "truncated": False,
                          "energies": {"INTERACTION": {"reference": [-20, -5],
                                                       "forcefield": [-19, -6]}}}, one.json())
        self.assertEqual(404, client.get("/api/jobs/%s/compounds/1" % job["id"],
                                         headers=self.headers).status_code)
        self.assertEqual(404, client.get("/api/jobs/%s/compounds/0" % described["id"],
                                         headers=self.headers).status_code)
        self.assertEqual(404, client.get("/api/jobs/nosuchjob/compounds/0",
                                         headers=self.headers).status_code)
        self.assertEqual(401, client.get("/api/jobs/%s/compounds/0" % job["id"]).status_code)
        # The file itself is not handed out
        self.assertEqual(404, client.get("/api/jobs/%s/files/json" % job["id"],
                                         headers=self.headers).status_code)
        self.assertEqual(404, client.get("/api/jobs/%s/files/COULOMB" % job["id"],
                                         headers=self.headers).status_code)
        # The plot on paper compares the values point by point
        pdf = client.get("/api/jobs/%s/plots/INTERACTION" % job["id"], headers=self.headers)
        self.assertEqual(200, pdf.status_code)
        self.assertEqual([("INTERACTION.xvg", {"scatter": True})], plotted)
        self.assertEqual(404, client.get("/api/jobs/%s/plots/sel" % job["id"],
                                         headers=self.headers).status_code)

        ncalls = len(self.run.calls)
        minimized = done(post({"task": "minimize", "molecule": {"smiles": "CC"}}))
        ncalls += 1
        good = {"file": molprop, "described": described["id"]}
        wrong = [
            dict(body, observables=[]),
            dict(body, observables=["inter", "inter"]),
            dict(body, observables=["optimize"]),
            dict(body, observables="inter"),
            {k: v for k, v in body.items() if k != "observables"},
            {k: v for k, v in body.items() if k != "forcefield"},
            dict(body, molprop={"file": molprop}),
            dict(body, molprop={"file": molprop, "described": "nosuchjob"}),
            dict(body, molprop={"file": molprop, "described": minimized["id"]}),
            dict(body, molprop={"file": molprop, "described": 3}),
            dict(body, molprop={"described": described["id"]}),
            dict(body, molprop=good, options={"optimize": True}),
            dict(body, molprop=good, options={"max_iter": 100}),
            dict(body, molprop=good, options={"o": "/tmp/ff.xml"}),
            dict(body, molprop=good, options={"sel": "/etc/passwd"}),
            dict(body, molprop=good, options={"fit": "sigma"}),
        ]
        for request in wrong:
            self.assertEqual(400, post(request).status_code, request)
        self.assertEqual(ncalls, len(self.run.calls))

    def test_points_structures_and_choices(self):
        """!
        @brief A comparison remembers the properties that were chosen, in
        their order. The page gets the points of an energy term with their
        compounds and calculations, and for one calculation its energies
        and its structure from the molprop file that is open. A selection
        file of the user goes to train_ff as it is.
        """
        from tests.test_evaluation import MOLPROP  # pylint: disable=import-outside-toplevel
        client, _ = self.evaluation_app()

        def done(answer):
            self.assertEqual(200, answer.status_code, answer.text)
            for _ in range(500):
                job = client.get("/api/jobs/" + answer.json()["id"], headers=self.headers).json()
                if job["status"] in ("finished", "failed", "cancelled"):
                    return job
                time.sleep(0.01)
            self.fail("The job did not finish")
            return None

        def post(body):
            return client.post("/api/jobs", headers=self.headers, json=body)
        molprop = client.post("/api/uploads", params={"kind": "molprop", "name": "mols.xml"},
                              content=MP, headers=self.headers).json()["id"]
        described = done(post({"task": "mp_describe", "molprop": {"file": molprop}}))
        body = {"task": "evaluate", "forcefield": {"default": True},
                "molprop": {"file": molprop, "described": described["id"]},
                "observables": ["disp", "inter"]}
        job = done(post(body))
        self.assertEqual("finished", job["status"], job["error"])
        self.assertEqual(["disp", "inter"], job["observables"])
        self.assertEqual([], done(post({"task": "minimize",
                                        "molecule": {"smiles": "CC"}}))["observables"])

        def get(path, **params):
            return client.get("/api/jobs/%s/%s" % (job["id"], path), params=params,
                              headers=self.headers)
        points = get("points/INTERACTION")
        self.assertEqual(200, points.status_code, points.text)
        self.assertEqual({"term": "INTERACTION", "truncated": False, "reference": [-20, -5],
                          "forcefield": [-19, -6], "dataset": ["Train", "Train"],
                          "compound": [0, 0],
                          "calculation": ["calculation-0007", "calculation-0008"]}, points.json())
        for wrong in ("epotcorr", "json", "x"):
            self.assertEqual(404, get("points/" + wrong).status_code, wrong)
        self.assertEqual(404, client.get("/api/jobs/%s/points/INTERACTION" % described["id"],
                                         headers=self.headers).status_code)
        self.assertEqual(401, client.get("/api/jobs/%s/points/INTERACTION" % job["id"]).status_code)

        # One calculation, with its structure from the molprop file that is open
        structures = client.post("/api/uploads", params={"kind": "molprop", "name": "dimers.xml"},
                                 content=MOLPROP.encode(), headers=self.headers)
        self.assertEqual(200, structures.status_code, structures.text)
        open_file = structures.json()["id"]
        one = get("compounds/0/calculations/calculation-0007", molprop=open_file)
        self.assertEqual(200, one.status_code, one.text)
        told = one.json()
        self.assertEqual("water#water", told["name"])
        self.assertEqual([{"term": "INTERACTION", "reference": -20, "forcefield": -19,
                           "difference": 1}], told["energies"])
        self.assertEqual(["O", "H", "H", "O", "H", "Cl"], [a["element"] for a in told["atoms"]])
        # Without the file, or with one that does not have it: the energies alone
        alone = get("compounds/0/calculations/calculation-0008").json()
        self.assertEqual([], alone["atoms"])
        self.assertEqual("", alone["structure_error"])
        other = get("compounds/0/calculations/calculation-0008", molprop=molprop).json()
        self.assertEqual([], other["atoms"])
        self.assertIn("has no calculation 0008", other["structure_error"])
        for path, params in (("compounds/0/calculations/calculation-9", {}),
                             ("compounds/3/calculations/calculation-0007", {}),
                             ("compounds/0/calculations/calculation-0007", {"molprop": "nosuch"})):
            self.assertEqual(404, get(path, **params).status_code, (path, params))
        forcefield = client.post("/api/uploads", params={"kind": "forcefield", "name": "f.xml"},
                                 content=FF, headers=self.headers).json()["id"]
        self.assertEqual(404, get("compounds/0/calculations/calculation-0007",
                                  molprop=forcefield).status_code)

        # A selection file of the user, as it is
        mine = dict(body, molprop={"file": molprop, "described": described["id"],
                                   "selection": "water#water | Train\nmethanol#water|Test\n"})
        job = done(post(mine))
        self.assertEqual("finished", job["status"], job["error"])
        self.assertEqual("water#water|Train\nmethanol#water|Test\n",
                         self.run.calls[-1]["options"]["sel"].read_text())
        ncalls = len(self.run.calls)
        for wrong in (dict(mine["molprop"], selection="water#water|Maybe\n"),
                      dict(mine["molprop"], selection="methanol#water|Test\n"),
                      dict(mine["molprop"], selection=3),
                      dict(mine["molprop"], sets={"test": ["methanol#water"]})):
            answer = post(dict(mine, molprop=wrong))
            self.assertEqual(400, answer.status_code, wrong)
        answer = post(dict(mine, task="train", training={"fit": ["sigma"], "test": 0.2}))
        self.assertEqual(400, answer.status_code)
        self.assertIn("selection file", answer.json()["detail"])
        self.assertEqual(ncalls, len(self.run.calls))

    def test_selection_file(self):
        """!
        @brief The server writes a selection file for a molprop file that
        was read: all compounds, those the user chose one by one or by
        monomer, with compounds for testing by hand or drawn at random.
        """
        client, _ = self.evaluation_app()
        molprop = client.post("/api/uploads", params={"kind": "molprop", "name": "mols.xml"},
                              content=MP, headers=self.headers).json()["id"]
        answer = client.post("/api/jobs", headers=self.headers,
                             json={"task": "mp_describe", "molprop": {"file": molprop}})
        self.assertEqual(200, answer.status_code, answer.text)
        described = answer.json()["id"]
        for _ in range(500):
            job = client.get("/api/jobs/" + described, headers=self.headers).json()
            if job["status"] != "running" and job["status"] != "queued":
                break
            time.sleep(0.01)
        self.assertEqual("finished", job["status"], job["error"])
        # The page gets the monomers of each compound
        self.assertEqual([["water"], ["methanol", "water"]],
                         [m["monomers"] for m in job["result"]["molecules"]])

        def ask(**choices):
            return client.post("/api/selection", headers=self.headers,
                               json=dict(choices, described=described))
        answer = ask()
        self.assertEqual(200, answer.status_code, answer.text)
        self.assertEqual("water#water|Train\nmethanol#water|Train\n", answer.text)
        self.assertTrue(answer.headers["content-type"].startswith("text/plain"))
        self.assertEqual("attachment", answer.headers["content-disposition"])
        self.assertEqual("water#water|Train\nmethanol#water|Test\n",
                         ask(sets={"test": ["methanol#water"]}).text)
        self.assertEqual("methanol#water|Train\n",
                         ask(monomers={"names": ["methanol"]}).text)
        self.assertEqual("water#water|Train\n",
                         ask(monomers={"names": ["water"], "every": True}).text)
        drawn = ask(test=0.5, seed=3)
        self.assertEqual(200, drawn.status_code, drawn.text)
        self.assertEqual(1, drawn.text.count("|Test"))
        for wrong in ({"sets": {"test": ["acetone"]}}, {"monomers": {"names": ["acetone"]}},
                      {"test": 0.9}, {"test": "half"}, {"seed": "x"},
                      {"sets": {"ignore": ["water#water", "methanol#water"]}}):
            self.assertEqual(400, ask(**wrong).status_code, wrong)
        # Only for a file that was read, and with the key
        for wrong in ("nosuchjob", 3, None):
            self.assertEqual(400, client.post("/api/selection", headers=self.headers,
                                              json={"described": wrong}).status_code)
        self.assertEqual(400, client.post("/api/selection", headers=self.headers,
                                          json=["x"]).status_code)
        self.assertEqual(401, client.post("/api/selection",
                                          json={"described": described}).status_code)

    def test_training_gets_the_time_to_stop_in(self):
        """!
        @brief A program that can stop a training in time (train_ff -maxh)
        is told the time: that of a calculation on this computer, less
        what the end of train_ff needs. An older program is not.
        """
        def with_maxh():
            info = fake_program_info()
            options = dict(info.commands["train_ff"].options)
            options["maxh"] = SimpleNamespace(name="maxh", type="real", default=0, values=(),
                                              description="Help for maxh")
            info.commands["train_ff"] = SimpleNamespace(
                options=options, has_option=lambda name: name in options)
            return info

        for program, expected in ((with_maxh, 480 / 3600), (fake_program_info, None)):
            local = FakeRun()

            def run(command, *, workdir, local=local, **kwargs):
                result = local(command, workdir=workdir, **kwargs)
                if command == "edit_mp":
                    result.data = {"summary": {"molecules": 2},
                                   "molecules": [{"name": "water"}, {"name": "methanol"}],
                                   "validation": {"checked": True, "ok": True}}
                return result
            app = create_app(self.settings, run=run, program_info=program)
            self.addCleanup(app.state.jobs.shutdown)
            client = TestClient(app)
            molprop = client.post("/api/uploads", params={"kind": "molprop", "name": "m.xml"},
                                  content=MP, headers=self.headers).json()["id"]

            def done(answer, client=client):
                self.assertEqual(200, answer.status_code, answer.text)
                for _ in range(500):
                    job = client.get("/api/jobs/" + answer.json()["id"],
                                     headers=self.headers).json()
                    if job["status"] in ("finished", "failed", "cancelled"):
                        return job
                    time.sleep(0.01)
                self.fail("The job did not finish")
                return None
            described = done(client.post("/api/jobs", headers=self.headers, json={
                "task": "mp_describe", "molprop": {"file": molprop}}))
            done(client.post("/api/jobs", headers=self.headers, json={
                "task": "train", "forcefield": {"default": True},
                "molprop": {"file": molprop, "described": described["id"]},
                "observables": ["epot"], "training": {"fit": ["sigma"]}}))
            options = local.calls[-1]["options"]
            self.assertEqual("train_ff", local.calls[-1]["command"])
            if expected is None:
                self.assertNotIn("maxh", options)
            else:
                self.assertAlmostEqual(expected, options["maxh"], places=4)
            # A comparison takes the time it takes
            done(client.post("/api/jobs", headers=self.headers, json={
                "task": "evaluate", "forcefield": {"default": True},
                "molprop": {"file": molprop, "described": described["id"]},
                "observables": ["epot"]}))
            self.assertNotIn("maxh", local.calls[-1]["options"])

    def test_read_selection_file(self):
        """!
        @brief A selection file of the user is read for a molprop file,
        and a calculation with what was read gets the same selection.
        """
        client, _ = self.evaluation_app()

        def done(answer):
            self.assertEqual(200, answer.status_code, answer.text)
            for _ in range(500):
                job = client.get("/api/jobs/" + answer.json()["id"], headers=self.headers).json()
                if job["status"] in ("finished", "failed", "cancelled"):
                    return job
                time.sleep(0.01)
            self.fail("The job did not finish")
            return None
        molprop = client.post("/api/uploads", params={"kind": "molprop", "name": "mols.xml"},
                              content=MP, headers=self.headers).json()["id"]
        described = done(client.post("/api/jobs", headers=self.headers, json={
            "task": "mp_describe", "molprop": {"file": molprop}}))["id"]

        def read(text, job=described):
            return client.post("/api/selection/read", headers=self.headers,
                               json={"described": job, "text": text})
        answer = read("methanol#water|Test\nwater#water|Train\nacetone|Train\n")
        self.assertEqual(200, answer.status_code, answer.text)
        told = answer.json()
        self.assertEqual({"test": ["methanol#water"], "ignore": []}, told["sets"])
        self.assertEqual((1, 1, 0, ["acetone"], 1, 0),
                         tuple(told[key] for key in ("train", "test", "ignore", "unknown",
                                                     "unknown_count", "invalid")))
        # A comparison with it, on this computer
        job = done(client.post("/api/jobs", headers=self.headers, json={
            "task": "evaluate", "forcefield": {"default": True}, "observables": ["inter"],
            "molprop": {"file": molprop, "described": described, "sets": told["sets"]}}))
        self.assertEqual("finished", job["status"], job["error"])
        self.assertEqual("water#water|Train\nmethanol#water|Test\n",
                         self.run.calls[-1]["options"]["sel"].read_text())
        # And the same file again when it is saved
        saved = client.post("/api/selection", headers=self.headers,
                            json={"described": described, "sets": told["sets"]})
        self.assertEqual("water#water|Train\nmethanol#water|Test\n", saved.text)
        self.assertEqual({"test": [], "ignore": ["methanol#water"]},
                         read("water#water|Train\n").json()["sets"])
        for wrong in ("acetone|Train\n", "", 3, None, ["water#water|Train"]):
            self.assertEqual(400, read(wrong).status_code, wrong)
        for wrong in ("nosuchjob", 3, None):
            self.assertEqual(400, read("water#water|Train\n", wrong).status_code, wrong)
        self.assertEqual(400, client.post("/api/selection/read", headers=self.headers,
                                          json=["x"]).status_code)
        self.assertEqual(401, client.post("/api/selection/read", json={
            "described": described, "text": "water#water|Train\n"}).status_code)
        # Not more than a selection file can be
        self.assertEqual(413, client.post(
            "/api/selection/read", content=b"{}",
            headers=dict(self.headers, **{"Content-Length": str(10 ** 9),
                                          "Content-Type": "application/json"})).status_code)

    def test_train_force_field(self):
        """!
        @brief train_ff gets what an evaluation gets, with the kinds of
        parameters to train, an individual for each process and a name for
        the trained force field, which a later calculation can use. The
        page follows the training and gets the parameters.
        """
        client, _ = self.evaluation_app()

        def post(body):
            return client.post("/api/jobs", headers=self.headers, json=body)

        def done(answer):
            self.assertEqual(200, answer.status_code, answer.text)
            for _ in range(500):
                job = client.get("/api/jobs/" + answer.json()["id"], headers=self.headers).json()
                if job["status"] in ("finished", "failed", "cancelled"):
                    return job
                time.sleep(0.01)
            self.fail("The job did not finish")
            return None
        molprop = client.post("/api/uploads", params={"kind": "molprop", "name": "mols.xml"},
                              content=MP, headers=self.headers).json()["id"]
        described = done(post({"task": "mp_describe", "molprop": {"file": molprop}}))
        body = {"task": "train", "forcefield": {"default": True},
                "molprop": {"file": molprop, "described": described["id"]},
                "observables": ["inter"], "training": {"fit": ["sigma", "epsilon"]}}
        job = done(post(body))
        self.assertEqual("finished", job["status"], job["error"])
        call = self.run.calls[-1]
        self.assertEqual("train_ff", call["command"])
        self.assertTrue(call["progress"])
        self.assertEqual({"ff": self.settings.default_forcefield,
                          "mp": [self.settings.uploads_dir / (molprop + ".xml")],
                          "sel": call["workdir"] / "selection.dat",
                          "fc_inter": 1, "fit": "sigma epsilon", "optimizer": "MCMC",
                          "pop_size": 1, "o": call["workdir"] / "trained.xml",
                          "json": True}, call["options"])
        self.assertEqual("water#water|Train\nmethanol#water|Train\n",
                         call["options"]["sel"].read_text())
        # What the page gets
        self.assertEqual(3, job["progress_total"])
        self.assertEqual([{"class": "sigma", "name": "ow", "best": 0.3, "min": None,
                           "max": None, "mean": None, "stdev": 0.01, "median": None}],
                         job["result"]["parameters"])
        self.assertEqual({"mode": "1D", "minimum": "yes"}, job["result"]["sensitivity"])
        self.assertIn("Train", job["result"]["statistics"])
        self.assertNotIn("molecules", job["result"])
        self.assertIn("o", job["files"])
        self.assertIn("INTERACTION", job["files"])
        # The trained force field is one to calculate with
        minimized = done(post({"task": "minimize", "molecule": {"smiles": "CC"},
                               "forcefield": {"job": job["id"]}}))
        self.assertEqual("finished", minimized["status"], minimized["error"])
        self.assertEqual(call["workdir"] / "trained.xml", self.run.calls[-1]["options"]["ff"])

        # Several processes, an optimizer that needs them, its numbers and
        # compounds for testing
        more = dict(body, processes=2, training={"fit": ["sigma"], "test": 0.5},
                    options={"optimizer": "HYBRID", "max_generations": 5, "max_iter": 20,
                             "seed": 7, "random_init": False, "temp": 0.5})
        job = done(post(more))
        self.assertEqual("finished", job["status"], job["error"])
        options = self.run.calls[-1]["options"]
        self.assertEqual((2, 2, "HYBRID", 5, 20, 7, False, 0.5, "sigma"),
                         tuple(options[key] for key in
                               ("processes", "pop_size", "optimizer", "max_generations",
                                "max_iter", "seed", "random_init", "temp", "fit")))
        self.assertEqual(["Test", "Train"],
                         sorted(line.split("|")[1]
                                for line in options["sel"].read_text().splitlines()))

        # The next training continues from the individuals of this one
        hybrid = job
        answer = client.get("/api/jobs/%s/population" % hybrid["id"], headers=self.headers)
        self.assertEqual({"individuals": 2}, answer.json())
        further = dict(more, forcefield={"job": hybrid["id"]},
                       training={"fit": ["sigma"], "continue": hybrid["id"]})
        job = done(post(further))
        self.assertEqual("finished", job["status"], job["error"])
        options = self.run.calls[-1]["options"]
        pool = self.run.calls[-2]["workdir"] / "genepool.csv"
        self.assertEqual((pool, self.run.calls[-2]["workdir"] / "trained.xml", 2),
                         (options["gpin"], options["ff"], options["pop_size"]))
        # Not with another number of individuals, not with Monte Carlo
        # alone, and not from a training that has no population
        ncalls = len(self.run.calls)
        mcmc = [j for j in client.get("/api/jobs", headers=self.headers).json()["jobs"]
                if j["task"] == "train"][-1]["id"]
        self.assertEqual(404, client.get("/api/jobs/%s/population" % mcmc,
                                         headers=self.headers).status_code)
        self.assertEqual(404, client.get("/api/jobs/nosuchjob/population",
                                         headers=self.headers).status_code)
        # The same individuals on more processes, each with a helper
        job = done(post(dict(further, processes=4,
                             training={"fit": ["sigma"], "continue": hybrid["id"], "population": 2})))
        self.assertEqual("finished", job["status"], job["error"])
        options = self.run.calls[-1]["options"]
        self.assertEqual((4, 2), (options["processes"], options["pop_size"]))
        ncalls = len(self.run.calls)
        for wrong, text in (
                (dict(further, processes=4), "needs 2 individuals, not 4"),
                (dict(further, processes=1, options={"optimizer": "MCMC"}), "Monte Carlo alone"),
                (dict(further, training={"fit": ["sigma"], "continue": mcmc}), "no population"),
                (dict(further, training={"fit": ["sigma"], "continue": "nosuchjob"}),
                 "no population"),
                (dict(further, training={"fit": ["sigma"], "continue": described["id"]}),
                 "no population"),
                (dict(further, training={"fit": ["sigma"], "continue": 3}), "identifier"),
                (dict(further, options={"optimizer": "HYBRID", "gpin": "/etc/passwd"}), ""),
                (dict(more, processes=3), "even number"),
                (dict(more, training={"fit": ["sigma"], "population": 3}), "even number"),
                (dict(more, processes=1, options={"optimizer": "MCMC"},
                      training={"fit": ["sigma"], "population": 2}), "Monte Carlo alone"),
                (dict(more, training={"fit": ["sigma"], "population": "4"}), "between")):
            answer = post(wrong)
            self.assertEqual(400, answer.status_code, wrong)
            self.assertIn(text, answer.json()["detail"])
        self.assertEqual(ncalls, len(self.run.calls))
        # More individuals than processes, which compute them in turn
        bigger = done(post(dict(more, training={"fit": ["sigma"], "population": 6})))
        self.assertEqual("finished", bigger["status"], bigger["error"])
        options = self.run.calls[-1]["options"]
        self.assertEqual((2, 6), (options["processes"], options["pop_size"]))

        # The compounds for testing chosen by hand, and a property that counts double
        chosen = dict(body, weights={"inter": 2},
                      molprop=dict(body["molprop"], sets={"test": ["methanol#water"]}))
        job = done(post(chosen))
        self.assertEqual("finished", job["status"], job["error"])
        options = self.run.calls[-1]["options"]
        self.assertEqual(2, options["fc_inter"])
        self.assertEqual("water#water|Train\nmethanol#water|Test\n", options["sel"].read_text())
        ignored = dict(body, task="evaluate", observables=["inter"],
                       molprop=dict(body["molprop"], sets={"ignore": ["water#water"]}))
        del ignored["training"]
        job = done(post(ignored))
        self.assertEqual("finished", job["status"], job["error"])
        self.assertEqual("methanol#water|Train\n",
                         self.run.calls[-1]["options"]["sel"].read_text())

        ncalls = len(self.run.calls)
        wrong = [
            dict(body, weights={"epot": 2}),
            dict(body, weights={"inter": 0}),
            dict(body, weights=[2]),
            dict(body, molprop=dict(body["molprop"], sets={"test": ["acetone"]})),
            dict(body, molprop=dict(body["molprop"], sets={"test": ["water#water",
                                                                    "methanol#water"]})),
            dict(body, molprop=dict(body["molprop"], sets={"test": ["methanol#water"]}),
                 training={"fit": ["sigma"], "test": 0.5}),
            {k: v for k, v in body.items() if k != "training"},
            dict(body, training={}),
            dict(body, training={"fit": ["sigma", "-o"]}),
            dict(body, training={"fit": ["sigma"], "test": 0.9}),
            dict(body, observables=[]),
            dict(body, processes=0),
            dict(body, processes=self.settings.max_processes + 1),
            dict(body, processes="2"),
            dict(body, options={"optimizer": "GA"}),
            dict(body, options={"optimizer": "HYBRID"}, processes=1),
            dict(body, options={"optimizer": "Simplex"}),
            dict(body, options={"max_iter": 0}),
            dict(body, options={"max_iter": 10 ** 7}),
            dict(body, options={"max_generations": 0}),
            dict(body, options={"pop_size": 4}),
            dict(body, options={"optimize": False}),
            dict(body, options={"o": "/tmp/ff.xml"}),
            dict(body, options={"fit": "sigma"}),
            # The other calculations run on one process
            {"task": "minimize", "molecule": {"smiles": "CC"}, "processes": 2},
        ]
        for request in wrong:
            self.assertEqual(400, post(request).status_code, request)
        self.assertEqual(ncalls, len(self.run.calls))

        # More settings: annealing within the iterations and over the
        # generations, Boltzmann weighting of the energies, crossover and mutation
        chosen = {"anneal_begin": 0.5, "anneal_end": 0.9, "anneal_global_begin": 0.2,
                  "anneal_global_end": 0.8, "ener_boltz_temp": 300, "n_crossovers": 2,
                  "pr_cross": 0.6, "pr_mut": 0.05}
        tuned = done(post(dict(more, options=dict(more["options"], **chosen))))
        self.assertEqual("finished", tuned["status"], tuned["error"])
        options = self.run.calls[-1]["options"]
        self.assertEqual(chosen, {key: options[key] for key in chosen})
        # Without them train_ff decides
        self.assertFalse(set(chosen) & set(self.run.calls[-2]["options"]))
        ncalls = len(self.run.calls)
        for wrong, said in (({"anneal_begin": 1.5}, "between 0 and 1"),
                            ({"anneal_begin": -0.1}, "between 0 and 1"),
                            ({"anneal_begin": "half"}, "must be a number"),
                            ({"anneal_begin": 0.8, "anneal_end": 0.2}, "before its start"),
                            ({"anneal_end": 0.5}, "Give where the annealing starts"),
                            ({"anneal_global_begin": 0.9, "anneal_global_end": 0.1},
                             "before its start"),
                            ({"pr_cross": 1.2}, "between 0 and 1"),
                            ({"pr_mut": -1}, "between 0 and 1"),
                            ({"n_crossovers": 0}, "between 1 and 1000"),
                            ({"n_crossovers": 1.5}, "whole number"),
                            ({"ener_boltz_temp": -5}, "between 0 and"),
                            ({"optimizer": "MCMC", "max_generations": None, "pr_cross": 0.5},
                             "Monte Carlo alone has no crossover"),
                            ({"optimizer": "MCMC", "max_generations": None,
                              "anneal_global_begin": 0.5}, "for the optimizer HYBRID"),
                            ({"optimizer": "GA", "anneal_begin": 0.5}, "no Monte Carlo steps"),
                            ({"anneal": 0.5}, "has no option 'anneal'")):
            answer = post(dict(more, options=dict(more["options"], **wrong)))
            self.assertEqual(400, answer.status_code, wrong)
            self.assertIn(said, answer.json()["detail"], wrong)
        self.assertEqual(ncalls, len(self.run.calls))
        # Monte Carlo alone anneals as well
        alone = done(post(dict(body, options={"anneal_begin": 0.25, "ener_boltz_temp": 298.15})))
        self.assertEqual("finished", alone["status"], alone["error"])
        self.assertEqual((0.25, 298.15), tuple(self.run.calls[-1]["options"][key]
                                               for key in ("anneal_begin", "ener_boltz_temp")))

    def test_molprop_files_may_be_larger(self):
        """!
        @brief Molprop files have their own largest size.
        """
        large = MP + b"<!-- " + b"x" * 6000 + b" -->\n"
        self.assertGreater(len(large), self.settings.max_upload_bytes)
        self.assertEqual(200, self.upload("mols.xml", "molprop", large).status_code)
        self.assertEqual(413, self.upload("big.xml", "forcefield", FF + large).status_code)
        session = self.client.get("/api/session", headers=self.headers).json()
        self.assertEqual(self.settings.max_molprop_bytes, session["max_molprop_bytes"])

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
        # The trajectory has the shells and the virtual sites, for the viewer
        self.assertIs(True, self.run.calls[-1]["options"]["ws"])
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

        def fake(xvg, pdf, series=(), reverse_x=False, **labels):
            calls.append((Path(xvg).name, Path(pdf).name, list(series)) + ((True,) if reverse_x else ())
                         + ((labels,) if labels else ()))
            Path(pdf).write_bytes(b"%PDF-1.4 fake")
            return Path(pdf)

        def broken(xvg, pdf, series=(), reverse_x=False, **labels):
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
        self.assertEqual(["e", "hist"], tasks["simulate"]["plots"])
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
        # The temperature is not an energy, as the file says
        self.assertEqual(("data.xvg", "plot-e-TEMPERATURE.pdf", ["TEMPERATURE"],
                          {"title": "Temperature", "ylabel": "Temperature (K)"}), calls[-1])
        answer = client.get(url, headers=self.headers, params={"series": "EPOT"})
        self.assertEqual(("data.xvg", "plot-e-EPOT.pdf", ["EPOT"]), calls[-1])
        # With the x-axis reversed, which is another file
        answer = client.get(url, headers=self.headers, params={"reverse": "true"})
        self.assertEqual(200, answer.status_code)
        self.assertIn("simulate-e-reversed.pdf", answer.headers["content-disposition"])
        self.assertEqual(("data.xvg", "plot-e-reversed.pdf", [], True), calls[-1])
        self.assertEqual(422, client.get(url, headers=self.headers,
                                         params={"reverse": "sideways"}).status_code)
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
