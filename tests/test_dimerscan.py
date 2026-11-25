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
@brief Tests of the scan of the distance between the two compounds of a
dimer, as the script dimer_scan of ACT-utilities does it.
"""
import math
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from act_gui import dimerscan

## A water dimer as gentop writes it: the atoms, a residue for each compound
DIMER = """ATOM      1  O1  OXI     1      -0.067   0.000   1.494  1.00  0.00           O
ATOM      2  H2  OXI     1       0.816   0.000   1.866  1.00  0.00           H
ATOM      3  H3  OXI     1       0.069   0.000   0.539  1.00  0.00           H
ATOM      4  O1  OXI     2       0.063   0.000  -1.423  1.00  0.00           O
ATOM      5  H2  OXI     2      -0.407  -0.760  -1.772  1.00  0.00           H
ATOM      6  H3  OXI     2      -0.407   0.760  -1.772  1.00  0.00           H
END
"""


def position(text, serial):
    """!
    @brief The position of an atom in a pdb file.
    @param text   The file.
    @param serial  The number of the atom.
    @return x, y and z in Angstrom.
    """
    for line in text.splitlines():
        if line.startswith(("ATOM", "HETATM")) and int(line[6:11]) == serial:
            return tuple(float(line[c:c + 8]) for c in (30, 38, 46))
    raise KeyError(serial)


class FakeAlexandria:
    """!
    @brief Stands in for act.run: gentop writes the dimer, minimize gives
    an interaction energy that depends on the distance and writes the
    structure it got.
    """

    def __init__(self, dimer=DIMER, fail_at=None):
        self.calls = []
        self.dimer = dimer
        self.fail_at = fail_at

    def __call__(self, command, *, workdir, timeout=None, stop=None, **options):
        self.calls.append((command, dict(options)))
        Path(workdir).mkdir(parents=True, exist_ok=True)
        if command == "gentop":
            Path(options["c"]).write_text(self.dimer)
            return SimpleNamespace(data={}, files={"c": [Path(options["c"])]})
        text = Path(options["f"]).read_text()
        r = math.dist(position(text, 1), position(text, 4)) / 10
        if self.fail_at is not None and abs(r - self.fail_at) < 1e-3:
            raise RuntimeError("alexandria minimize failed with exit status 1")
        Path(options["c"]).write_text(text)
        energy = 4 * ((0.3 / r) ** 12 - (0.3 / r) ** 6)
        return SimpleNamespace(
            data={"status": "Converged", "Interaction energies": {
                "EPOT": {"value": energy, "unit": "kJ/mol"},
                "COULOMB": {"value": energy / 2, "unit": "kJ/mol"}},
                  "Energies after": {"EPOT": {"value": energy - 100, "unit": "kJ/mol"}}},
            files={"c": [Path(options["c"])]})


class DimerScanTest(unittest.TestCase):
    """!
    @brief The settings, the structures and the scan itself.
    """

    def test_settings(self):
        """!
        @brief The atoms, the distances in nm and whether bonds are
        constrained, with limits.
        """
        made = dimerscan.settings({"atom1": 1, "atom2": 4, "rmin": 0.25, "rmax": 0.3,
                                   "step": 0.01, "constrain": True})
        self.assertEqual({"atom1": 1, "atom2": 4, "rmin": 0.25, "rmax": 0.3, "step": 0.01,
                          "constrain": True}, made)
        self.assertEqual(6, len(dimerscan.distances(made)))
        self.assertAlmostEqual(0.3, dimerscan.distances(made)[-1])
        self.assertFalse(dimerscan.settings({"atom1": 1, "atom2": 4})["constrain"])
        for wrong in (None, [], {"atom1": 1}, {"atom1": 0, "atom2": 4},
                      {"atom1": 1, "atom2": 1}, {"atom1": "1", "atom2": 4},
                      {"atom1": 1, "atom2": 4, "rmin": 0.5, "rmax": 0.4},
                      {"atom1": 1, "atom2": 4, "step": 0},
                      {"atom1": 1, "atom2": 4, "rmin": 0.01},
                      {"atom1": 1, "atom2": 4, "rmin": 0.2, "rmax": 2.9, "step": 0.001},
                      {"atom1": 1, "atom2": 4, "constrain": "yes"},
                      {"atom1": 1, "atom2": 4, "other": 1}):
            with self.subTest(wrong=wrong):
                with self.assertRaises(dimerscan.DimerScanError):
                    dimerscan.settings(wrong)

    def test_placed(self):
        """!
        @brief The second compound moves along the line between the two
        atoms until they are at the distance; the first stays.
        """
        atoms = dimerscan.read_pdb(DIMER)
        self.assertEqual([1, 1, 1, 2, 2, 2], [a["residue"] for a in atoms])
        text = dimerscan.placed(atoms, 1, 4, 0.35)
        self.assertAlmostEqual(3.5, math.dist(position(text, 1), position(text, 4)), places=2)
        self.assertEqual(position(DIMER, 2), position(text, 2))
        # The second compound keeps its shape
        self.assertAlmostEqual(math.dist(position(DIMER, 4), position(DIMER, 5)),
                               math.dist(position(text, 4), position(text, 5)), places=2)
        for atom1, atom2, said in ((4, 1, "first compound"), (1, 2, "second compound"),
                                   (1, 9, "no atom 9")):
            with self.assertRaises(dimerscan.DimerScanError) as caught:
                dimerscan.placed(atoms, atom1, atom2, 0.3)
            self.assertIn(said, str(caught.exception))

    def test_scan(self):
        """!
        @brief For each distance a minimization with the two atoms frozen,
        and the interaction energy of the structure it found; each point is
        reported when it is done, a point that fails is told, and the
        energies are written in an xvg file.
        """
        alexandria = FakeAlexandria(fail_at=0.27)
        reported = []
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            scan = dimerscan.settings({"atom1": 1, "atom2": 4, "rmin": 0.25, "rmax": 0.3,
                                       "step": 0.01, "constrain": True})
            result = dimerscan.run_scan(alexandria, "minimize", workdir=workdir, scan=scan,
                                        on_progress=reported.append, ff=Path("ff.xml"),
                                        smiles="O.O", qalg="SQE")
            self.assertEqual(("gentop", {"ff": Path("ff.xml"), "smiles": "O.O", "qalg": "SQE",
                                         "c": workdir / "start" / "dimer.pdb"}),
                             alexandria.calls[0])
            command, options = alexandria.calls[1]
            self.assertEqual("minimize", command)
            self.assertEqual("bonds", options["constrain"])
            self.assertEqual("[ freeze ]\n1 4\n", Path(options["freeze"]).read_text())
            self.assertTrue(options["json"])
            # With the shells and virtual sites in the structure, for the viewer
            self.assertIs(True, options["ws"])
            self.assertEqual("SQE", options["qalg"])
            points = result.data["points"]
            self.assertEqual(6, len(points))
            self.assertEqual(points, reported)
            self.assertEqual([0.25, 0.26, 0.27, 0.28, 0.29, 0.3],
                             [round(p["distance"], 6) for p in points])
            failed = points[2]
            self.assertIn("failed", failed["error"])
            self.assertNotIn("energies", failed)
            first = points[0]
            self.assertEqual({"EPOT", "COULOMB"}, set(first["energies"]))
            # From the structure that was minimized, of which the coordinates have three decimals
            self.assertAlmostEqual(4 * ((0.3 / 0.25) ** 12 - (0.3 / 0.25) ** 6),
                                   first["energies"]["EPOT"], delta=0.2)
            self.assertAlmostEqual(first["energies"]["EPOT"] - 100, first["epot"], places=6)
            self.assertEqual([1, 4], result.data["atoms"])
            # The structures, to show one of them
            self.assertEqual(workdir / "0001" / "final.pdb", dimerscan.structure(workdir, 1))
            with self.assertRaises(dimerscan.DimerScanError):
                dimerscan.structure(workdir, 2)
            with self.assertRaises(dimerscan.DimerScanError):
                dimerscan.structure(workdir, 99)
            xvg = result.files["xvg"][0].read_text()
            self.assertIn('@ s0 legend "EPOT"', xvg)
            self.assertEqual(5, len([line for line in xvg.splitlines()
                                     if line and line[0] not in "#@"]))

    def test_scan_needs_two_compounds(self):
        """!
        @brief A structure with one or three compounds cannot be scanned.
        """
        lines = DIMER.splitlines()
        one = "\n".join(lines[:3] + ["END"]) + "\n"
        three = "\n".join(lines[:6] + [
            "ATOM      7  O1  OXI     3       5.000   0.000   0.000  1.00  0.00           O",
            "END"]) + "\n"
        scan = dimerscan.settings({"atom1": 1, "atom2": 4})
        for text in (one, three):
            with tempfile.TemporaryDirectory() as tmp, self.subTest(text=text[-40:]):
                with self.assertRaises(dimerscan.DimerScanError) as caught:
                    dimerscan.run_scan(FakeAlexandria(text), "minimize", workdir=Path(tmp),
                                       scan=scan, ff=Path("ff.xml"), smiles="O.O")
                self.assertIn("two compounds", str(caught.exception))

    def test_scan_stops(self):
        """!
        @brief A scan that is stopped stops between two distances.
        """
        stop = threading.Event()
        alexandria = FakeAlexandria()
        with tempfile.TemporaryDirectory() as tmp:
            def progress(point):
                stop.set()
            with self.assertRaises(RuntimeError) as caught:
                dimerscan.run_scan(alexandria, "minimize", workdir=Path(tmp), stop=stop,
                                   scan=dimerscan.settings({"atom1": 1, "atom2": 4}),
                                   on_progress=progress, ff=Path("ff.xml"), smiles="O.O")
            self.assertEqual("ActCancelledError", type(caught.exception).__name__)
            # gentop and one minimization
            self.assertEqual(2, len(alexandria.calls))


if __name__ == "__main__":
    unittest.main()
