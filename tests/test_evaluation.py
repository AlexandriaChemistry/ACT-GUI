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

    def test_compounds_chosen_by_hand(self):
        """!
        @brief The user says which compounds are for testing and which are
        not used. Only names of the file are taken, and a compound is
        left to train on.
        """
        molecules = [{"name": "w2", "iupac": "water#water"}, {"name": "methanol"},
                     {"iupac": "ethanol"}, {"name": "propanol"}]
        self.assertEqual("water#water|Train\nmethanol|Test\npropanol|Train\n",
                         evaluation.selection(molecules, sets={"test": ["methanol"],
                                                               "ignore": ["ethanol"]}))
        self.assertEqual("water#water|Train\nmethanol|Train\nethanol|Train\npropanol|Train\n",
                         evaluation.selection(molecules, sets={}))
        self.assertEqual("methanol|Train\n", evaluation.selection(
            molecules, sets={"ignore": ["water#water", "ethanol", "propanol"], "test": []}))
        # A part for testing is drawn from the compounds that are used
        drawn = evaluation.selection(molecules, 0.5, sets={"ignore": ["ethanol", "propanol"]})
        self.assertEqual(["Test", "Train"], sorted(line.split("|")[1]
                                                   for line in drawn.splitlines()))
        self.assertEqual(["water#water", "methanol"],
                         [line.split("|")[0] for line in drawn.splitlines()])
        wrong = [
            {"test": ["acetone"]}, {"ignore": ["w2"]}, {"test": "methanol"},
            {"test": ["methanol"], "ignore": ["methanol"]}, {"test": ["methanol", "methanol"]},
            {"train": ["methanol"]}, {"test": [3]}, ["methanol"], "methanol",
            {"test": ["methanol|Train"]}, {"test": ["methanol\nwater|Test"]},
            # Nothing left to train on
            {"ignore": ["water#water", "methanol", "ethanol", "propanol"]},
            {"test": ["water#water", "methanol", "ethanol", "propanol"]},
            {"test": ["water#water", "methanol"], "ignore": ["ethanol", "propanol"]},
        ]
        for sets in wrong:
            with self.assertRaises(evaluation.EvaluationError, msg=repr(sets)):
                evaluation.selection(molecules, sets=sets)
        # By hand or as a part, not both
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.selection(molecules, 0.25, sets={"test": ["methanol"]})

    def test_weights(self):
        """!
        @brief A property can weigh more or less than the others in a
        training; a weight is a number above zero for a property that is
        chosen.
        """
        self.assertEqual({"fc_epot": 2.5, "epotcorr": True, "fc_force": 1, "forcecorr": True},
                         evaluation.options(["epot", "force"], {"epot": 2.5}))
        self.assertEqual({"fc_inter": 0.01, "fc_elec": 3},
                         evaluation.options(["inter", "elec"], {"inter": 0.01, "elec": 3}))
        self.assertEqual({"fc_inter": 1}, evaluation.options(["inter"], {}))
        self.assertEqual({"fc_inter": 1}, evaluation.options(["inter"], None))
        for wrong in ({"epot": 0}, {"epot": -1}, {"epot": "2"}, {"epot": True},
                      {"force": 2}, {"fc_epot": 2}, {"epot": 1e9}, {"epot": float("nan")},
                      [2], "2", {"epot": None}):
            with self.assertRaises(evaluation.EvaluationError, msg=repr(wrong)):
                evaluation.options(["epot"], wrong)

    def test_energies_of_a_compound(self):
        """!
        @brief The energies of one compound, for each term the reference
        values and those of the force field, in the order of the
        calculations. What is no number is left out, and the atoms stay
        on the server.
        """
        result = {"molecules": {
            "0": {"name": "water#water", "dataset": "Train", "atoms": {"0": {"fx": 1.0}},
                  "interaction_energies": {
                      "c1": {"INTERACTION": {"QM": -20, "ACT": -19.5}, "COULOMB": {"QM": -5, "ACT": -6}},
                      "c2": {"INTERACTION": {"QM": -5, "ACT": -6}, "COULOMB": {"QM": 1, "ACT": "x"}},
                      "c3": "nothing", "c4": {"INTERACTION": {"QM": None, "ACT": 2}}}},
            "1": {"name": "methanol", "dataset": "Test", "interaction_energies": None,
                  "Energy terms": {"unit": "kJ/mol"}}}}
        self.assertEqual({"name": "water#water", "dataset": "Train", "truncated": False,
                          "energies": {"INTERACTION": {"reference": [-20, -5],
                                                       "forcefield": [-19.5, -6]},
                                       "COULOMB": {"reference": [-5], "forcefield": [-6]}}},
                         evaluation.compound(result, 0))
        self.assertEqual({"name": "methanol", "dataset": "Test", "truncated": False,
                          "energies": {}}, evaluation.compound(result, 1))
        # The same compounds in the same order as the digest has them
        self.assertEqual([c["name"] for c in evaluation.digest(result)["compounds"]],
                         [evaluation.compound(result, i)["name"] for i in range(2)])
        for wrong in (2, -1, "0", None, 0.0, True):
            with self.assertRaises(evaluation.EvaluationError, msg=repr(wrong)):
                evaluation.compound(result, wrong)
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.compound("text", 0)
        # Of very many points the first ones are sent
        many = {"molecules": [{"name": "w", "interaction_energies": {
            "c%d" % i: {"INTERACTION": {"QM": i, "ACT": i}} for i in range(evaluation.MAX_POINTS + 5)}}]}
        sent = evaluation.compound(many, 0)
        self.assertTrue(sent["truncated"])
        self.assertEqual(evaluation.MAX_POINTS, len(sent["energies"]["INTERACTION"]["reference"]))

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
