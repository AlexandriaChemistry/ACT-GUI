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


## What geometry_ff -analyse adds to it
ANALYSIS = {"test": "Anderson-Darling", "alpha": 0.01, "minimum": 8,
            "BONDS": {"unit": "pm", "tolerance": 8, "distributions": 2, "wide": 1, "tested": 1,
                      "not_normal": 1,
                      "entries": [
                          {"identifier": "c3_b~h_b", "count": 3, "average": 113.1, "sigma": 9.2,
                           "wide": True, "tested": False},
                          {"identifier": "c3_b~c3_b", "count": 9, "average": 153.4, "sigma": 0.6,
                           "wide": False, "tested": True, "normal": False, "statistic": 2.49,
                           "pvalue": 2.7e-06, "skewness": 0.01, "kurtosis": -1.99}]},
            "ANGLES": {"unit": "degree", "tolerance": 8, "distributions": 1, "wide": 0,
                       "tested": 0, "not_normal": 0,
                       "entries": [{"identifier": "h_b~c3_b~h_b", "count": 4, "average": 107.8,
                                    "sigma": 0.3, "wide": False, "tested": False}]}}


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
        # No analysis of the distributions without geometry_ff -analyse
        self.assertNotIn("analysis", kept)
        # From an older alexandria, without histograms
        self.assertEqual({"compounds": 1}, geometry.digest({"compounds": 1}))
        self.assertEqual("text", geometry.digest("text"))

    def test_analysis(self):
        """!
        @brief With geometry_ff -analyse the result has the numbers of each
        kind of geometry, and each type in the list of histograms has what
        was found for it.
        """
        kept = geometry.digest(dict(RESULT, analysis=ANALYSIS))
        analysis = kept["analysis"]
        self.assertEqual(("Anderson-Darling", 0.01, 8),
                         (analysis["test"], analysis["alpha"], analysis["minimum"]))
        self.assertEqual({"unit": "pm", "tolerance": 8, "distributions": 2, "wide": 1,
                          "tested": 1, "not_normal": 1}, analysis["kinds"]["BONDS"])
        self.assertEqual(["BONDS", "ANGLES"], list(analysis["kinds"]))
        # The types are with their histograms, not twice in the result
        self.assertNotIn("types", analysis)
        self.assertNotIn("entries", analysis["kinds"]["BONDS"])
        bonds = {h["identifier"]: h for h in kept["histograms"]["BONDS"]}
        self.assertEqual({"average": 153.4, "sigma": 0.6, "statistic": 2.49, "pvalue": 2.7e-06,
                          "skewness": 0.01, "kurtosis": -1.99, "wide": False, "tested": True,
                          "normal": False}, bonds["c3_b~c3_b"]["analysis"])
        self.assertEqual({"average": 113.1, "sigma": 9.2, "wide": True, "tested": False},
                         bonds["c3_b~h_b"]["analysis"])
        self.assertEqual(9, bonds["c3_b~c3_b"]["count"])
        self.assertFalse(kept["histograms"]["ANGLES"][0]["analysis"]["tested"])
        # One histogram is as before
        self.assertEqual(3, geometry.histogram(dict(RESULT, analysis=ANALYSIS), "BONDS",
                                               "c3_b~h_b")["count"])

    def test_analysis_that_is_odd(self):
        """!
        @brief An analysis that is not as expected does not stop the result:
        what cannot be used is left out.
        """
        for odd in ("text", [1], 5):
            self.assertNotIn("analysis", geometry.digest(dict(RESULT, analysis=odd)))
        odd = {"test": 5, "alpha": "x", "BONDS": {"unit": "pm", "tolerance": True, "entries": [
                   "text", {"identifier": 5}, {"identifier": "c3_b~h_b", "wide": "yes",
                                               "pvalue": "small", "tested": True},
                   {"identifier": "nosuch", "wide": True}]},
               "ANGLES": {"unit": "degree"}, "DIHEDRALS": "none"}
        kept = geometry.digest(dict(RESULT, analysis=odd))
        self.assertEqual({"test": "5", "kinds": {"BONDS": {"unit": "pm"}}}, kept["analysis"])
        bonds = {h["identifier"]: h for h in kept["histograms"]["BONDS"]}
        self.assertEqual({"tested": True}, bonds["c3_b~h_b"]["analysis"])
        self.assertNotIn("analysis", bonds["c3_b~c3_b"])
        self.assertEqual(["c3_b~c3_b", "c3_b~h_b"], list(bonds))

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
