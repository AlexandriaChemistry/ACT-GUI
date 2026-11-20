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
@brief Tests of what is made for, and kept of, the evaluation of a force
field with alexandria train_ff.
"""
import unittest

from act_gui import evaluation


class EvaluationTest(unittest.TestCase):
    """!
    @brief The options, the selection and the digest of the result.
    """

    def test_options(self):
        """!
        @brief Each property gives its weight and the files with the
        comparison; properties of dimers need no option for their files.
        """
        self.assertEqual({"fc_epot": 1, "epotcorr": True, "fc_polar": 1, "alphacorr": True,
                          "isopol": True, "anisopol": True},
                         evaluation.options(["epot", "polar"]))
        self.assertEqual({"fc_inter": 1, "fc_elec": 1}, evaluation.options(["inter", "elec"]))
        for wrong in ([], None, "epot", ["epot", "epot"], ["fc_epot"], ["-o"], [3],
                      ["epot", "optimize"]):
            with self.assertRaises(evaluation.EvaluationError, msg=wrong):
                evaluation.options(wrong)

    def test_every_observable_is_described(self):
        """!
        @brief The list for the browser has all properties, and the files
        that can be plotted are those of the properties and of the dimers.
        """
        described = evaluation.describe()
        self.assertEqual(list(evaluation.OBSERVABLES), [o["name"] for o in described])
        self.assertTrue(all(o["label"] for o in described))
        self.assertIn("epotcorr", evaluation.PLOTS)
        self.assertIn("INTERACTION", evaluation.PLOTS)
        self.assertEqual(len(evaluation.PLOTS), len(set(evaluation.PLOTS)))
        self.assertTrue(any(o["dimer"] for o in described))
        self.assertFalse(all(o["dimer"] for o in described))

    def test_selection(self):
        """!
        @brief All compounds are selected for training, by the name that
        train_ff looks them up with, each once.
        """
        molecules = [{"name": "water dimer", "iupac": "water#water"},
                     {"name": "methanol", "iupac": ""},
                     {"name": "x", "iupac": "water#water"},
                     {"iupac": "1-propanol#ethane"}]
        self.assertEqual("water#water|Train\nmethanol|Train\n1-propanol#ethane|Train\n",
                         evaluation.selection(molecules))

    def test_names_that_cannot_be_selected(self):
        """!
        @brief A name cannot add a line or a field to the selection, and a
        file without compounds cannot be evaluated.
        """
        for wrong in ([], None, "water", {"name": "water"},
                      [{"name": "a|Test"}], [{"name": "a\nb|Test"}], [{"name": "a\rb"}],
                      [{"iupac": "a\tb"}], [{"name": ""}], [{"name": "  "}], [{}], ["water"],
                      [{"name": 5}], [{"name": "a" * 301}], [{"name": "ok"}, {"name": "a\x00"}]):
            with self.assertRaises(evaluation.EvaluationError, msg=repr(wrong)):
                evaluation.selection(wrong)

    def test_digest(self):
        """!
        @brief The atoms and the data points stay on the server; the
        statistics, the compounds and a few outliers go to the browser.
        """
        result = {
            "molecules": {
                "0": {"name": "water#water", "dataset": "Train",
                      "atoms": {"0": {"fx": 1.0}},
                      "interaction_energies": {"c1": {"INTERACTION": {"QM": -20, "ACT": -19}},
                                               "c2": {"INTERACTION": {"QM": -5, "ACT": -6}}}},
                "1": {"name": "methanol", "dataset": "Test", "atoms": {},
                      "interaction_energies": None, "Energy terms": {"QM": 1}}},
            "statistics": {"Train": {"INTERACTION": {"N": 2, "RMSD": 1.0}}, "Test": {},
                           "Ignore": None},
            "outliers": {"INTERACTION": {str(i): {"molecule": "water#water", "QM": 1, "ACT": 2}
                                         for i in range(30)}},
            "Timings": {"Processes": "1 Master"},
        }
        kept = evaluation.digest(result)
        self.assertEqual([{"name": "water#water", "dataset": "Train", "points": 2},
                          {"name": "methanol", "dataset": "Test", "points": 0}],
                         kept["compounds"])
        self.assertEqual({"Train": {"INTERACTION": {"N": 2, "RMSD": 1.0}}}, kept["statistics"])
        self.assertEqual(20, len(kept["outliers"]["INTERACTION"]))
        self.assertEqual({"molecule": "water#water", "QM": 1, "ACT": 2, "calculation": "0"},
                         kept["outliers"]["INTERACTION"][0])
        self.assertNotIn("atoms", str(kept))
        self.assertNotIn("Timings", kept)
        # A result of another shape is passed on or gives empty lists
        self.assertIsNone(evaluation.digest(None))
        self.assertEqual({"compounds": [], "statistics": {}, "outliers": {}},
                         evaluation.digest({}))


if __name__ == "__main__":
    unittest.main()
