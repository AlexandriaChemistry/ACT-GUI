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
@brief Tests of the jobs on the cluster as the interface keeps them: how a
calculation becomes a job, how it is followed with few logins, and what is
there when the interface starts again. A directory stands in for the
cluster and small programs for ssh and Slurm, so that no test logs in
anywhere.
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from act_gui import cluster, cluster_queue
from act_gui.cluster_queue import ClusterQueue, make_plan
from act_gui.jobs import InputFile
from tests.test_cluster import FakeServer

## The options of the stand-in for the commands that write a file, with
## the name of the file if none is given
OUTPUTS = {"json": "%s.json", "epotcorr": "epot_corr.xvg", "o": "out.xml", "g": "%s.log",
           "progress": "progress.jsonl"}


def fake_arguments(command, options, workdir):
    """!
    @brief Stands in for act.build_arguments() with the command looked up.
    @param command The command.
    @param options The options.
    @param workdir The directory the command runs in.
    @return The arguments, and the files that the command writes by option.
    """
    args, outputs = [], {}
    for key, value in options.items():
        if value is None:
            continue
        if isinstance(value, bool) and key in OUTPUTS:
            if value:
                name = OUTPUTS[key]
                outputs[key] = [Path(workdir) / (name % command if "%" in name else name)]
                args += ["-" + key, str(outputs[key][0])]
        elif isinstance(value, bool):
            args.append(("-" if value else "-no") + key)
        else:
            items = list(value) if isinstance(value, (list, tuple)) else [value]
            args += ["-" + key] + [str(item) for item in items]
            if key in OUTPUTS:
                outputs[key] = [Path(item) for item in items]
    # The log file is written without being asked
    outputs.setdefault("g", [Path(workdir) / ("%s.log" % command)])
    return args, outputs


class Clock:
    """!
    @brief A clock that the test sets.
    """

    def __init__(self):
        """!
        @brief Start at some time.
        """
        self.now = 1000.0

    def __call__(self):
        """!
        @brief Tell the time.
        @return The seconds.
        """
        return self.now


class QueueCase(unittest.TestCase):
    """!
    @brief What the tests of the jobs on the cluster share.
    """

    def setUp(self):
        """!
        @brief Make the stand-in for a cluster, files of a calculation and
        an empty list of jobs.
        """
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name).resolve()
        self.server = FakeServer(self.dir / "server")
        self.settings = cluster.parse({"server": "good", "user": "spoel", "workdir": "act/jobs",
                                       "load": "none"})
        self.remote = self.server.dir / "home" / "act" / "jobs"
        self.forcefield = self.dir / "uploads" / "0123abcd.xml"
        self.forcefield.parent.mkdir()
        self.forcefield.write_text("<alexandria_chemistry/>\n")
        self.molprop = self.dir / "uploads" / "4567cdef.xml"
        self.molprop.write_text("<molecules/>\n")
        self.clock = Clock()
        self.local = self.dir / "cluster-jobs"
        self.queue = self.new_queue()

    def tearDown(self):
        """!
        @brief Remove the files.
        """
        self._tmp.cleanup()

    def new_queue(self):
        """!
        @brief The list of jobs, as when the interface starts.
        @return The list.
        """
        return ClusterQueue(self.local, program=str(self.server.program), clock=self.clock)

    def plan(self, name, staging, **more):
        """!
        @brief A comparison of a force field with a molprop file as a job.
        @param name    The name of the job.
        @param staging The directory that stands for that of the job.
        @param more    More options of the command.
        @return The plan.
        """
        options = {"ff": self.forcefield, "mp": [self.molprop], "fc_epot": 1, "epotcorr": True,
                   "json": True, "optimize": False}
        options.update(more)
        return make_plan(name, "evaluate", "train_ff", options, staging, fake_arguments,
                         self.settings,
                         inputs={"sel": [InputFile("selection.dat", "water|Train\n")]},
                         collect={"INTERACTION": "INTERACTION.xvg"}, tasks=4, minutes=90)

    def send(self, name="job1", **more):
        """!
        @brief Send a job.
        @param name The name of the job.
        @param more More options of the command.
        @return The job.
        """
        staging = self.queue.reserve(name)
        return self.queue.send(self.plan(name, staging, **more), self.settings)

    def logins(self):
        """!
        @brief The number of times that ssh was started.
        @return The number.
        """
        return len(self.server.calls())


