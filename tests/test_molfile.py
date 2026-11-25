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
@brief Tests of the SDF files that gentop reads for a structure of a molprop file.
"""
import unittest

from act_gui import molfile


def written(elements, bonds, fragments=()):
    """!
    @brief Write a structure with all atoms at the origin and read back
    what matters.
    @param elements  The element of each atom.
    @param bonds     The bonds: the atoms, from zero, and the order.
    @param fragments The atoms and the charge of each compound.
    @return The bond types as written and the formal charges, by atom from one.
    """
    text = molfile.sdf("x", [{"element": e, "x": 0, "y": 0, "z": 0} for e in elements],
                       bonds, fragments)
    lines = text.splitlines()
    count = int(lines[3][:3])
    types = [int(line[6:9]) for line in lines[4 + count:4 + count + len(bonds)]]
    charges = {}
    for line in lines:
        if line.startswith("M  CHG"):
            words = [int(w) for w in line[9:].split()]
            charges.update(dict(zip(words[0::2], words[1::2])))
    return types, charges


class MolfileTest(unittest.TestCase):
    """!
    @brief The bonds and the charges of the SDF file of a structure.
    """

    def test_layout(self):
        """!
        @brief The counts, the atoms with their positions in Angstrom, the
        bonds and the end, as RDKit reads a V2000 file.
        """
        text = molfile.sdf("water", [{"element": "O", "x": 0, "y": 0, "z": 0},
                                     {"element": "H", "x": 0.96, "y": 0, "z": 0},
                                     {"element": "H", "x": -0.24, "y": 0.93, "z": 0}],
                           [(0, 1, 1), (0, 2, 1)], [([0, 1, 2], 0)])
        lines = text.splitlines()
        self.assertEqual("water", lines[0])
        self.assertTrue(lines[3].startswith("  3  2  0"))
        self.assertTrue(lines[3].endswith("V2000"))
        self.assertEqual("    0.9600    0.0000    0.0000 H ", lines[5][:33])
        self.assertEqual("  1  2  1  0", lines[7])
        self.assertEqual(["M  END", "$$$$"], lines[-2:])
        self.assertNotIn("M  CHG", text)

    def test_ions(self):
        """!
        @brief An ion that is a compound by itself has the charge of the
        compound, the water next to it none.
        """
        self.assertEqual(([1, 1], {4: 1}), written(["O", "H", "H", "Na"], [(0, 1, 1), (0, 2, 1)],
                                                   [([0, 1, 2], 0), ([3], 1)]))
        self.assertEqual(([1, 1], {1: -1}), written(["Cl", "O", "H", "H"], [(1, 2, 1), (1, 3, 1)],
                                                    [([0], -1), ([1, 2, 3], 0)]))

    def test_charges_from_the_bonds(self):
        """!
        @brief An atom with more or fewer bonds than it usually has gets a
        charge: the nitrogen of an ammonium, the oxygen of a hydroxide.
        """
        self.assertEqual({1: 1}, written(["N", "H", "H", "H", "H"],
                                         [(0, k, 1) for k in range(1, 5)], [([0, 1, 2, 3, 4], 1)])[1])
        self.assertEqual({1: -1}, written(["O", "H"], [(0, 1, 1)], [([0, 1], -1)])[1])
        # Guanidinium as a molprop file has it, with one double bond
        guanidinium = ["C", "N", "N", "N"] + ["H"] * 6
        bonds = [(0, 1, 2), (0, 2, 1), (0, 3, 1), (1, 4, 1), (1, 5, 1), (2, 6, 1), (2, 7, 1),
                 (3, 8, 1), (3, 9, 1)]
        self.assertEqual({2: 1}, written(guanidinium, bonds, [(list(range(10)), 1)])[1])

    def test_conjugated_groups(self):
        """!
        @brief Bonds of order 1.5 outside a ring become single and double
        bonds such that the formal charges add up to the charge of the
        compound, on other atoms than carbon if that can be.
        """
        # Acetate: one oxygen with a double bond, the other with the charge
        acetate = ["C", "C", "O", "O", "H", "H", "H"]
        types, charges = written(acetate, [(0, 1, 1), (1, 2, 1.5), (1, 3, 1.5), (0, 4, 1),
                                           (0, 5, 1), (0, 6, 1)], [(list(range(7)), -1)])
        self.assertEqual([1, 1, 2, 1, 1, 1], types)
        self.assertEqual({3: -1}, charges)
        # Guanidinium with three bonds of order 1.5: an iminium, not a carbocation
        guanidinium = ["C", "N", "N", "N"] + ["H"] * 6
        bonds = [(0, 1, 1.5), (0, 2, 1.5), (0, 3, 1.5), (1, 4, 1), (1, 5, 1), (2, 6, 1),
                 (2, 7, 1), (3, 8, 1), (3, 9, 1)]
        types, charges = written(guanidinium, bonds, [(list(range(10)), 1)])
        self.assertEqual(1, types[:3].count(2))
        self.assertEqual(1, len(charges))
        self.assertEqual("N", guanidinium[next(iter(charges)) - 1])
        # Sulfate: two double bonds and two charged oxygens
        types, charges = written(["S", "O", "O", "O", "O"], [(0, k, 1.5) for k in range(1, 5)],
                                 [([0, 1, 2, 3, 4], -2)])
        self.assertEqual([1, 1, 2, 2], types)
        self.assertEqual({2: -1, 3: -1}, charges)

    def test_aromatic_rings(self):
        """!
        @brief Bonds of order 1.5 in a ring are written as aromatic, and a
        charge of an aromatic compound goes to the nitrogen that can have it.
        """
        benzene = ["C"] * 6 + ["H"] * 6
        bonds = [(k, (k + 1) % 6, 1.5) for k in range(6)] + [(k, 6 + k, 1) for k in range(6)]
        self.assertEqual(([4] * 6 + [1] * 6, {}), written(benzene, bonds, [(list(range(12)), 0)]))
        ring = [(0, 1, 1.5), (1, 2, 1.5), (2, 3, 1.5), (3, 4, 1.5), (4, 0, 1.5)]
        hydrogens = [(k, 5 + k, 1) for k in range(5)]
        five = ["N", "C", "N", "C", "C"] + ["H"] * 5
        # Imidazolium has its charge on a nitrogen, pyrrole has none
        self.assertEqual({1: 1}, written(five, ring + hydrogens, [(list(range(10)), 1)])[1])
        pyrrole = ["N", "C", "C", "C", "C"] + ["H"] * 5
        self.assertEqual({}, written(pyrrole, ring + hydrogens, [(list(range(10)), 0)])[1])

    def test_one_compound_without_fragments(self):
        """!
        @brief A structure without compounds is one neutral compound.
        """
        self.assertEqual(([1, 1], {}), written(["O", "H", "H"], [(0, 1, 1), (0, 2, 1)]))

    def test_wrong_structures(self):
        """!
        @brief What an SDF file cannot have is refused.
        """
        atom = {"element": "C", "x": 0, "y": 0, "z": 0}
        for atoms, bonds in (([], []), ([atom] * 1000, []), ([atom, atom], [(0, 2, 1)]),
                             ([atom, atom], [(0, 0, 1)]), ([atom, atom], [(0, 1, 1.25)]),
                             ([atom, atom], [(-1, 1, 1)])):
            with self.assertRaises(molfile.MolfileError, msg=(len(atoms), bonds)):
                molfile.sdf("x", atoms, bonds, [])

    def test_many_charges(self):
        """!
        @brief At most eight charges on a line.
        """
        ions = ["Na"] * 10
        text = molfile.sdf("salt", [{"element": e, "x": k, "y": 0, "z": 0}
                                    for k, e in enumerate(ions)], [],
                           [([k], 1) for k in range(10)])
        lines = [line for line in text.splitlines() if line.startswith("M  CHG")]
        self.assertEqual(["M  CHG  8", "M  CHG  2"], [line[:9] for line in lines])


if __name__ == "__main__":
    unittest.main()
