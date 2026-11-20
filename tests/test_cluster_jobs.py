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
@brief Tests of the jobs on the cluster of the user: sending, asking for
the state, fetching the results and stopping. A directory stands in for the
cluster and small programs for ssh and Slurm, so that no test logs in
anywhere.
"""
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from act_gui import cluster, cluster_jobs
from tests.test_cluster import FakeServer


class ClusterJobTest(unittest.TestCase):
    """!
    @brief A job from this machine to the cluster and back.
    """

    def setUp(self):
        """!
        @brief Make the stand-in for a cluster and the settings for it.
        """
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.server = FakeServer(self.dir / "server")
        self.settings = cluster.parse({"server": "good", "user": "spoel", "workdir": "act/jobs",
                                       "load": "none"})
        self.ssh = str(self.server.program)
        self.remote = self.server.dir / "home" / "act" / "jobs"

    def tearDown(self):
        """!
        @brief Remove the files.
        """
        self._tmp.cleanup()

    def send(self, name="job1", arguments=("train_ff", "-ff", "ff.xml")):
        """!
        @brief Send a job that runs the stand-in for alexandria.
        @param name      The name of the job.
        @param arguments The arguments of the program.
        @return The number that Slurm gave the job.
        """
        forcefield = self.dir / "local-name.xml"
        forcefield.write_text("<alexandria_chemistry/>\n")
        script = cluster.batch_script(self.settings, name, ["alexandria"] + list(arguments))
        return cluster_jobs.submit(self.settings, name, script,
                                   {"ff.xml": forcefield, "selection.dat": b"water|Train\n"},
                                   program=self.ssh)

    def test_job_is_sent_and_runs(self):
        """!
        @brief The files and the script arrive in a directory of the job,
        sbatch is started there, and all of it is one login.
        """
        self.assertEqual("4242", self.send())
        job = self.remote / "job1"
        self.assertEqual(["act-exit-status", "ff.xml", "job.sh", "result.json", "selection.dat",
                          "slurm.out"], sorted(p.name for p in job.iterdir()))
        self.assertEqual("<alexandria_chemistry/>\n", (job / "ff.xml").read_text())
        self.assertEqual("water|Train\n", (job / "selection.dat").read_text())
        self.assertIn("#SBATCH --partition=CLUSTER,CLUSTER-AMD", (job / "job.sh").read_text())
        # sbatch ran in the directory of the job, with the script
        self.assertTrue((self.server.dir / "sbatch.log").read_text().strip().endswith(
            "/act/jobs/job1 --parsable job.sh"))
        self.assertEqual({"asked": "train_ff -ff ff.xml"},
                         json.loads((job / "result.json").read_text()))
        self.assertEqual("0\n", (job / "act-exit-status").read_text())
        self.assertEqual(1, len(self.server.calls()))

    def test_job_that_cannot_be_sent(self):
        """!
        @brief A name that is taken, a name or a file that is not possible,
        and a server that cannot be reached give a message.
        """
        self.send()
        with self.assertRaises(cluster.ClusterError) as caught:
            self.send()
        self.assertIn("could not be sent", str(caught.exception))
        script = cluster.batch_script(self.settings, "j", ["alexandria"])
        for name in ("../job", "a b", "", "-rf", "a/b", 5):
            with self.assertRaises(cluster.ClusterError):
                cluster_jobs.submit(self.settings, name, script, {}, program=self.ssh)
        for filename in ("../x.xml", "a b.xml", "x;y", "job.sh", "-x", "a/b.xml"):
            with self.assertRaises(cluster.ClusterError):
                cluster_jobs.submit(self.settings, "job2", script, {filename: b"x"},
                                    program=self.ssh)
        self.assertFalse((self.remote / "job2").exists())
        nologin = cluster.parse({"server": "nologin", "user": "spoel", "workdir": "act/jobs"})
        with self.assertRaises(cluster.ClusterError) as caught:
            cluster_jobs.submit(nologin, "job3", script, {}, program=self.ssh)
        self.assertIn("Logging in on nologin as spoel did not work", str(caught.exception))
        with self.assertRaises(cluster.ClusterError) as caught:
            cluster_jobs.submit(self.settings, "job4", script, {},
                                program=str(self.dir / "no-ssh"))
        self.assertIn("could not be started", str(caught.exception))

    def test_state_of_a_job(self):
        """!
        @brief Waiting and running come from Slurm; the end comes from the
        exit status that the script leaves, whatever Slurm still says.
        """
        queue = self.server.dir / "queue"
        queue.write_text("PENDING\n")
        self.assertEqual("4242", self.send())
        self.assertEqual({"slurm": "PENDING", "state": "waiting"},
                         cluster_jobs.status(self.settings, "job1", "4242", program=self.ssh))
        queue.write_text("RUNNING\n")
        self.assertEqual("running", cluster_jobs.status(self.settings, "job1", "4242",
                                                        program=self.ssh)["state"])
        # The program ends while Slurm still lists the job
        (self.remote / "job1" / "act-exit-status").write_text("0\n")
        queue.write_text("COMPLETING\n")
        self.assertEqual({"slurm": "COMPLETING", "state": "finished", "exit": 0},
                         cluster_jobs.status(self.settings, "job1", "4242", program=self.ssh))
        queue.unlink()
        self.assertEqual({"slurm": "", "state": "finished", "exit": 0},
                         cluster_jobs.status(self.settings, "job1", "4242", program=self.ssh))
        # A program that fails
        self.send("job2", ("train_ff", "-fail"))
        self.assertEqual({"slurm": "", "state": "failed", "exit": 3},
                         cluster_jobs.status(self.settings, "job2", "4242", program=self.ssh))
        # Gone from the queue without an exit status: stopped, or out of time
        (self.remote / "job2" / "act-exit-status").unlink()
        self.assertEqual({"slurm": "", "state": "lost"},
                         cluster_jobs.status(self.settings, "job2", "4242", program=self.ssh))
        # Each question is one login
        self.assertEqual(2 + 6, len(self.server.calls()))

    def test_progress_of_a_job(self):
        """!
        @brief A job that reports its progress is asked for it in the same
        login as for its state: the last generation, or without one the
        last line. Of what the cluster sends only a few numbers are kept.
        """
        self.send("job1", ("train_ff", "-progress", "progress.jsonl"))
        self.send("job2", ("train_ff",))
        # As if they were still running
        (self.server.dir / "queue").write_text("RUNNING\n")
        for name in ("job1", "job2"):
            (self.remote / name / "act-exit-status").unlink()
        logins = len(self.server.calls())
        said = cluster_jobs.status_all(self.settings, [("job1", "4242", "progress.jsonl"),
                                                       ("job2", "4242", ""),
                                                       ("job3", "4242")], program=self.ssh)
        self.assertEqual(logins + 1, len(self.server.calls()))
        self.assertEqual({"slurm": "RUNNING", "state": "running",
                          "progress": {"event": "generation", "generation": 2,
                                       "ngenerations": 3, "chi2": 46.5, "seconds": 3.0}},
                         said["job1"])
        self.assertNotIn("progress", said["job2"])
        self.assertNotIn("progress", said["job3"])
        # Before the first generation: the last line that is there
        report = self.remote / "job1" / "progress.jsonl"
        report.write_text(report.read_text().splitlines()[0] + "\n")
        said = cluster_jobs.status_all(self.settings, [("job1", "4242", "progress.jsonl")],
                                       program=self.ssh)
        self.assertEqual({"event": "start", "ngenerations": 3, "niterations": 20, "chi2": 96.5},
                         said["job1"]["progress"])
        # A file that is not there yet, or that has something else
        report.unlink()
        self.assertNotIn("progress", cluster_jobs.status_all(
            self.settings, [("job1", "4242", "progress.jsonl")], program=self.ssh)["job1"])
        for text in ("$(reboot)\n", '{"event":"reboot","generation":1}\n', "[1, 2]\n",
                     '{"event":"generation","generation":"<b>1</b>","best":{"train":"x"}}\n',
                     '{"event":"step","x":"' + "y" * 3000 + '"}\n'):
            report.write_text(text)
            said = cluster_jobs.status_all(self.settings, [("job1", "4242", "progress.jsonl")],
                                           program=self.ssh)["job1"]
            self.assertIn(said.get("progress"), (None, {"event": "generation"}), text)
        # The name of the file is checked as the other names in a command
        self.assertEqual({"event": "generation"}, cluster_jobs.progress_summary(
            {"event": "generation", "best": {"train": float("nan")}, "generation": True}))
        self.assertIsNone(cluster_jobs.progress_summary({"generation": 1}))
        self.assertIsNone(cluster_jobs.progress_summary(None))
        for wrong in ("42; reboot", "", "-1", "abc", 42):
            with self.assertRaises(cluster.ClusterError):
                cluster_jobs.status(self.settings, "job1", wrong, program=self.ssh)

    def test_results_are_fetched(self):
        """!
        @brief The files that are asked for and that are there come back,
        in one login.
        """
        self.send()
        before = len(self.server.calls())
        target = self.dir / "results"
        fetched = cluster_jobs.fetch(self.settings, "job1",
                                     ["result.json", "slurm.out", "not-written.xvg"], target,
                                     program=self.ssh)
        self.assertEqual(["result.json", "slurm.out"], sorted(p.name for p in fetched))
        self.assertEqual({"asked": "train_ff -ff ff.xml"},
                         json.loads((target / "result.json").read_text()))
        self.assertIn("running train_ff", (target / "slurm.out").read_text())
        self.assertEqual(before + 1, len(self.server.calls()))
        self.assertEqual([], cluster_jobs.fetch(self.settings, "job1", ["nothing.here"],
                                                self.dir / "none", program=self.ssh))
        self.assertEqual([], cluster_jobs.fetch(self.settings, "job1", [], target,
                                                program=self.ssh))
        for wrong in (["../../etc/passwd"], ["a b"], ["x;reboot"], ["sub/file"], ["-T"]):
            with self.assertRaises(cluster.ClusterError):
                cluster_jobs.fetch(self.settings, "job1", wrong, target, program=self.ssh)
        with self.assertRaises(cluster.ClusterError):
            cluster_jobs.fetch(self.settings, "no-such-job", ["result.json"], target,
                               program=self.ssh)

    def test_what_the_server_sends_is_not_trusted(self):
        """!
        @brief An archive with a file that was not asked for, a link or a
        path out of the directory is refused, and nothing of it is written.
        """
        def answer_with(members):
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w") as archive:
                for info, content in members:
                    archive.addfile(info, io.BytesIO(content) if content is not None else None)

            def run(command, **kwargs):
                return SimpleNamespace(returncode=0, stdout=buffer.getvalue(), stderr=b"")
            return run

        def plain(name, content=b"data"):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            return info, content
        link = tarfile.TarInfo("result.json")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        target = self.dir / "fetched"
        for members in ([plain("../outside.txt")], [plain("/tmp/absolute.txt")],
                        [plain("other.txt")], [(link, None)],
                        [plain("result.json"), plain("sub/result.json")]):
            with self.assertRaises(cluster.ClusterError):
                cluster_jobs.fetch(self.settings, "job1", ["result.json"], target,
                                   run=answer_with(members))
        self.assertFalse((self.dir / "outside.txt").exists())
        self.assertLessEqual({p.name for p in target.iterdir()} if target.exists() else set(),
                             {"result.json"})
        # What is no archive at all
        with self.assertRaises(cluster.ClusterError):
            cluster_jobs.fetch(
                self.settings, "job1", ["result.json"], target,
                run=lambda command, **kwargs: SimpleNamespace(returncode=0, stdout=b"hello",
                                                              stderr=b""))

    def test_job_is_stopped(self):
        """!
        @brief scancel gets the number of the job, and nothing else.
        """
        cluster_jobs.cancel(self.settings, "4242", program=self.ssh)
        self.assertEqual("4242\n", (self.server.dir / "scancel.log").read_text())
        for wrong in ("4242 4243", "--user=spoel", "", "42;id"):
            with self.assertRaises(cluster.ClusterError):
                cluster_jobs.cancel(self.settings, wrong, program=self.ssh)
        self.assertEqual("4242\n", (self.server.dir / "scancel.log").read_text())

    def test_server_that_does_not_answer(self):
        """!
        @brief A login that takes too long is given up with a message.
        """
        def slow(command, **kwargs):
            raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 0))
        with self.assertRaises(cluster.ClusterError) as caught:
            cluster_jobs.status(self.settings, "job1", "4242", run=slow)
        self.assertIn("did not answer", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