class PlanTest(QueueCase):
    """!
    @brief A calculation as a job for the cluster.
    """

    def test_plan(self):
        """!
        @brief The files get a name in the directory of the job, and the
        command and the script have those names only.
        """
        staging = self.dir / "staging"
        staging.mkdir()
        plan = self.plan("compare-1", staging)
        self.assertEqual(["alexandria", "train_ff", "-ff", "ff.xml", "-mp", "mp.xml",
                          "-fc_epot", "1", "-epotcorr", "epot_corr.xvg",
                          "-json", "train_ff.json", "-nooptimize", "-sel", "selection.dat"],
                         plan.words)
        self.assertEqual({"ff.xml": self.forcefield, "mp.xml": self.molprop,
                          "selection.dat": staging / "selection.dat"}, plan.files)
        self.assertEqual("water|Train\n", (staging / "selection.dat").read_text())
        self.assertEqual({"epotcorr": ["epot_corr.xvg"], "json": ["train_ff.json"],
                          "g": ["train_ff.log"]}, plan.outputs)
        self.assertEqual({"sel": "selection.dat"}, plan.inputs)
        self.assertEqual({"INTERACTION": "INTERACTION.xvg"}, plan.collect)
        self.assertIn("#SBATCH --job-name=compare-1\n", plan.script)
        self.assertIn("#SBATCH -n 4\n", plan.script)
        self.assertIn("#SBATCH --time=0-01:30:00\n", plan.script)
        self.assertIn("\nsrun alexandria train_ff -ff ff.xml -mp mp.xml ", plan.script)
        # Nothing of this machine goes to the cluster
        self.assertNotIn(str(self.dir), plan.script)
        self.assertNotIn("0123abcd", plan.script)
        described = plan.describe(self.settings)
        self.assertEqual(("compare-1", "spoel@good", "act/jobs/compare-1"),
                         (described["name"], described["server"], described["directory"]))
        self.assertEqual([{"name": "ff.xml", "size": 24}, {"name": "mp.xml", "size": 13},
                          {"name": "selection.dat", "size": 12}], described["files"])
        self.assertTrue(described["command"].startswith("alexandria train_ff -ff ff.xml"))
        self.assertEqual(plan.script, described["script"])

    def test_program_with_another_name(self):
        """!
        @brief The job starts the program that the settings name, such as
        that of a build in double precision.
        """
        settings = cluster.parse({"server": "good", "user": "spoel", "workdir": "act/jobs",
                                  "load": "none", "program": "alexandria_d"})
        staging = self.queue.reserve("job1")
        options = {"ff": self.forcefield, "mp": [self.molprop], "json": True}
        plan = make_plan("job1", "evaluate", "train_ff", options, staging, fake_arguments,
                         settings, tasks=2)
        self.assertEqual(["alexandria_d", "train_ff", "-ff", "ff.xml"], plan.words[:4])
        self.assertIn("\nsrun alexandria_d train_ff -ff ff.xml ", plan.script)
        self.queue.release("job1")

    def test_several_files_for_one_option(self):
        """!
        @brief Each file of an option that takes several gets a name.
        """
        second = self.dir / "uploads" / "second.XML"
        second.write_text("<molecules/>\n")
        odd = self.dir / "uploads" / "data.a b"
        odd.write_text("x")
        staging = self.dir / "staging"
        staging.mkdir()
        plan = self.plan("j", staging, mp=[self.molprop, second], extra=odd)
        self.assertEqual({"ff.xml", "mp-1.xml", "mp-2.XML", "extra", "selection.dat"},
                         set(plan.files))
        self.assertIn("-mp mp-1.xml mp-2.XML ", " ".join(plan.words))

    def test_plans_that_are_refused(self):
        """!
        @brief A file that is gone, a file that cannot be sent and a name,
        a number of processes or a time that is not possible.
        """
        staging = self.dir / "staging"
        staging.mkdir()
        cases = [
            dict(ff=self.dir / "uploads" / "gone.xml"),
            # The name of a file of this machine in an option that is no file
            dict(qalg="/etc/passwd"),
            # An output file outside the directory of the job
            dict(o=str(self.dir / "elsewhere.xml")),
        ]
        for more in cases:
            with self.assertRaises(cluster.ClusterError, msg=more):
                self.plan("j", staging, **more)
        for name in ("", "a b", "../x", "-x", 7):
            with self.assertRaises(cluster.ClusterError, msg=name):
                self.plan(name, staging)
        options = {"ff": self.forcefield}
        for more in (dict(tasks=0), dict(tasks=True), dict(minutes=0), dict(minutes=10 ** 6)):
            with self.assertRaises(cluster.ClusterError, msg=more):
                make_plan("j", "evaluate", "train_ff", options, staging, fake_arguments,
                          self.settings, **more)


