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
@brief Tests of what is kept of the result of alexandria b2.
"""
import unittest

from act_gui import virial

## The result of b2 for two temperatures, in short
RESULT = {"Force field file": "SPCE.xml", "Second virial coefficients": {
    "Unit": "cm^3/mol",
    "T = 350 K": {"B2(Classical)": -3969.3, "B2(Force)": 131.3, "B2(Torque1)": 346.3,
                  "B2(Torque2)": 622.8, "B2(Total)": -3353.5},
    "T = 300 K": {"B2(Classical)": -16708.9, "B2(Force)": 784.6, "B2(Torque1)": 1297.2,
                  "B2(Torque2)": 3117.5, "B2(Total)": -13716.9}}}


class VirialTest(unittest.TestCase):
    """!
    @brief The coefficients by temperature.
    """

    def test_digest(self):
        """!
        @brief The temperatures from low to high, and each part as a column.
        """
        kept = virial.digest(RESULT)
        self.assertEqual("SPCE.xml", kept["Force field file"])
        table = kept["virial"]
        self.assertEqual("cm^3/mol", table["unit"])
        self.assertEqual([300.0, 350.0], table["temperatures"])
        self.assertEqual([-13716.9, -3353.5], table["parts"]["Total"])
        self.assertEqual([784.6, 131.3], table["parts"]["Force"])
        self.assertEqual(["Total", "Classical", "Force", "Torque1", "Torque2"],
                         list(table["parts"]))

    def test_without_coefficients(self):
        """!
        @brief A result without coefficients, or that is no object, stays as it is.
        """
        self.assertEqual({"x": 1}, virial.digest({"x": 1}))
        self.assertIsNone(virial.digest(None))
        odd = {"Second virial coefficients": {"Unit": "cm^3/mol", "T = x K": {},
                                              "T = 300 K": {"B2(Total)": True}}}
        self.assertEqual({"unit": "cm^3/mol", "temperatures": [300.0],
                          "parts": {"Total": [None], "Classical": [None], "Force": [None],
                                    "Torque1": [None], "Torque2": [None]}},
                         virial.digest(odd)["virial"])


if __name__ == "__main__":
    unittest.main()
