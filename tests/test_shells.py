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
@brief Tests of the particles of a molecule with relaxed shells.
"""
import copy
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from act_gui import shells


def chloride_water():
    """!
    @brief A chloride with a water as gentop describes it: with the second
    charge of the chloride as a virtual site on it, and the shells on their atoms.
    @return The compound.
    """
    particles = [
        {"name": "O", "type": "ow", "particletype": "Atom", "charge": 4.7},
        {"name": "Os", "type": "ow_s", "particletype": "Shell", "charge": -4.9, "parents": [0]},
        {"name": "H", "type": "hw", "particletype": "Atom", "charge": 0.7},
        {"name": "H", "type": "hw", "particletype": "Atom", "charge": 0.7},
        {"name": "v3bw", "type": "v3bw", "particletype": "VSite", "charge": -1.2,
         "parents": [0, 2, 3]},
        {"name": "Cl", "type": "Cl-", "particletype": "Atom", "charge": 16.5},
        {"name": "Cls", "type": "Cl-_s", "particletype": "Shell", "charge": -9, "parents": [5]},
        {"name": "v1Cl-", "type": "v1Cl-", "particletype": "VSite", "charge": -8.5,
         "parents": [5]}]
    positions = [[-0.028, 0.0004, -0.3669], [-0.028, 0.0004, -0.3669], [0.0294, -0.0768, -0.3682],
                 [0.0317, 0.0759, -0.3658], [0.01, -0.0002, -0.367], [0.011, -0.0002, 0.189],
                 [0.011, -0.0002, 0.189], [0.011, -0.0002, 0.189]]
    return {"molecule": {"name": "x", "particles": particles,
                         "bonds": [{"atoms": [2, 0], "order": 1}, {"atoms": [0, 3], "order": 1}]},
            "coordinates": {"unit": "nm", "positions": positions}}


## The same with relaxed shells, as simulate -sp -ws writes it: without the
## virtual site of the second charge, the shell of the oxygen 0.01 Angstrom away
RELAXED = """TITLE      processed by ACT - The Alexandria Chemistry Toolkit
CRYST1   20.000   20.000   20.000  90.00  90.00  90.00 P 1           1
MODEL        1
ATOM      1  O1          1      -0.280   0.004  -3.669  1.00  0.00           O
ATOM      2  Os1         1      -0.280   0.004  -3.679  1.00  0.00          EP
ATOM      3  H2          1       0.294  -0.768  -3.682  1.00  0.00           H
ATOM      4  H3          1       0.317   0.759  -3.658  1.00  0.00           H
ATOM      5 v3bw         1       0.100  -0.002  -3.670  1.00  0.00          v3bw
ATOM      6 Cl1          2       0.110  -0.002   1.890  1.00  0.00          Cl1-
ATOM      7 Cls1         2       0.110  -0.002   1.890  1.00  0.00          EP
CONECT    3    1
ENDMDL
MODEL        2
ATOM      1  O1          1       9.000   9.000   9.000  1.00  0.00           O
ENDMDL
"""


class FakePrograms:
    """!
    @brief Stands in for act.run(): gentop describes the compound, simulate
    writes the structure with the relaxed shells.
    """

    def __init__(self, compound, relaxed=RELAXED, fail=None):
        """!
        @brief Say what the programs give.
        @param compound What gentop describes, None for a result without compounds.
        @param relaxed  The file that simulate writes.
        @param fail     The error of simulate, None if it works.
        """
        self.compound, self.relaxed, self.fail = compound, relaxed, fail
        self.calls = []

    def __call__(self, command, *, workdir, timeout=None, stop=None, **options):
        """!
        @brief Pretend to run a command.
        @param command The command.
        @param workdir The directory it runs in.
        @param timeout The time limit.
        @param stop    The event that stops it.
        @param options The options.
        @return An object with data and files, as act.Result has.
        """
        self.calls.append((command, Path(workdir), options))
        if command == "simulate":
            if self.fail is not None:
                raise self.fail
            Path(options["c"]).write_text(self.relaxed)
            return SimpleNamespace(data={}, files={})
        data = {"molecules": [] if self.compound is None else [self.compound, {"other": True}]}
        return SimpleNamespace(data=data, files={"c": [Path(workdir) / "molecule.pdb"]})


class ShellsTest(unittest.TestCase):
    """!
    @brief The second charges back on their atoms, and the shells where a
    single point calculation puts them.
    """

    def test_second_charges_back_on_their_atoms(self):
        """!
        @brief The virtual site that gentop made of a second charge is
        taken out and its charge is the second charge of its atom, as
        simulate, minimize and nma have it.
        """
        given = chloride_water()
        before = copy.deepcopy(given)
        made = shells.fold(given)
        self.assertEqual(before, given)
        particles = made["molecule"]["particles"]
        self.assertEqual(["O", "Os", "H", "H", "v3bw", "Cl", "Cls"], [p["name"] for p in particles])
        self.assertEqual(-8.5, particles[5]["charge_vs"])
        self.assertEqual(16.5, particles[5]["charge"])
        self.assertEqual([5], particles[6]["parents"])
        self.assertEqual(7, len(made["coordinates"]["positions"]))
        self.assertEqual(given["molecule"]["bonds"], made["molecule"]["bonds"])
        # A site in the middle: the particles after it and the bonds are counted anew
        moved = chloride_water()
        last = moved["molecule"]["particles"]
        last.insert(0, last.pop())
        moved["coordinates"]["positions"].insert(0, moved["coordinates"]["positions"].pop())
        for particle in last:
            if "parents" in particle:
                particle["parents"] = [parent + 1 for parent in particle["parents"]]
        moved["molecule"]["bonds"] = [{"atoms": [3, 1], "order": 1}, {"atoms": [1, 4], "order": 1}]
        self.assertEqual(made["molecule"], shells.fold(moved)["molecule"])
        # With a number, when the name was taken
        numbered = chloride_water()
        numbered["molecule"]["particles"][7]["type"] = "v1Cl-_2"
        self.assertEqual(7, len(shells.fold(numbered)["molecule"]["particles"]))

    def test_what_is_no_second_charge(self):
        """!
        @brief Other virtual sites stay: one of several atoms, one with
        another name, one on a shell or on an atom that has a second charge.
        """
        for change in ({"type": "v1Na+"}, {"type": "v1Cl-x"}, {"parents": [5, 0]}, {"parents": [6]},
                       {"parents": [9]}, {"parents": [True]}, {"particletype": "Shell"}):
            given = chloride_water()
            given["molecule"]["particles"][7].update(change)
            self.assertIs(given, shells.fold(given), change)
        given = chloride_water()
        given["molecule"]["particles"][5]["charge_vs"] = -1
        self.assertIs(given, shells.fold(given))
        # What is not a compound of gentop is passed on
        for other in (None, [], {"molecule": 3}, {"molecule": {"particles": [{}]}},
                      {"molecule": {"particles": [{}]}, "coordinates": {"positions": []}}):
            self.assertIs(other, shells.fold(other))

    def test_positions_of_a_pdb_file(self):
        """!
        @brief The positions of the first structure, in nm.
        """
        positions = shells.pdb_positions(RELAXED)
        self.assertEqual(7, len(positions))
        self.assertAlmostEqual(-0.3679, positions[1][2])
        self.assertAlmostEqual(0.189, positions[6][2])
        self.assertEqual([], shells.pdb_positions("TITLE nothing\n"))
        with self.assertRaises(shells.ShellError):
            shells.pdb_positions("ATOM      1  O1          1      -0.280   0.004    wrong\n")

    def test_relaxed_positions(self):
        """!
        @brief The shells get the positions of the structure, if it has the
        same particles and its atoms are where they were.
        """
        folded = shells.fold(chloride_water())
        made = shells.relaxed(folded, RELAXED)
        self.assertAlmostEqual(-0.3679, made["coordinates"]["positions"][1][2])
        self.assertAlmostEqual(-0.3669, folded["coordinates"]["positions"][1][2])
        self.assertEqual("nm", made["coordinates"]["unit"])
        # Not folded: one particle more than the structure has
        with self.assertRaisesRegex(shells.ShellError, "7 particles, the molecule has 8"):
            shells.relaxed(chloride_water(), RELAXED)
        # An atom somewhere else
        with self.assertRaisesRegex(shells.ShellError, "somewhere else"):
            shells.relaxed(folded, RELAXED.replace("   0.294  -0.768", "   0.394  -0.768"))

    def run_particles(self, programs, **options):
        """!
        @brief Describe a molecule with the fake programs.
        @param programs The programs.
        @param options  The options of gentop.
        @return The result, and the directory of the job.
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        result = shells.run_particles(programs, "gentop", workdir=tmp.name, timeout=7,
                                      stop=threading.Event(), **options)
        return result, Path(tmp.name)

    def test_gentop_and_a_single_point(self):
        """!
        @brief gentop describes the molecule, simulate -sp -ws relaxes its
        shells from the same structure, with the same force field and charges.
        """
        programs = FakePrograms(chloride_water())
        result, workdir = self.run_particles(programs, ff=Path("ff.xml"), f=Path("mol.sdf"),
                                             qalg="SQE", json=True, c=Path("out.pdb"))
        self.assertEqual(["gentop", "simulate"], [call[0] for call in programs.calls])
        self.assertEqual({"ff": Path("ff.xml"), "f": Path("mol.sdf"), "qalg": "SQE", "json": True,
                          "c": Path("out.pdb")}, programs.calls[0][2])
        self.assertEqual(workdir, programs.calls[0][1])
        self.assertEqual({"ff": Path("ff.xml"), "f": Path("mol.sdf"), "qalg": "SQE", "sp": True,
                          "ws": True, "c": workdir / "relax" / "relaxed.pdb"}, programs.calls[1][2])
        self.assertEqual(workdir / "relax", programs.calls[1][1])
        first, second = result.data["molecules"]
        self.assertEqual({"present": True, "relaxed": True}, first["shells"])
        self.assertEqual(7, len(first["molecule"]["particles"]))
        self.assertAlmostEqual(-0.3679, first["coordinates"]["positions"][1][2])
        self.assertEqual({"other": True}, second)
        self.assertEqual({"c": [workdir / "molecule.pdb"]}, result.files)
        # Of a SMILES string each program makes its own coordinates: the structure of gentop
        programs = FakePrograms(chloride_water())
        self.run_particles(programs, ff=Path("ff.xml"), smiles="O.[Cl-]", c=Path("out.pdb"))
        self.assertEqual(Path("out.pdb"), programs.calls[1][2]["f"])
        self.assertNotIn("smiles", programs.calls[1][2])

    def test_without_shells_or_without_relaxation(self):
        """!
        @brief A molecule without shells needs no single point, and one of
        which the shells cannot be relaxed is described all the same, with
        the reason.
        """
        plain = chloride_water()
        for particle in plain["molecule"]["particles"]:
            if particle["particletype"] == "Shell":
                particle["particletype"] = "VSite"
        programs = FakePrograms(plain)
        result, _ = self.run_particles(programs, ff=Path("ff.xml"), f=Path("mol.sdf"))
        self.assertEqual(["gentop"], [call[0] for call in programs.calls])
        self.assertEqual({"present": False, "relaxed": False}, result.data["molecules"][0]["shells"])
        self.assertEqual(7, len(result.data["molecules"][0]["molecule"]["particles"]))
        # simulate fails
        programs = FakePrograms(chloride_water(), fail=RuntimeError("no parameters"))
        result, _ = self.run_particles(programs, ff=Path("ff.xml"), f=Path("mol.sdf"))
        first = result.data["molecules"][0]
        self.assertEqual({"present": True, "relaxed": False, "reason": "no parameters"},
                         first["shells"])
        self.assertAlmostEqual(-0.3669, first["coordinates"]["positions"][1][2])
        # Another structure than that of gentop
        programs = FakePrograms(chloride_water(), relaxed=RELAXED.replace("ATOM      7", "REMARK"))
        result, _ = self.run_particles(programs, ff=Path("ff.xml"), f=Path("mol.sdf"))
        self.assertIn("6 particles", result.data["molecules"][0]["shells"]["reason"])
        # No structure to give to simulate
        programs = FakePrograms(chloride_water())
        result, _ = self.run_particles(programs, ff=Path("ff.xml"), smiles="O")
        self.assertEqual(["gentop"], [call[0] for call in programs.calls])
        self.assertFalse(result.data["molecules"][0]["shells"]["relaxed"])
        # A result without compounds is passed on
        programs = FakePrograms(None)
        result, _ = self.run_particles(programs, ff=Path("ff.xml"), f=Path("mol.sdf"))
        self.assertEqual({"molecules": []}, result.data)

    def test_stopped(self):
        """!
        @brief A job that is stopped during the single point ends as stopped.
        """
        stop = threading.Event()

        class Cancelled(RuntimeError):
            """Stands in for the error of a run that was stopped"""

        def programs(command, *, workdir, timeout=None, stop=None, **options):
            if command == "simulate":
                stop.set()
                raise Cancelled("stopped")
            return SimpleNamespace(data={"molecules": [chloride_water()]}, files={})
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Cancelled):
                shells.run_particles(programs, "gentop", workdir=tmp, stop=stop, f=Path("m.sdf"))


if __name__ == "__main__":
    unittest.main()
