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
@brief Tests of the archive for simulations with OpenMM.

The last test makes the files with the real alexandria program and runs
ten steps with OpenMM, when ACT_ALEXANDRIA is set and OpenMM and the
modules of ACT-utilities can be imported.
"""
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from act_gui import openmm_package as package
from act_gui.settings import Settings

## The alexandria program, if the tests with it are to run
ALEXANDRIA = os.environ.get("ACT_ALEXANDRIA", "")


def have(module: str) -> bool:
    """!
    @brief Whether a module can be imported.
    @param module The name.
    @return True if it is installed.
    """
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def settings_of(text: str) -> dict:
    """!
    @brief Read a file with settings as act_openmm.py does.
    @param text The text of the file.
    @return The values by name, as text.
    """
    values = {}
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


class PackageTest(unittest.TestCase):
    """!
    @brief The settings and the contents of the archive.
    """

    def setUp(self):
        """!
        @brief Make stand-ins for the two files of gentop.
        """
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.pdb = self.dir / "job.pdb"
        self.pdb.write_text("ATOM      1  C1  PR1     1       0.000   0.000   0.000\n")
        self.xml = self.dir / "job.xml"
        self.xml.write_text("<ForceField/>\n")

    def tearDown(self):
        """!
        @brief Remove the files.
        """
        self._tmp.cleanup()

    def test_settings(self):
        """!
        @brief Numbers and texts with numbers are taken, defaults fill in.
        """
        chosen = package.parse_settings("2000", 0.25, "310.5")
        self.assertEqual((2000, 0.25, 310.5), (chosen.steps, chosen.dt, chosen.temperature))
        default = package.parse_settings()
        self.assertEqual(package.PackageSettings(), default)
        self.assertEqual(2, chosen.save_every)
        self.assertEqual(20, chosen.frame_every)
        self.assertEqual(1, package.parse_settings(steps=5).save_every)

    def test_settings_that_are_refused(self):
        """!
        @brief Values that are no numbers or outside the limits.
        """
        for kwargs in ({"steps": 0}, {"steps": -5}, {"steps": 1.5}, {"steps": "many"},
                       {"steps": 1e12}, {"steps": True}, {"dt": 0}, {"dt": 100},
                       {"dt": "nan"}, {"dt": "inf"}, {"temperature": -1},
                       {"temperature": 1e6}, {"temperature": "300; import os"},
                       {"temperature": [300]}):
            with self.assertRaises(package.PackageError, msg=str(kwargs)):
                package.parse_settings(**kwargs)

    def test_simulation_settings(self):
        """!
        @brief The file has what the user chose, in the units of OpenMM,
        and a molecule in vacuum.
        """
        values = settings_of(package.simulation_dat(package.parse_settings(4000, 0.5, 250)))
        self.assertEqual("4000", values["steps"])
        self.assertEqual("0.0005*picoseconds", values["dt"])
        self.assertEqual("250*kelvin", values["temperature_c"])
        self.assertEqual("NoCutoff", values["nonbondedMethod"])
        self.assertEqual("4", values["saveEnergy"])
        self.assertEqual("40", values["savePdb"])
        self.assertEqual("False", values["useMonteCarloBarostat"])
        # Everything that act_openmm.py reads without a default
        for key in ("equilibrationSteps", "nonbondedCutoff", "useAndersenThermostat",
                    "useMonteCarloAnisotropicBarostat", "rigidWater", "constraintTolerance",
                    "outStep", "outTime", "outPotentialEnergy", "outKineticEnergy",
                    "outTemperature", "outDensity", "outSeparator", "dielectricConstant",
                    "usePlatform", "temperature_s", "friction_c", "friction_s", "integrator",
                    "saveXtc", "saveChk"):
            self.assertIn(key, values)

    def test_archive(self):
        """!
        @brief The archive has the five files in one directory, the files
        of gentop unchanged, and a script that is valid Python.
        """
        chosen = package.parse_settings(1000, 1.0, 300)
        data = package.build(self.pdb, self.xml, chosen, "1.2.3")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertIsNone(archive.testzip())
            names = archive.namelist()
            self.assertEqual(sorted("act-openmm/" + n for n in (
                "conf.pdb", "openmm.xml", "simulation.dat", "run.py", "README.md")), sorted(names))
            self.assertEqual(self.pdb.read_bytes(), archive.read("act-openmm/conf.pdb"))
            self.assertEqual(self.xml.read_bytes(), archive.read("act-openmm/openmm.xml"))
            script = archive.read("act-openmm/run.py").decode()
            compile(script, "run.py", "exec")
            for name in ("conf.pdb", "openmm.xml", "simulation.dat"):
                self.assertIn('"%s"' % name, script)
            text = archive.read("act-openmm/README.md").decode()
            self.assertIn("version 1.2.3", text)
            self.assertIn("1000 steps of 1 fs at 300 K", text)
            # The script can be started
            self.assertTrue(archive.getinfo("act-openmm/run.py").external_attr >> 16 & 0o100)
        # The same input gives the same archive
        self.assertEqual(data, package.build(self.pdb, self.xml, chosen, "1.2.3"))

    def test_missing_files(self):
        """!
        @brief Without the files of gentop there is no archive.
        """
        chosen = package.parse_settings()
        with self.assertRaises(package.PackageError):
            package.build(self.dir / "nothere.pdb", self.xml, chosen, "1")
        empty = self.dir / "empty.xml"
        empty.write_text("")
        with self.assertRaises(package.PackageError):
            package.build(self.pdb, empty, chosen, "1")


@unittest.skipUnless(ALEXANDRIA and have("openmm") and have("act_openmm"),
                     "ACT_ALEXANDRIA, OpenMM or ACT-utilities is missing")
class OpenMMRunTest(unittest.TestCase):
    """!
    @brief The archive made with the real program runs with OpenMM.
    """

    def test_ten_steps(self):
        """!
        @brief Unpack the archive of acetone and run its script.
        """
        import act  # pylint: disable=import-outside-toplevel
        from act_gui.tasks import TASKS  # pylint: disable=import-outside-toplevel

        task = TASKS["openmm"]
        saved = act.config.executable
        act.config.executable = ALEXANDRIA
        try:
            with tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp) / "job"
                forcefield = Settings(data_dir=Path(tmp) / "data").default_forcefield
                options = dict(task.fixed, ff=forcefield, smiles="CC(=O)C")
                options.update({name: work / filename for name, filename in task.outputs.items()})
                result = act.run(task.command, workdir=work, **options)
                files = {name: paths[0] for name, paths in result.files.items() if paths}
                data = package.build(files["c"], files["openmm"],
                                     package.parse_settings(10, 0.5, 300), "test")
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    archive.extractall(tmp)
                run_dir = Path(tmp) / package.FOLDER
                done = subprocess.run([sys.executable, package.RUN_NAME], cwd=run_dir,
                                      capture_output=True, text=True, timeout=300, check=False)
                self.assertEqual(0, done.returncode, done.stdout + done.stderr)
                rows = [line for line in (run_dir / "energy.csv").read_text().splitlines()
                        if line and not line.startswith("#")]
                self.assertEqual(10, len(rows))
                # Step, time, potential and kinetic energy, temperature
                last = rows[-1].split(";")
                self.assertEqual("10", last[0])
                self.assertLess(float(last[2]), 0)
                self.assertTrue((run_dir / "final.pdb").read_text().startswith(("REMARK", "CRYST1",
                                                                              "HETATM", "ATOM")))
                self.assertIn("@", (run_dir / "energy.xvg").read_text())
        finally:
            act.config.executable = saved


if __name__ == "__main__":
    unittest.main()
