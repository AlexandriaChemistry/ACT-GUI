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
@brief Tests of the jobs on the cluster through the interface of the
server: a job is shown, sent, followed, stopped, and its results are shown
as those of a calculation on this machine. A directory stands in for the
cluster and small programs for ssh and Slurm, so that no test logs in
anywhere.
"""
import re
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from act_gui.app import TOKEN_HEADER, create_app
from act_gui.settings import Settings
from tests.test_api import fake_program_info
from tests.test_cluster import FakeServer
from tests.test_cluster_queue import fake_arguments
from tests.test_jobs import FakeRun
from tests.test_uploads import FF, MP

## The settings of the cluster of the tests
CLUSTER = {"server": "good", "user": "spoel", "workdir": "act/jobs", "load": "none"}


class ClusterJobsApiTest(unittest.TestCase):
    """!
    @brief Requests for jobs on the cluster.
    """

    def setUp(self):
        """!
        @brief Make a stand-in for a cluster and an interface that knows it.
        """
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name).resolve()
        self.server = FakeServer(self.dir / "server")
        self.remote = self.server.dir / "home" / "act" / "jobs"
        self.headers = {TOKEN_HEADER: "t"}
        self.apps = []
        self.plotted = []
        forcefield = self.dir / "default.xml"
        forcefield.write_bytes(FF)
        self.options = {"data_dir": self.dir / "data", "token": "t",
                        "config_file": self.dir / "config" / "settings.json",
                        "ssh_program": str(self.server.program),
                        "cluster_jobs_dir": self.dir / "kept",
                        "default_forcefield": forcefield}
        self.client = self.new_client()
        answer = self.client.put("/api/cluster", headers=self.headers, json=CLUSTER)
        self.assertEqual(200, answer.status_code, answer.text)
        # A molprop file that was read, as the page has it
        self.molprop = self.client.post(
            "/api/uploads", params={"kind": "molprop", "name": "mols.xml"}, content=MP,
            headers=self.headers).json()["id"]
        described = self.client.post("/api/jobs", headers=self.headers, json={
            "task": "mp_describe", "molprop": {"file": self.molprop}}).json()["id"]
        for _ in range(500):
            if self.client.get("/api/jobs/" + described,
                               headers=self.headers).json()["status"] == "finished":
                break
            time.sleep(0.01)
        self.body = {"task": "evaluate", "forcefield": {"default": True},
                     "molprop": {"file": self.molprop, "described": described},
                     "observables": ["inter", "epot"]}

    def tearDown(self):
        """!
        @brief Stop the jobs and remove the files.
        """
        for app in self.apps:
            app.state.jobs.shutdown()
        self._tmp.cleanup()

    def new_client(self, **more):
        """!
        @brief Start the interface, as when the program starts.
        @param more Other settings than those of the test.
        @return A client for it.
        """
        local = FakeRun()

        def run(command, *, workdir, **kwargs):
            result = local(command, workdir=workdir, **kwargs)
            if command == "edit_mp":
                result.data = {"summary": {"molecules": 1},
                               "molecules": [{"name": "w2", "iupac": "water#water"}],
                               "validation": {"checked": True, "ok": True}}
            return result

        def plotter(xvg, pdf, series=(), reverse_x=False, **extra):
            self.plotted.append((Path(xvg).name, extra))
            Path(pdf).write_bytes(b"%PDF-1.4 fake")
            return Path(pdf)
        options = dict(self.options)
        options.update(more)
        app = create_app(Settings(**options), run=run, program_info=fake_program_info,
                         plotter=plotter, arguments=fake_arguments)
        self.apps.append(app)
        return TestClient(app)

    def post(self, send=False, client=None, **cluster):
        """!
        @brief Ask for the comparison as a job on the cluster.
        @param send    Whether to send it.
        @param client  The client, that of the test if None.
        @param cluster What the request says about the job itself.
        @return The answer.
        """
        return (client or self.client).post(
            "/api/cluster/jobs", headers=self.headers,
            json=dict(self.body, cluster=cluster, send=send))

    def logins(self):
        """!
        @brief The number of times that ssh was started.
        @return The number.
        """
        return len(self.server.calls())

    def test_job_is_shown_before_it_is_sent(self):
        """!
        @brief The script, the command, the files and where they go, and
        nobody logs in for it.
        """
        answer = self.post(name="compare-1", tasks=8, minutes=150, gpu=True)
        self.assertEqual(200, answer.status_code, answer.text)
        shown = answer.json()
        self.assertEqual(("compare-1", "spoel@good", "act/jobs/compare-1", False, False),
                         tuple(shown[key] for key in ("name", "server", "directory", "sent",
                                                      "taken")))
        self.assertEqual(["ff.xml", "mp.xml", "selection.dat"],
                         [entry["name"] for entry in shown["files"]])
        self.assertEqual(len(MP), shown["files"][1]["size"])
        for line in ("#SBATCH --job-name=compare-1", "#SBATCH --partition=GPU,CLUSTER-AMD",
                     "#SBATCH --gres=gpu:1", "#SBATCH --ntasks=8", "#SBATCH --time=0-02:30:00",
                     "srun alexandria train_ff -ff ff.xml -mp mp.xml -fc_inter 1 -fc_epot 1 "
                     "-epotcorr epot_corr.xvg -json train_ff.json -nooptimize "
                     "-sel selection.dat"):
            self.assertIn(line + "\n", shown["script"])
        # No name of a file of this machine
        self.assertNotIn(str(self.dir), answer.text)
        self.assertNotIn(self.molprop, answer.text)
        self.assertEqual(0, self.logins())
        self.assertFalse((self.dir / "kept").exists())
        # A name is proposed
        proposed = self.post().json()
        self.assertTrue(re.match(r"^evaluate-\d{8}-\d{6}$", proposed["name"]), proposed["name"])
        self.assertIn("#SBATCH --partition=CLUSTER,CLUSTER-AMD\n", proposed["script"])
        self.assertNotIn("srun", proposed["script"])
        self.assertEqual([], self.client.get("/api/cluster/jobs",
                                             headers=self.headers).json()["jobs"])

    def test_job_from_sending_to_results(self):
        """!
        @brief One login to send, one to ask and one to fetch. Then the
        results are those of a calculation: statistics, files and plots.
        """
        answer = self.post(send=True, name="compare-1")
        self.assertEqual(200, answer.status_code, answer.text)
        sent = answer.json()
        self.assertEqual((True, "compare-1", "4242"), (sent["sent"], sent["name"], sent["slurm"]))
        self.assertEqual(1, self.logins())
        job = sent["jobs"][0]
        self.assertEqual(("compare-1", "evaluate", "waiting", "4242", False, "",
                          "Compare a force field with the data of a molprop file"),
                         tuple(job[key] for key in ("name", "task", "state", "slurm", "fetched",
                                                    "job", "title")))
        self.assertEqual("water#water|Train\n",
                         (self.remote / "compare-1" / "selection.dat").read_text())
        self.assertEqual(MP, (self.remote / "compare-1" / "mp.xml").read_bytes())
        # Results are not there before the cluster was asked
        self.assertEqual(400, self.client.post("/api/cluster/jobs/compare-1/open",
                                               headers=self.headers).status_code)
        # Listing the jobs asks nobody
        listed = self.client.get("/api/cluster/jobs", headers=self.headers).json()
        self.assertEqual((120, 0, "", []), (listed["poll_seconds"], listed["wait"],
                                            listed["paused"], listed["unread"]))
        self.assertEqual(1, self.logins())
        checked = self.client.post("/api/cluster/jobs/check", headers=self.headers).json()
        self.assertEqual((True, ""), (checked["asked"], checked["problem"]))
        self.assertEqual(("finished", True, 0), tuple(checked["jobs"][0][key] for key in
                                                      ("state", "fetched", "exit")))
        self.assertEqual(3, self.logins())
        self.assertGreater(checked["wait"], 100)
        # Asked again at once: nothing happens, also for the user
        again = self.client.post("/api/cluster/jobs/check", params={"manual": "true"},
                                 headers=self.headers).json()
        self.assertFalse(again["asked"])
        self.assertEqual(3, self.logins())

        opened = self.client.post("/api/cluster/jobs/compare-1/open", headers=self.headers)
        self.assertEqual(200, opened.status_code, opened.text)
        self.assertEqual("evaluate", opened.json()["task"])
        job_id = opened.json()["job"]
        result = self.client.get("/api/jobs/" + job_id, headers=self.headers).json()
        self.assertEqual("finished", result["status"])
        self.assertEqual({"Train": {"INTERACTION": {"N": 2, "RMSD": 1.0}}},
                         result["result"]["statistics"])
        self.assertEqual([{"name": "water#water", "dataset": "Train", "points": 1}],
                         result["result"]["compounds"])
        self.assertLessEqual({"INTERACTION", "epotcorr", "json", "sel"}, set(result["files"]))
        data = self.client.get("/api/jobs/%s/files/INTERACTION" % job_id, headers=self.headers)
        self.assertEqual(200, data.status_code)
        self.assertIn("-20 -19", data.text)
        pdf = self.client.get("/api/jobs/%s/plots/epotcorr" % job_id, headers=self.headers)
        self.assertEqual(200, pdf.status_code)
        self.assertEqual([("epot_corr.xvg", {"scatter": True})], self.plotted)
        # Opened once more it is the same job
        self.assertEqual(job_id, self.client.post("/api/cluster/jobs/compare-1/open",
                                                  headers=self.headers).json()["job"])
        self.assertEqual(job_id, self.client.get("/api/cluster/jobs",
                                                 headers=self.headers).json()["jobs"][0]["job"])
        self.assertEqual(3, self.logins())

    def test_training_on_the_cluster(self):
        """!
        @brief A training has an individual for each process of the job
        and reports its progress in a file: the list of jobs tells how far
        it is, and the results come with the parameters and the progress.
        """
        body = dict(self.body, task="train", observables=["inter"],
                    training={"fit": ["sigma", "epsilon"]},
                    options={"optimizer": "HYBRID", "max_generations": 3, "max_iter": 20})

        def post(send=False, request=body, **chosen):
            return self.client.post("/api/cluster/jobs", headers=self.headers,
                                    json=dict(request, cluster=chosen, send=send))
        answer = post(name="train-1", tasks=4, minutes=120)
        self.assertEqual(200, answer.status_code, answer.text)
        shown = answer.json()
        self.assertIn("#SBATCH --ntasks=4\n", shown["script"])
        self.assertIn("srun alexandria train_ff -ff ff.xml -mp mp.xml -fc_inter 1 "
                      "-fit 'sigma epsilon' -optimizer HYBRID -max_generations 3 -max_iter 20 "
                      "-pop_size 4 -json train_ff.json -progress progress.jsonl "
                      "-o trained.xml -sel selection.dat\n", shown["script"])
        self.assertEqual(0, self.logins())
        # The processes are those of the job, whatever the request says
        # about this computer; the genetic algorithm needs two
        shown = post(name="train-1", tasks=2, request=dict(body, processes=1)).json()
        self.assertIn("-pop_size 2 ", shown["script"])
        refused = post(name="train-1", tasks=1)
        self.assertEqual(400, refused.status_code)
        self.assertIn("two processes", refused.json()["detail"])
        for tasks in (0, "4", 5000, True):
            self.assertEqual(400, post(name="train-1", tasks=tasks).status_code, tasks)
        self.assertEqual(0, self.logins())

        sent = post(send=True, name="train-1", tasks=4)
        self.assertEqual(200, sent.status_code, sent.text)
        self.assertEqual("train", sent.json()["jobs"][0]["task"])
        self.assertIsNone(sent.json()["jobs"][0]["progress"])
        checked = self.client.post("/api/cluster/jobs/check", headers=self.headers).json()
        job = checked["jobs"][0]
        self.assertEqual(("finished", True), (job["state"], job["fetched"]))
        self.assertEqual({"event": "generation", "generation": 2, "ngenerations": 3,
                          "chi2": 46.5, "seconds": 3.0}, job["progress"])
        self.assertEqual(3, self.logins())
        opened = self.client.post("/api/cluster/jobs/train-1/open", headers=self.headers)
        self.assertEqual(200, opened.status_code, opened.text)
        self.assertEqual("train", opened.json()["task"])
        result = self.client.get("/api/jobs/" + opened.json()["job"],
                                 headers=self.headers).json()
        self.assertEqual("finished", result["status"])
        self.assertEqual("sigma", result["result"]["parameters"][0]["class"])
        self.assertEqual({"mode": "1D", "minimum": "yes"}, result["result"]["sensitivity"])
        self.assertIn("Train", result["result"]["statistics"])
        self.assertEqual(["start", "generation", "generation", "step"],
                         [report["event"] for report in result["progress"]])
        self.assertLessEqual({"o", "progress", "INTERACTION", "json"}, set(result["files"]))
        # The trained force field is one to calculate with
        trained = self.client.get("/api/jobs/%s/files/o" % opened.json()["job"],
                                  headers=self.headers)
        self.assertEqual(200, trained.status_code)
        self.assertIn("alexandria_chemistry", trained.text)
        self.assertEqual(3, self.logins())

    def test_jobs_are_there_when_the_interface_starts_again(self):
        """!
        @brief A later run of the interface lists the job, follows it and
        shows its results, also when the settings of the cluster are gone.
        """
        (self.server.dir / "queue").write_text("RUNNING\n")
        self.assertEqual(200, self.post(send=True, name="long-job").status_code)
        self.assertEqual(200, self.client.delete("/api/cluster", headers=self.headers).status_code)
        later = self.new_client(data_dir=self.dir / "data2")
        listed = later.get("/api/cluster/jobs", headers=self.headers).json()
        self.assertEqual([("long-job", "waiting")],
                         [(job["name"], job["state"]) for job in listed["jobs"]])
        self.assertEqual(str(self.dir / "kept"), listed["directory"])
        checked = later.post("/api/cluster/jobs/check", headers=self.headers).json()
        self.assertEqual((True, "running"), (checked["asked"], checked["jobs"][0]["state"]))
        # The program ends
        (self.server.dir / "queue").unlink()
        remote = self.remote / "long-job"
        (remote / "act-exit-status").write_text("0\n")
        (remote / "train_ff.json").write_text('{"train_ff": {"statistics": {"Train": {}}}}')
        later.app.state.cluster_queue._asked = 0.0  # pylint: disable=protected-access
        checked = later.post("/api/cluster/jobs/check", headers=self.headers).json()
        self.assertEqual("finished", checked["jobs"][0]["state"])
        third = self.new_client(data_dir=self.dir / "data3")
        opened = third.post("/api/cluster/jobs/long-job/open", headers=self.headers)
        self.assertEqual(200, opened.status_code, opened.text)
        result = third.get("/api/jobs/" + opened.json()["job"], headers=self.headers).json()
        self.assertEqual({"compounds": [], "statistics": {}, "outliers": {}}, result["result"])

    def test_stop_and_forget(self):
        """!
        @brief A job that waits is stopped; a job that is done is taken
        from the list.
        """
        (self.server.dir / "queue").write_text("PENDING\n")
        self.assertEqual(200, self.post(send=True, name="job1").status_code)
        self.assertEqual(409, self.client.delete("/api/cluster/jobs/job1",
                                                 params={"forget": "true"},
                                                 headers=self.headers).status_code)
        stopped = self.client.delete("/api/cluster/jobs/job1", headers=self.headers)
        self.assertEqual(200, stopped.status_code, stopped.text)
        self.assertEqual("cancelled", stopped.json()["jobs"][0]["state"])
        self.assertEqual("4242\n", (self.server.dir / "scancel.log").read_text())
        self.assertEqual(2, self.logins())
        self.assertEqual(409, self.client.delete("/api/cluster/jobs/job1",
                                                 headers=self.headers).status_code)
        for path in ("/api/cluster/jobs/job1/open", "/api/cluster/jobs/job1/fetch"):
            self.assertIn(self.client.post(path, headers=self.headers).status_code, (400, 502))
        gone = self.client.delete("/api/cluster/jobs/job1", params={"forget": "true"},
                                  headers=self.headers)
        self.assertEqual([], gone.json()["jobs"])
        self.assertFalse((self.dir / "kept" / "job1").exists())
        self.assertTrue((self.remote / "job1" / "job.sh").is_file())
        self.assertEqual(2, self.logins())
        for method, path in (("delete", "/api/cluster/jobs/job1"),
                             ("post", "/api/cluster/jobs/job1/open"),
                             ("post", "/api/cluster/jobs/job1/fetch"),
                             ("delete", "/api/cluster/jobs/..%2F..%2Fetc")):
            self.assertEqual(404, getattr(self.client, method)(
                path, headers=self.headers).status_code, path)

    def test_requests_that_are_refused(self):
        """!
        @brief What is not accepted for a calculation here is not accepted
        for the cluster, nor a job that cannot be, and nobody logs in.
        """
        wrong = [
            dict(name="a b"), dict(name="../x"), dict(name="-x"), dict(name=5),
            dict(tasks=0), dict(tasks="4"), dict(tasks=10 ** 6), dict(minutes=0),
            dict(minutes=1.5), dict(gpu="yes"), dict(partition="other"),
            dict(script="#!/bin/sh\nrm -rf ~"),
        ]
        for cluster in wrong:
            for send in (False, True):
                self.assertEqual(400, self.post(send=send, **cluster).status_code, cluster)

        def raw(body):
            return self.client.post("/api/cluster/jobs", headers=self.headers, json=body)
        bodies = [
            dict(self.body, observables=[]),
            dict(self.body, molprop={"file": self.molprop}),
            dict(self.body, options={"o": "/tmp/ff.xml"}),
            dict(self.body, cluster="fast"),
            dict(self.body, send="yes"),
            # A calculation that runs here only
            {"task": "minimize", "molecule": {"smiles": "CC"}},
            {"task": "nosuchtask"}, [1], "x",
        ]
        for body in bodies:
            self.assertEqual(400, raw(body).status_code, body)
            if isinstance(body, dict) and "send" not in body:
                self.assertEqual(400, raw(dict(body, send=True)).status_code, body)
        self.assertEqual(400, self.client.post(
            "/api/cluster/jobs", headers=dict(self.headers, **{"Content-Type": "application/json"}),
            content=b"{not json").status_code)
        self.assertEqual(0, self.logins())
        self.assertFalse((self.dir / "kept").exists() and any((self.dir / "kept").iterdir()))
        # A name that is taken
        self.assertEqual(200, self.post(send=True, name="job1").status_code)
        self.assertTrue(self.post(name="job1").json()["taken"])
        taken = self.post(send=True, name="job1")
        self.assertEqual(409, taken.status_code)
        self.assertIn("choose another name", taken.json()["detail"])
        self.assertEqual(1, self.logins())
        # Without the token
        self.assertEqual(401, self.client.post("/api/cluster/jobs", json=self.body).status_code)
        self.assertEqual(401, self.client.post("/api/cluster/jobs/check").status_code)
        self.assertEqual(401, self.client.delete("/api/cluster/jobs/job1").status_code)

    def test_job_that_cannot_be_sent(self):
        """!
        @brief Without settings there is nowhere to send a job; a cluster
        that cannot be reached gives a message and leaves no job behind.
        """
        (self.remote / "there").mkdir()
        answer = self.post(send=True, name="there")
        self.assertEqual(502, answer.status_code)
        self.assertIn("could not be sent", answer.json()["detail"])
        self.assertFalse((self.dir / "kept" / "there").exists())
        self.client.put("/api/cluster", headers=self.headers, json=dict(CLUSTER,
                                                                         server="nologin"))
        answer = self.post(send=True, name="job1")
        self.assertEqual(502, answer.status_code)
        self.assertIn("Logging in on nologin as spoel did not work", answer.json()["detail"])
        self.assertEqual([], self.client.get("/api/cluster/jobs",
                                             headers=self.headers).json()["jobs"])
        self.assertEqual([], [p.name for p in (self.dir / "kept").iterdir()])
        self.client.delete("/api/cluster", headers=self.headers)
        logins = self.logins()
        for send in (False, True):
            answer = self.post(send=send, name="job1")
            self.assertEqual(400, answer.status_code)
            self.assertIn("tab Cluster", answer.json()["detail"])
        self.assertEqual(logins, self.logins())

    def test_server_for_several_users_sends_no_jobs(self):
        """!
        @brief On a server for several users there are no jobs on a
        cluster of a user.
        """
        client = self.new_client(mode="server", data_dir=self.dir / "data-server")
        for method, path in (("get", "/api/cluster/jobs"), ("post", "/api/cluster/jobs"),
                             ("post", "/api/cluster/jobs/check"),
                             ("post", "/api/cluster/jobs/job1/open"),
                             ("delete", "/api/cluster/jobs/job1")):
            self.assertIn(getattr(client, method)(path, headers=self.headers).status_code,
                          (404, 405), path)
        self.assertEqual(0, self.logins())

    def test_tasks_tell_whether_they_run_on_the_cluster(self):
        """!
        @brief The page learns which calculations can be sent.
        """
        tasks = {task["name"]: task["cluster"] for task in
                 self.client.get("/api/tasks", headers=self.headers).json()["tasks"]}
        self.assertTrue(tasks["evaluate"])
        self.assertFalse(tasks["minimize"])


if __name__ == "__main__":
    unittest.main()
