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
## The directory src/act/testdata of the source of ACT, if it is given
TESTDATA = os.environ.get("ACT_TESTDATA", "")


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

    # --- the force field workspace ---

    def default_types(self, **extra):
        """!
        @brief The atom types that the generator proposes.
        @param extra What to set for each of them, such as shell=True.
        @return The list for a request.
        """
        described = self.client.get("/api/generator", headers=self.headers).json()
        return [dict({"name": t["name"]}, **extra) for t in described["atomtypes"]
                if t["default"]]

    def parameter(self, job, interaction, identifier, name):
        """!
        @brief Look up a parameter in the description of a force field.
        @param job         A job with a description.
        @param interaction The type of the interaction.
        @param identifier  The identifier of the parameter list.
        @param name        The name of the parameter.
        @return The parameter, None if it is not there.
        """
        for inter in job["result"]["forcefield"]["interactions"]:
            if inter["type"] == interaction:
                for entry in inter["parameterlists"]:
                    if entry["identifier"] == identifier:
                        for param in entry["parameters"]:
                            if param["type"] == name:
                                return param
        return None

    def test_generate_force_fields(self):
        """!
        @brief gen_ff makes a valid force field of what the generator
        proposes, of a polarizable model and of one with virtual sites.
        """
        plain = self.run_task({"task": "gen_ff",
                               "generator": {"atomtypes": self.default_types()}})
        self.assertEqual("finished", plain["status"], plain["error"])
        described = plain["result"]["forcefield"]
        self.assertFalse(described["polarizable"])
        self.assertTrue(plain["result"]["validation"]["ok"])
        ntypes = len(described["particletypes"]["types"])
        self.assertGreater(ntypes, 40)
        kinds = {inter["type"]: inter["function"] for inter in described["interactions"]}
        self.assertEqual(("WANG_BUCKINGHAM", "HUA_BONDS"),
                         (kinds["VANDERWAALS"], kinds["BONDS"]))
        written = self.client.get("/api/jobs/%s/files/o" % plain["id"], headers=self.headers)
        self.assertEqual(200, written.status_code)
        self.assertIn("<alexandria", written.text[:300])
        table = self.client.get("/api/jobs/%s/files/f" % plain["id"], headers=self.headers)
        self.assertTrue(table.text.startswith("#atomtype|"))

        polarizable = self.run_task({"task": "gen_ff", "generator": {
            "atomtypes": self.default_types(shell=True),
            "vdw": {"potential": "LJ14_7"},
            "charge": {"model": "P+G"},
            "induction": {"potential": "MACDANIEL_SCHMIDT"},
            "vsites": {"files": ["vsite2_halogen_sigma_holes", "vsite3_water_lone_pairs"],
                       "exchange": True}}})
        self.assertEqual("finished", polarizable["status"], polarizable["error"])
        described = polarizable["result"]["forcefield"]
        self.assertTrue(described["polarizable"])
        self.assertTrue(polarizable["result"]["validation"]["ok"])
        # Each atom type has a shell, and the virtual sites have their types
        self.assertGreater(len(described["particletypes"]["types"]), 2 * ntypes)
        kinds = {inter["type"]: inter for inter in described["interactions"]}
        self.assertEqual("LJ14_7", kinds["VANDERWAALS"]["function"])
        self.assertEqual("Volumetric",
                         kinds["VANDERWAALS"]["combination_rules"]["sigma"]["rule"])
        self.assertIn("VSITE2", kinds)
        self.assertIn("INDUCTIONCORRECTION", kinds)

    def test_describe_edit_and_use_a_force_field(self):
        """!
        @brief A force field is uploaded and described, a parameter is
        changed, and the new force field is used for a molecule.
        """
        data = self.settings.default_forcefield.read_bytes()
        uploaded = self.client.post("/api/uploads", params={"kind": "forcefield",
                                                            "name": "ff.xml"},
                                    content=data, headers=self.headers).json()["id"]
        described = self.run_task({"task": "ff_describe", "forcefield": {"file": uploaded}})
        self.assertEqual("finished", described["status"], described["error"])
        self.assertFalse(described["result"]["changed"])
        self.assertTrue(described["result"]["validation"]["ok"])
        self.assertNotIn("o", described["files"])
        # A parameter that training may change
        target = None
        for inter in described["result"]["forcefield"]["interactions"]:
            for entry in inter["parameterlists"]:
                for param in entry["parameters"]:
                    if (param["mutability"] == "Bounded"
                            and param["maximum"] - param["minimum"] > 1e-3 and target is None):
                        target = (inter["type"], entry["identifier"], param)
        self.assertIsNotNone(target)
        interaction, identifier, before = target
        new = 0.5 * (before["minimum"] + before["maximum"])
        if abs(new - before["value"]) < 1e-6:
            new = 0.75 * before["minimum"] + 0.25 * before["maximum"]
        edited = self.run_task({"task": "ff_edit", "forcefield": {"file": uploaded},
                                "options": {"p": before["type"], "a": identifier, "val": new}})
        self.assertEqual("finished", edited["status"], edited["error"])
        self.assertTrue(edited["result"]["changed"])
        self.assertTrue(edited["result"]["written"])
        after = self.parameter(edited, interaction, identifier, before["type"])
        self.assertAlmostEqual(new, after["value"], places=5)
        self.assertIn("o", edited["files"])
        # The new force field in turn: another change, and a calculation
        fixed = self.run_task({"task": "ff_edit", "forcefield": {"job": edited["id"]},
                               "options": {"p": before["type"], "a": identifier,
                                           "mut": "Fixed"}})
        self.assertEqual("finished", fixed["status"], fixed["error"])
        after = self.parameter(fixed, interaction, identifier, before["type"])
        self.assertEqual("Fixed", after["mutability"])
        self.assertAlmostEqual(new, after["value"], places=5)
        job = self.run_task({"task": "minimize", "molecule": {"smiles": "CC(=O)C"},
                             "forcefield": {"job": fixed["id"]}})
        self.assertEqual("finished", job["status"], job["error"])
        self.assertEqual("C3H6O", job["result"]["molecule"]["formula"])

    def test_change_that_changes_nothing(self):
        """!
        @brief A change of a type that is not in the force field leaves no
        force field behind, and the result says that nothing changed.
        """
        made = self.run_task({"task": "gen_ff", "generator": {
            "atomtypes": [{"name": "hc"}, {"name": "c3"}]}})
        self.assertEqual("finished", made["status"], made["error"])
        same = self.run_task({"task": "ff_edit", "forcefield": {"job": made["id"]},
                              "options": {"p": "chi", "a": "nosuch_z", "val": 5}})
        self.assertEqual("finished", same["status"], same["error"])
        self.assertFalse(same["result"]["changed"])
        self.assertNotIn("o", same["files"])
        answer = self.client.post("/api/jobs", headers=self.headers, json={
            "task": "ff_describe", "forcefield": {"job": same["id"]}})
        self.assertEqual(400, answer.status_code)
        # A real change of the same force field
        real = self.run_task({"task": "ff_edit", "forcefield": {"job": made["id"]},
                              "options": {"p": "chi", "a": ["hc_z", "c3_z"], "val": 6.5}})
        self.assertEqual("finished", real["status"], real["error"])
        self.assertTrue(real["result"]["changed"])
        for identifier in ("hc_z", "c3_z"):
            chi = self.parameter(real, "ELECTRONEGATIVITYEQUALIZATION", identifier, "chi")
            self.assertAlmostEqual(6.5, chi["value"], places=5)

    @unittest.skipUnless(os.path.isdir(TESTDATA), "ACT_TESTDATA is not set")
    def test_geometry_from_a_molprop_file(self):
        """!
        @brief A force field gets its bonds from the compounds of a molprop
        file. With a force field that has other atom types than the file no
        compound can be used, which the result and the log tell.
        """
        def send(directory, name, kind):
            """Upload a file of the test data of ACT"""
            answer = self.client.post("/api/uploads", params={"kind": kind, "name": name},
                                      content=(Path(TESTDATA) / directory / name).read_bytes(),
                                      headers=self.headers)
            self.assertEqual(200, answer.status_code, answer.text)
            return answer.json()["id"]
        molprop = {"file": send("molprop", "molprop.xml", "molprop")}
        forcefield = {"file": send("forcefields", "ACS-g1.xml", "forcefield")}
        job = self.run_task({"task": "geometry_ff", "forcefield": forcefield,
                             "molprop": molprop, "options": {"bfactor": 0.9}})
        self.assertEqual("finished", job["status"], job["error"])
        self.assertEqual(3, job["result"]["compounds"])
        self.assertGreater(job["result"]["extracted"]["BONDS"], 3)
        length = self.parameter(job, "BONDS", "c3_b~h_b", "bondlength")
        self.assertEqual(24, length["ntrain"])
        self.assertAlmostEqual(0.9 * length["value"], length["minimum"], places=3)
        repaired = self.run_task({"task": "ff_repair", "forcefield": {"job": job["id"]}})
        self.assertEqual("finished", repaired["status"], repaired["error"])
        self.assertIn("validation", repaired["result"])
        # A new force field with the atom types of the generator, which
        # the compounds of this file do not have
        made = self.run_task({"task": "gen_ff", "generator": {
            "atomtypes": self.default_types(), "bonds": "MORSE_BONDS"}})
        self.assertEqual("finished", made["status"], made["error"])
        none = self.run_task({"task": "geometry_ff", "forcefield": {"job": made["id"]},
                              "molprop": molprop})
        self.assertEqual("finished", none["status"], none["error"])
        self.assertEqual(0, none["result"]["compounds"])
        log = self.client.get("/api/jobs/%s/files/g" % none["id"], headers=self.headers)
        self.assertEqual(200, log.status_code)
        self.assertIn("Cannot find atomtype", log.text)

    # --- the molprop workspace ---

    @unittest.skipUnless(os.path.isdir(TESTDATA), "ACT_TESTDATA is not set")
    def test_describe_molprop_files(self):
        """!
        @brief The content of a file with monomers and of one with dimers,
        and the result of the validation.
        """
        expected = {"molprop.xml": (3, 3, 0, "1-butanol", "DeltaHform"),
                    "mpdimer.xml": (3, 0, 3, "1-propanol#ethane", "InteractionEnergy")}
        for name, (total, monomers, dimers, first, observable) in expected.items():
            uploaded = self.client.post(
                "/api/uploads", params={"kind": "molprop", "name": name},
                content=(Path(TESTDATA) / "molprop" / name).read_bytes(), headers=self.headers)
            self.assertEqual(200, uploaded.status_code, uploaded.text)
            job = self.run_task({"task": "mp_describe",
                                 "molprop": {"file": uploaded.json()["id"]}})
            self.assertEqual("finished", job["status"], job["error"])
            summary = job["result"]["summary"]
            self.assertEqual((total, monomers, dimers),
                             (summary["molecules"], summary["monomers"], summary["dimers"]))
            self.assertGreater(summary["properties"][observable], 0)
            molecules = job["result"]["molecules"]
            self.assertEqual(total, len(molecules))
            self.assertEqual(first, molecules[0]["name"])
            self.assertEqual(2 if dimers else 1, len(molecules[0]["fragments"]))
            validation = job["result"]["validation"]
            self.assertTrue(validation["checked"])
            self.assertTrue(validation["ok"])

    @unittest.skipUnless(os.path.isdir(TESTDATA), "ACT_TESTDATA is not set")
    def test_evaluate_a_force_field(self):
        """!
        @brief A force field is compared with the interaction energies of
        dimers, and another with the energies and forces of compounds: the
        statistics, the files with the comparison and a plot of them.
        """
        from act_gui import plots  # pylint: disable=import-outside-toplevel

        def send(directory, name, kind):
            """Upload a file of the test data of ACT"""
            answer = self.client.post("/api/uploads", params={"kind": kind, "name": name},
                                      content=(Path(TESTDATA) / directory / name).read_bytes(),
                                      headers=self.headers)
            self.assertEqual(200, answer.status_code, answer.text)
            return answer.json()["id"]
        cases = (("mpdimer.xml", "ACS-pol.xml", ["inter", "elec"], "INTERACTION", 243,
                  ("INTERACTION", "COULOMB")),
                 ("booze-mono.xml", "halgren-PG.xml", ["epot", "force", "mu"],
                  "Potential energy", 16, ("epotcorr", "forcecorr")))
        for molprop, forcefield, observables, statistic, npoint, files in cases:
            mp_id = send("molprop", molprop, "molprop")
            described = self.run_task({"task": "mp_describe", "molprop": {"file": mp_id}})
            self.assertEqual("finished", described["status"], described["error"])
            job = self.run_task({"task": "evaluate",
                                 "forcefield": {"file": send("forcefields", forcefield,
                                                             "forcefield")},
                                 "molprop": {"file": mp_id, "described": described["id"]},
                                 "observables": observables})
            self.assertEqual("finished", job["status"], job["error"])
            result = job["result"]
            # All compounds were used, for training
            self.assertEqual(len(described["result"]["molecules"]), len(result["compounds"]))
            self.assertEqual({"Train"}, {c["dataset"] for c in result["compounds"]})
            self.assertEqual(["Train"], list(result["statistics"]))
            numbers = result["statistics"]["Train"][statistic]
            self.assertEqual(npoint, numbers["N"])
            self.assertGreater(numbers["RMSD"], 0)
            self.assertEqual("kJ/mol", numbers["unit"])
            for option in files:
                self.assertIn(option, job["files"])
                data = self.client.get("/api/jobs/%s/files/%s" % (job["id"], option),
                                       headers=self.headers)
                self.assertEqual(200, data.status_code, option)
                self.assertIn('legend "Train"', data.text)
            # The dipoles are not in this file, so there is nothing to plot for them
            self.assertNotIn("dipolecorr", job["files"])
            if plots.available():
                pdf = self.client.get("/api/jobs/%s/plots/%s" % (job["id"], files[0]),
                                      headers=self.headers)
                self.assertEqual(200, pdf.status_code, pdf.text[:200])
                self.assertTrue(pdf.content.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