class QueueTest(QueueCase):
    """!
    @brief Jobs from the first login to the results.
    """

    def test_job_runs_and_its_results_come_back(self):
        """!
        @brief One login to send, one to ask and one to fetch; then the
        results are on this machine and the job is as a job that ran here.
        """
        job = self.send()
        self.assertEqual(("job1", "4242", "waiting", False), (job.name, job.slurm, job.state,
                                                              job.fetched))
        self.assertEqual(1, self.logins())
        self.assertTrue((self.remote / "job1" / "train_ff.json").is_file())
        # The job is noted with the script that was sent
        self.assertEqual(["job.json", "job.sh", "selection.dat"],
                         sorted(p.name for p in (self.local / "job1").iterdir()))
        self.clock.now += 200
        answer = self.queue.check(120)
        self.assertEqual((True, "", False), (answer["asked"], answer["problem"],
                                             answer["paused"]))
        self.assertEqual(3, self.logins())
        self.assertEqual(("finished", 0, True, ""), (job.state, job.exit, job.fetched, job.error))
        self.assertEqual(self.clock.now, job.ended)
        data, files, inputs = self.queue.results("job1")
        self.assertEqual({"Train": {"INTERACTION": {"N": 2, "RMSD": 1.0}}}, data["statistics"])
        local = self.local / "job1"
        self.assertEqual({"sel": local / "selection.dat", "json": local / "train_ff.json",
                          "epotcorr": local / "epot_corr.xvg",
                          "INTERACTION": local / "INTERACTION.xvg"}, files)
        self.assertEqual({"sel": local / "selection.dat"}, inputs)
        self.assertIn("running train_ff", (local / "slurm.out").read_text())
        # Nothing more to ask
        self.clock.now += 200
        self.assertFalse(self.queue.check(120)["asked"])
        self.assertEqual(3, self.logins())
        summary = self.queue.summaries()[0]
        self.assertEqual(("job1", "evaluate", "finished", "spoel@good", "act/jobs/job1", True),
                         tuple(summary[key] for key in ("name", "task", "state", "server",
                                                        "directory", "fetched")))
        self.assertNotIn(str(self.dir), json.dumps(summary))

    def test_progress_of_a_job(self):
        """!
        @brief How far a job is comes with its state, in the same login,
        and is kept with the job; when the job has ended, all it reported
        is on this machine.
        """
        job = self.send(progress=True)
        self.assertEqual("progress.jsonl", job.progress_file)
        self.assertIsNone(job.progress)
        # As if it were still running
        (self.server.dir / "queue").write_text("RUNNING\n")
        (self.remote / "job1" / "act-exit-status").unlink()
        self.clock.now += 200
        self.assertTrue(self.queue.check(120)["asked"])
        self.assertEqual(2, self.logins())
        expected = {"event": "generation", "generation": 2, "ngenerations": 3, "chi2": 46.5,
                    "seconds": 3.0}
        self.assertEqual(("running", expected), (job.state, job.progress))
        self.assertEqual(expected, self.queue.summaries()[0]["progress"])
        self.assertEqual([], self.queue.progress_reports("job1", 10))
        # Kept between runs, and checked when it is read
        self.assertEqual(expected, self.new_queue().get("job1").progress)
        record = self.local / "job1" / "job.json"
        content = json.loads(record.read_text())
        content["progress"] = {"event": "generation", "generation": "<b>", "more": [1]}
        record.write_text(json.dumps(content))
        self.assertEqual({"event": "generation"}, self.new_queue().get("job1").progress)
        content["progress"] = "rm -rf"
        record.write_text(json.dumps(content))
        self.assertIsNone(self.new_queue().get("job1").progress)
        # The job ends: what it reported comes with the results
        (self.server.dir / "queue").unlink()
        (self.remote / "job1" / "act-exit-status").write_text("0\n")
        self.clock.now += 200
        self.assertTrue(self.queue.check(120)["asked"])
        self.assertEqual(("finished", True), (job.state, job.fetched))
        reports = self.queue.progress_reports("job1", 10)
        self.assertEqual(["start", "generation", "generation", "step"],
                         [report["event"] for report in reports])
        self.assertEqual(reports[:2], self.queue.progress_reports("job1", 2))
        # Lines that are no objects are left out
        (self.local / "job1" / "progress.jsonl").write_text('{"event":"start"}\nnot json\n[1]\n')
        self.assertEqual([{"event": "start"}], self.queue.progress_reports("job1", 10))
        # A job that does not report, and one that does not exist
        self.send("job2")
        self.assertEqual("", self.queue.get("job2").progress_file)
        self.assertEqual([], self.queue.progress_reports("job2", 10))
        self.assertEqual([], self.queue.progress_reports("nosuchjob", 10))

    def test_jobs_are_there_when_the_interface_starts_again(self):
        """!
        @brief What is known about a job is in a file; a later run reads
        it, follows the job and has its results.
        """
        (self.server.dir / "queue").write_text("PENDING\n")
        self.send()
        again = self.new_queue()
        job = again.get("job1")
        self.assertEqual(("4242", "waiting", self.settings, 4, 90, False),
                         (job.slurm, job.state, job.cluster, job.tasks, job.minutes, job.gpu))
        # The program ends
        (self.server.dir / "queue").unlink()
        remote = self.remote / "job1"
        (remote / "act-exit-status").write_text("0\n")
        (remote / "train_ff.json").write_text('{"train_ff": {"statistics": {}}}')
        self.clock.now += 500
        self.assertTrue(again.check(120)["asked"])
        self.assertEqual("finished", again.get("job1").state)
        third = self.new_queue()
        self.assertEqual(("finished", True), (third.get("job1").state, third.get("job1").fetched))
        self.assertEqual({"statistics": {}}, third.results("job1")[0])
        self.assertEqual([], third.unread)

    def test_files_that_are_no_job_are_left_alone(self):
        """!
        @brief A file that was changed into something else is not used,
        and is not removed either.
        """
        (self.server.dir / "queue").write_text("PENDING\n")
        self.send("job1")
        self.send("job2")
        self.send("job3")
        record = json.loads((self.local / "job1" / "job.json").read_text())
        (self.local / "job1" / "job.json").write_text(
            json.dumps(dict(record, slurm="42; reboot")))
        (self.local / "job2" / "job.json").write_text(
            json.dumps(dict(record, cluster=dict(record["cluster"], server="-oProxyCommand=x"),
                            name="job2")))
        (self.local / "job3" / "job.json").write_text("{not json")
        (self.local / "other").mkdir()
        again = self.new_queue()
        self.assertEqual([], again.list())
        self.assertEqual(["job1", "job2", "job3"], again.unread)
        self.assertTrue((self.local / "job1" / "job.json").is_file())
        # A name that is taken by such a directory cannot be used
        with self.assertRaises(cluster.ClusterError):
            again.reserve("job1")

    def test_cluster_is_asked_seldom(self):
        """!
        @brief One login tells the state of all jobs, and not before the
        time between two questions has passed; the user can ask sooner,
        but not at once.
        """
        (self.server.dir / "queue").write_text("PENDING\n")
        self.send("job1")
        self.send("job2")
        self.assertEqual(2, self.logins())
        self.clock.now += 130
        self.assertTrue(self.queue.check(120)["asked"])
        self.assertEqual(3, self.logins())
        self.assertEqual(["waiting", "waiting"], [job.state for job in self.queue.list()])
        self.assertEqual(self.clock.now, self.queue.get("job1").checked)
        # Too soon
        self.clock.now += 20
        answer = self.queue.check(120)
        self.assertEqual((False, 100.0), (answer["asked"], answer["wait"]))
        answer = self.queue.check(120, manual=True)
        self.assertEqual((False, 10.0), (answer["asked"], answer["wait"]))
        self.assertEqual(3, self.logins())
        self.clock.now += 15
        (self.server.dir / "queue").write_text("RUNNING\n")
        self.assertTrue(self.queue.check(120, manual=True)["asked"])
        self.assertEqual(4, self.logins())
        self.assertEqual(["running", "running"], [job.state for job in self.queue.list()])
        self.assertEqual("RUNNING", self.queue.get("job2").slurm_state)
        # A later run of the interface does not ask sooner either
        again = self.new_queue()
        self.clock.now += 60
        self.assertEqual(60.0, again.wait(120))
        self.assertFalse(again.check(120)["asked"])
        self.assertEqual(4, self.logins())

    def test_job_that_fails(self):
        """!
        @brief The exit status and the end of the output tell why.
        """
        job = self.send(fail=True)
        self.clock.now += 200
        self.queue.check(120)
        self.assertEqual(("failed", 3, True), (job.state, job.exit, job.fetched))
        self.assertIn("The program ended with status 3", job.error)
        self.assertIn("running train_ff", job.error)
        with self.assertRaises(cluster.ClusterError):
            self.queue.results("job1")

    def test_job_that_slurm_lost(self):
        """!
        @brief A job that Slurm does not know and that left no exit status
        is asked for once more before it counts as lost.
        """
        (self.server.dir / "queue").write_text("RUNNING\n")
        job = self.send()
        self.clock.now += 200
        self.queue.check(120)
        self.assertEqual("running", job.state)
        (self.server.dir / "queue").write_text("")
        self.clock.now += 200
        self.queue.check(120)
        self.assertEqual(("running", 1), (job.state, job.missing))
        self.clock.now += 200
        self.queue.check(120)
        self.assertEqual("lost", job.state)
        self.assertIn("longer than the 90 minutes", job.error)
        self.assertIsNotNone(job.ended)

    def test_login_that_fails_is_not_tried_again_by_itself(self):
        """!
        @brief After a login that did not work the cluster is asked again
        only when the user says so.
        """
        (self.server.dir / "queue").write_text("PENDING\n")
        self.send()
        record = json.loads((self.local / "job1" / "job.json").read_text())
        record["cluster"]["server"] = "nologin"
        (self.local / "job1" / "job.json").write_text(json.dumps(record))
        queue = self.new_queue()
        self.clock.now += 200
        answer = queue.check(120)
        self.assertEqual((True, True), (answer["asked"], answer["paused"]))
        self.assertIn("Logging in on nologin as spoel did not work", answer["problem"])
        self.assertEqual("waiting", queue.get("job1").state)
        logins = self.logins()
        for _ in range(3):
            self.clock.now += 200
            answer = queue.check(120)
            self.assertEqual((False, True), (answer["asked"], answer["paused"]))
            self.assertIn("did not work", answer["problem"])
        self.assertEqual(logins, self.logins())
        self.assertIn("did not work", queue.paused)
        # The user asks
        self.assertTrue(queue.check(120, manual=True)["asked"])
        self.assertEqual(logins + 1, self.logins())

    def test_results_that_cannot_be_fetched(self):
        """!
        @brief The job stays done, and the results can be fetched later.
        """
        broken = {"on": True}

        def run(command, **kwargs):
            if broken["on"] and "tar cf" in command[-1]:
                return SimpleNamespace(returncode=1, stdout=b"", stderr=b"tar: no space left")
            return subprocess.run(command, **kwargs)  # pylint: disable=subprocess-run-check
        self.queue = ClusterQueue(self.local, program=str(self.server.program), run=run,
                                  clock=self.clock)
        job = self.send()
        self.clock.now += 200
        answer = self.queue.check(120)
        self.assertEqual(("finished", False), (job.state, job.fetched))
        self.assertIn("The results could not be fetched", answer["problem"])
        self.assertIn("no space left", job.error)
        with self.assertRaises(cluster.ClusterError):
            self.queue.results("job1")
        with self.assertRaises(cluster.ClusterError):
            self.queue.fetch("job1")
        # The job is not asked for again, only its results
        self.clock.now += 200
        self.assertFalse(self.queue.check(120)["asked"])
        broken["on"] = False
        self.assertTrue(self.queue.fetch("job1").fetched)
        self.assertEqual("", job.error)
        self.assertIn("statistics", self.queue.results("job1")[0])
        self.assertTrue(self.new_queue().get("job1").fetched)
        with self.assertRaises(cluster.ClusterError):
            self.queue.fetch("nosuchjob")

    def test_stop_and_forget(self):
        """!
        @brief A job that waits is stopped with scancel; a job that is
        done can be taken from the list, with its files on this machine.
        """
        (self.server.dir / "queue").write_text("PENDING\n")
        self.send()
        with self.assertRaises(cluster.ClusterError):
            self.queue.forget("job1")
        job = self.queue.cancel("job1")
        self.assertEqual("cancelled", job.state)
        self.assertEqual("4242\n", (self.server.dir / "scancel.log").read_text())
        self.assertEqual("cancelled", self.new_queue().get("job1").state)
        for call in (self.queue.cancel, self.queue.fetch, self.queue.results):
            with self.assertRaises(cluster.ClusterError):
                call("job1")
        self.clock.now += 500
        self.assertFalse(self.queue.check(120)["asked"])
        self.queue.forget("job1")
        self.assertEqual([], self.queue.list())
        self.assertFalse((self.local / "job1").exists())
        # What is on the cluster stays there
        self.assertTrue((self.remote / "job1" / "job.sh").is_file())
        for call in (self.queue.cancel, self.queue.forget):
            with self.assertRaises(cluster.ClusterError):
                call("job1")

    def test_name_that_is_taken(self):
        """!
        @brief Two jobs cannot have the same name, and a job that could
        not be sent leaves nothing behind.
        """
        self.send()
        with self.assertRaises(cluster.ClusterError) as caught:
            self.queue.reserve("job1")
        self.assertIn("choose another name", str(caught.exception))
        # The directory is there on the cluster, from another machine
        (self.remote / "job2").mkdir()
        staging = self.queue.reserve("job2")
        with self.assertRaises(cluster.ClusterError):
            self.queue.send(self.plan("job2", staging), self.settings)
        self.queue.release("job2")
        self.assertFalse((self.local / "job2").exists())
        self.assertIsNone(self.queue.get("job2"))
        # A job that was sent is not removed this way
        self.queue.release("job1")
        self.assertTrue((self.local / "job1" / "job.json").is_file())
        self.assertEqual(cluster_queue.WAITING, self.queue.get("job1").state)


if __name__ == "__main__":
    unittest.main()
