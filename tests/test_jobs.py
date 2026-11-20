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
@brief Tests of the job engine, with a function in the place of act.run().
"""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from act_gui import jobs
from act_gui.jobs import JobError, JobManager


def wait_for(job, states, seconds=10.0):
    """!
    @brief Wait until a job is in one of the given states.
    @param job     The job.
    @param states  The states to wait for.
    @param seconds The longest time to wait.
    @return Whether the job got there.
    """
    end = time.time() + seconds
    while time.time() < end:
        if job.status in states:
            return True
        time.sleep(0.01)
    return False


class FakeRun:
    """!
    @brief Stands in for act.run(): records its calls and does what the
    option behaviour says.
    """

    def __init__(self):
        """!
        @brief Start without calls.
        """
        self.calls = []
        self.release = threading.Event()

    def __call__(self, command, *, workdir, timeout=None, stop=None, on_progress=None, **options):
        """!
        @brief Pretend to run a command.
        @param command     The command.
        @param workdir     The directory of the job.
        @param timeout     The time limit.
        @param stop        The event that stops the run.
        @param on_progress The function for the progress.
        @param options     The options. behaviour chooses what happens.
        @return An object with data and files, as act.Result has.
        """
        self.calls.append({"command": command, "workdir": workdir, "timeout": timeout,
                           "options": options, "progress": on_progress is not None})
        behaviour = options.get("behaviour", "ok")
        if behaviour == "fail":
            raise RuntimeError("alexandria minimize failed with exit status 1: no parameters")
        if behaviour == "block":
            # Until the test lets go, or the job is stopped
            while not self.release.is_set():
                if stop is not None and stop.is_set():
                    error = type("ActCancelledError", (RuntimeError,), {})
                    raise error("stopped")
                time.sleep(0.01)
        if on_progress is not None:
            for step in range(3):
                on_progress({"step": step})
        Path(workdir).mkdir(parents=True, exist_ok=True)
        out = Path(workdir) / "confout.pdb"
        out.write_text("ATOM\n")
        files = {"c": [out], "empty": []}
        if isinstance(options.get("o"), Path) and behaviour != "unchanged":
            # A command that writes a force field
            options["o"].write_text("<alexandria_chemistry/>\n")
            files["o"] = [options["o"]]
        return SimpleNamespace(data={"rmsd": {"value": 0.02, "unit": "nm"}}, files=files)


class JobTest(unittest.TestCase):
    """!
    @brief Jobs are started, followed, stopped and listed.
    """

    def setUp(self):
        """!
        @brief Make an engine with two workers that accepts three jobs.
        """
        self._tmp = tempfile.TemporaryDirectory()
        self.run = FakeRun()
        self.manager = JobManager(Path(self._tmp.name) / "jobs", self.run,
                                  max_workers=2, max_active=3, timeout=42.0, max_progress=2)

    def tearDown(self):
        """!
        @brief Stop the engine.
        """
        self.run.release.set()
        self.manager.shutdown()
        self._tmp.cleanup()

    def test_job_runs_and_keeps_its_result(self):
        """!
        @brief A job goes from queued to finished and has result and files.
        """
        job = self.manager.submit("minimize", "minimize", {"maxiter": 5})
        self.assertTrue(wait_for(job, (jobs.FINISHED,)))
        self.assertEqual(0.02, job.result["rmsd"]["value"])
        self.assertEqual(["c"], sorted(job.files))
        self.assertIsNotNone(job.started)
        self.assertIsNotNone(job.ended)
        call = self.run.calls[0]
        self.assertEqual("minimize", call["command"])
        self.assertEqual({"maxiter": 5}, call["options"])
        self.assertEqual(42.0, call["timeout"])
        # Each job has its own directory, named by the engine
        self.assertEqual(job.workdir, call["workdir"])
        self.assertEqual(job.id, job.workdir.name)
        self.assertIs(job, self.manager.get(job.id))

    def test_summary_has_no_paths(self):
        """!
        @brief What goes to the browser names files by option, not by path.
        """
        job = self.manager.submit("minimize", "minimize", {})
        self.assertTrue(wait_for(job, (jobs.FINISHED,)))
        summary = self.manager.summary(job)
        self.assertEqual(["c"], summary["files"])
        self.assertEqual("finished", summary["status"])
        self.assertNotIn(self._tmp.name, repr(summary))

    def test_output_files_get_names_in_the_job_directory(self):
        """!
        @brief Output files with a chosen name are put in the job directory.
        """
        job = self.manager.submit("minimize", "minimize", {},
                                  outputs={"c": "../../outside/min.pdb"})
        self.assertTrue(wait_for(job, (jobs.FINISHED,)))
        self.assertEqual(job.workdir / "min.pdb", self.run.calls[0]["options"]["c"])

    def test_input_files_are_written_in_the_job_directory(self):
        """!
        @brief Files that the server makes for a job are in its directory
        before it runs, whatever their name says, and stay available.
        """
        job = self.manager.submit(
            "gen_ff", "gen_ff", {},
            outputs={"o": "forcefield.xml"},
            inputs={"f": [jobs.InputFile("../../outside/atomtypes.csv", "#atomtype|a\nhc|1\n")],
                    "vs": [jobs.InputFile("vsites.json", "{}")]})
        self.assertTrue(wait_for(job, (jobs.FINISHED,)))
        options = self.run.calls[0]["options"]
        # One file for -f, a list for -vs, which takes several files
        self.assertEqual(job.workdir / "atomtypes.csv", options["f"])
        self.assertEqual([job.workdir / "vsites.json"], options["vs"])
        self.assertEqual("#atomtype|a\nhc|1\n", options["f"].read_text())
        self.assertFalse((job.workdir.parent.parent / "outside").exists())
        # They can be fetched next to the output files
        self.assertEqual(["c", "f", "o", "vs"], sorted(job.files))
        self.assertEqual(job.workdir / "atomtypes.csv", job.files["f"])
        self.assertEqual(job.workdir / "forcefield.xml", job.files["o"])

    def test_failure_is_reported(self):
        """!
        @brief A job that fails has the message of the error.
        """
        job = self.manager.submit("minimize", "minimize", {"behaviour": "fail"})
        self.assertTrue(wait_for(job, (jobs.FAILED,)))
        self.assertIn("no parameters", job.error)
        self.assertIsNone(self.manager.summary(job)["result"])

    def test_progress_is_kept_up_to_the_maximum(self):
        """!
        @brief Progress reports are kept, but not without limit.
        """
        job = self.manager.submit("simulate", "simulate", {}, progress=True)
        self.assertTrue(wait_for(job, (jobs.FINISHED,)))
        self.assertTrue(self.run.calls[0]["progress"])
        self.assertEqual([{"step": 0}, {"step": 1}], job.progress)
        summary = self.manager.summary(job, progress_from=1)
        self.assertEqual([{"step": 1}], summary["progress"])
        self.assertEqual(2, summary["progress_total"])

    def test_running_job_can_be_cancelled(self):
        """!
        @brief A job that runs is stopped.
        """
        job = self.manager.submit("simulate", "simulate", {"behaviour": "block"})
        self.assertTrue(wait_for(job, (jobs.RUNNING,)))
        self.assertTrue(self.manager.cancel(job.id))
        self.assertTrue(wait_for(job, (jobs.CANCELLED,)))
        self.assertEqual("", job.error)
        self.assertFalse(self.manager.cancel("nosuchjob"))

    def test_waiting_job_can_be_cancelled(self):
        """!
        @brief A job in the queue never starts when it is cancelled.
        """
        blockers = [self.manager.submit("simulate", "simulate", {"behaviour": "block"})
                    for _ in range(2)]
        for job in blockers:
            self.assertTrue(wait_for(job, (jobs.RUNNING,)))
        waiting = self.manager.submit("minimize", "minimize", {})
        self.assertEqual(jobs.QUEUED, waiting.status)
        self.manager.cancel(waiting.id)
        self.run.release.set()
        self.assertTrue(wait_for(waiting, (jobs.CANCELLED,)))
        self.assertEqual(2, len(self.run.calls))

    def test_too_many_jobs_are_refused(self):
        """!
        @brief The queue has a maximum length.
        """
        for _ in range(3):
            self.manager.submit("simulate", "simulate", {"behaviour": "block"})
        with self.assertRaises(JobError):
            self.manager.submit("minimize", "minimize", {})
        self.run.release.set()

    def test_list_newest_first(self):
        """!
        @brief The list of jobs starts with the newest.
        """
        first = self.manager.submit("minimize", "minimize", {})
        time.sleep(0.02)
        second = self.manager.submit("nma", "nma", {})
        self.assertEqual([second.id, first.id], [job.id for job in self.manager.list()])


if __name__ == "__main__":
    unittest.main()
