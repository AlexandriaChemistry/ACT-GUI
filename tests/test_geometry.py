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
@brief Tests of what is kept of the histograms that geometry_ff writes.
"""
import unittest

from act_gui import geometry

## What geometry_ff writes with -json and -samples, in short
RESULT = {"compounds": 2, "extracted": {"BONDS": 2}, "forcefield": {"checksum": "x"},
          "histograms": {
              "BONDS": [
                  {"identifier": "c3_b~h_b", "unit": "pm", "binwidth": 1, "count": 3,
                   "bins": [{"value": 109.5, "count": 2, "more": 0,
                             "samples": [{"value": 109.4, "molecule": "ethane", "calculation": "0001",
                                          "atoms": [1, 3], "names": "C1-H3"},
                                         {"value": 109.6, "molecule": "ethane", "calculation": "0001",
                                          "atoms": [1, 4], "names": "C1-H4"}]},
                            {"value": 120.5, "count": 1, "more": 0,
                             "samples": [{"value": 120.2, "molecule": "odd", "calculation": "0007",
                                          "atoms": [2, 5], "names": "C2-H5"}]}]},
                  {"identifier": "c3_b~c3_b", "unit": "pm", "binwidth": 1, "count": 9,
                   "bins": [{"value": 153.5, "count": 9}]}],
              "ANGLES": [{"identifier": "h_b~c3_b~h_b", "unit": "degree", "binwidth": 0.5,
                          "count": 4, "bins": [{"value": 107.75, "count": 4}]}]}}


class GeometryTest(unittest.TestCase):
    """!
    @brief The summary of the histograms and one histogram.
    """

    def test_digest(self):
        """!
        @brief The result keeps what was there, with of the histograms only
        for each type its identifiers with their number of values, the
        most common first.
        """
        kept = geometry.digest(RESULT)
        self.assertEqual(2, kept["compounds"])
        self.assertEqual({"checksum": "x"}, kept["forcefield"])
        self.assertEqual({"BONDS": [{"identifier": "c3_b~c3_b", "count": 9, "unit": "pm"},
                                    {"identifier": "c3_b~h_b", "count": 3, "unit": "pm"}],
                          "ANGLES": [{"identifier": "h_b~c3_b~h_b", "count": 4, "unit": "degree"}]},
                         kept["histograms"])
        # From an older alexandria, without histograms
        self.assertEqual({"compounds": 1}, geometry.digest({"compounds": 1}))
        self.assertEqual("text", geometry.digest("text"))

    def test_one_histogram(self):
        """!
        @brief One histogram with its bins and where their values come from.
        """
        made = geometry.histogram(RESULT, "BONDS", "c3_b~h_b")
        self.assertEqual("c3_b~h_b", made["identifier"])
        self.assertEqual([109.5, 120.5], [b["value"] for b in made["bins"]])
        self.assertEqual("odd", made["bins"][1]["samples"][0]["molecule"])
        for itype, identifier in (("BONDS", "x"), ("DIHEDRALS", "c3_b~h_b"), ("", ""),
                                  (None, "c3_b~h_b")):
            with self.assertRaises(geometry.GeometryError, msg=(itype, identifier)):
                geometry.histogram(RESULT, itype, identifier)
        with self.assertRaises(geometry.GeometryError):
            geometry.histogram({"compounds": 1}, "BONDS", "c3_b~h_b")


if __name__ == "__main__":
    unittest.main()
