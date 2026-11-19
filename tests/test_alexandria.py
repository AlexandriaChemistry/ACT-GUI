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
@brief Tests of the web interface with the real alexandria program.

They run when the environment variable ACT_ALEXANDRIA points to the program,
and are skipped otherwise.
"""
import os
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from act_gui.app import TOKEN_HEADER, create_app
from act_gui.settings import Settings

## The program, if it is given
ALEXANDRIA = os.environ.get("ACT_ALEXANDRIA", "")


@unittest.skipUnless(ALEXANDRIA, "ACT_ALEXANDRIA is not set")
class RealProgramTest(unittest.TestCase):
    """!
    @brief Calculations from a request to their results.
    """

    @classmethod
    def setUpClass(cls):
        """!
        @brief Make an application that runs the real program.
        """
        import act  # pylint: disable=import-outside-toplevel
        cls._executable = act.config.executable
        act.config.executable = ALEXANDRIA
        cls._tmp = tempfile.TemporaryDirectory()
        cls.settings = Settings(data_dir=Path(cls._tmp.name), token="t")
        cls.app = create_app(cls.settings)
        cls.client = TestClient(cls.app)
        cls.headers = {TOKEN_HEADER: "t"}

    @classmethod
    def tearDownClass(cls):
        """!
        @brief Stop the jobs and restore the settings.
        """
        import act  # pylint: disable=import-outside-toplevel
        cls.app.state.jobs.shutdown()
        act.config.executable = cls._executable
        cls._tmp.cleanup()

    def run_task(self, body, seconds=120):
        """!
        @brief Start a job and wait for it.
        @param body    The request.
        @param seconds The longest time to wait.
        @return The job when it is done.
        """
        answer = self.client.post("/api/jobs", headers=self.headers, json=body)
        self.assertEqual(200, answer.status_code, answer.text)
        job_id = answer.json()["id"]
        end = time.time() + seconds
        while time.time() < end:
            job = self.client.get("/api/jobs/" + job_id, headers=self.headers).json()
            if job["status"] not in ("queued", "running"):
                return job
            time.sleep(0.05)
        self.fail("The job did not finish")
        return None

    def test_tasks_have_the_options_of_the_program(self):
        """!
        @brief The forms are built from what the program says about itself.
        """
        tasks = self.client.get("/api/tasks", headers=self.headers).json()["tasks"]
        for task in tasks:
            for option in task["options"]:
                self.assertIn("type", option, "%s %s" % (task["name"], option["name"]))
                self.assertTrue(option["description"])

    def test_minimize_from_smiles_with_the_default_force_field(self):
        """!
        @brief Acetone from SMILES: charges, structures and the file.
        """
        job = self.run_task({"task": "minimize", "molecule": {"smiles": "CC(=O)C"}})
        self.assertEqual("finished", job["status"], job["error"])
        result = job["result"]
        self.assertTrue(result["converged"])
        self.assertEqual("C3H6O", result["molecule"]["formula"])
        # The default force field is polarizable: atoms and shells
        self.assertEqual(20, len(result["molecule"]["particles"]))
        self.assertEqual(20, len(result["coordinates"]["after"]))
        pdb = self.client.get("/api/jobs/%s/files/c" % job["id"], headers=self.headers)
        self.assertEqual(200, pdb.status_code)
        self.assertIn("ATOM", pdb.text)

    def test_charges(self):
        """!
        @brief The particles with their charges and positions.
        """
        job = self.run_task({"task": "charges", "molecule": {"smiles": "O"}})
        self.assertEqual("finished", job["status"], job["error"])
        compound = job["result"]["molecules"][0]
        self.assertEqual("H2O", compound["molecule"]["formula"])
        self.assertEqual(len(compound["molecule"]["particles"]),
                         len(compound["coordinates"]["positions"]))

    def test_normal_modes(self):
        """!
        @brief Frequencies, displacements and the spectrum file.
        """
        job = self.run_task({"task": "nma", "molecule": {"smiles": "O"}})
        self.assertEqual("finished", job["status"], job["error"])
        self.assertEqual(3, len(job["result"]["modes"]))
        spectrum = self.client.get("/api/jobs/%s/files/ir" % job["id"], headers=self.headers)
        self.assertEqual(200, spectrum.status_code)

    def test_simulation_with_progress(self):
        """!
        @brief A short simulation reports its progress and gives a trajectory.
        """
        job = self.run_task({"task": "simulate", "molecule": {"smiles": "CC(=O)C"},
                             "options": {"nsteps": 200, "nstener": 20, "nstxout": 50,
                                         "temp": 300, "seed": 7, "minimize": True}})
        self.assertEqual("finished", job["status"], job["error"])
        self.assertEqual(list(range(0, 200, 20)), [p["step"] for p in job["progress"]])
        trajectory = self.client.get("/api/jobs/%s/files/o" % job["id"], headers=self.headers)
        self.assertEqual(200, trajectory.status_code)
        self.assertEqual(4, trajectory.text.count("ENDMDL"))

    def test_wrong_smiles_fails_with_a_message(self):
        """!
        @brief The message of the program reaches the page.
        """
        job = self.run_task({"task": "minimize", "molecule": {"smiles": "C(C"}})
        self.assertEqual("failed", job["status"])
        self.assertIn("SMILES", job["error"])

    def test_wrong_value_fails_with_a_message(self):
        """!
        @brief A value of the wrong type is refused by the Python interface.
        """
        job = self.run_task({"task": "minimize", "molecule": {"smiles": "CCO"},
                             "options": {"minalg": "Fastest"}})
        self.assertEqual("failed", job["status"])
        self.assertIn("minalg", job["error"])


if __name__ == "__main__":
    unittest.main()
