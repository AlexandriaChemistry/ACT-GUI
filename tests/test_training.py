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
@brief Tests of what is made for, and kept of, the training of a force
field with alexandria train_ff.
"""
import unittest

from act_gui import evaluation, training


class TrainingTest(unittest.TestCase):
    """!
    @brief The options, the processes, the compounds for testing and the
    digest of the result.
    """

    def test_options(self):
        """!
        @brief The kinds of parameters become the option fit; what is not
        a name of a parameter is refused, since it goes on a command line.
        """
        self.assertEqual({"fit": "sigma epsilon"},
                         training.options({"fit": ["sigma", "epsilon"]}))
        self.assertEqual({"fit": "vs2a"}, training.options({"fit": ["vs2a"], "test": 0.2}))
        for wrong in (None, [], "sigma", {}, {"fit": []}, {"fit": "sigma"}, {"fit": None},
                      {"fit": ["sigma", "sigma"]}, {"fit": ["-o"]}, {"fit": ["sigma epsilon"]},
                      {"fit": ["sigma;ls"]}, {"fit": [""]}, {"fit": [3]}, {"fit": ["a" * 41]},
                      {"fit": ["p%d" % i for i in range(training.MAX_PARAMETERS + 1)]}):
            with self.assertRaises(training.TrainingError, msg=repr(wrong)):
                training.options(wrong)

    def test_processes(self):
        """!
        @brief One individual for each process: within the limit, and two
        at least for the genetic algorithm.
        """
        self.assertEqual(1, training.processes(None, None, 8))
        self.assertEqual(1, training.processes(1, "MCMC", 8))
        self.assertEqual(8, training.processes(8, "MCMC", 8))
        self.assertEqual(2, training.processes(2, "GA", 8))
        self.assertEqual(4, training.processes(4, "HYBRID", 4))
        for value, optimizer in ((0, "MCMC"), (9, "MCMC"), (-1, None), (2.0, "MCMC"),
                                 ("2", "MCMC"), (True, "MCMC"), (1, "GA"), (1, "HYBRID"),
                                 (None, "GA"), (2, "ga"), (2, "Newton"), (2, 3)):
            with self.assertRaises(training.TrainingError, msg=repr((value, optimizer))):
                training.processes(value, optimizer, 8)

    def test_part_for_testing(self):
        """!
        @brief The part of the compounds for testing is a number up to a
        half, and none without one.
        """
        self.assertEqual(0.0, training.test_fraction(None))
        self.assertEqual(0.0, training.test_fraction({"fit": ["sigma"]}))
        self.assertEqual(0.25, training.test_fraction({"test": 0.25}))
        self.assertEqual(0.0, training.test_fraction({"test": 0}))
        for wrong in (-0.1, 0.51, 1, "0.2", True, [0.2]):
            with self.assertRaises(training.TrainingError, msg=repr(wrong)):
                training.test_fraction({"test": wrong})

    def test_compounds_for_testing(self):
        """!
        @brief A part of the compounds is selected for testing, the same
        ones each time, and there is always a compound to train on.
        """
        molecules = [{"name": "compound%d" % i} for i in range(20)]
        text = evaluation.selection(molecules, 0.25)
        lines = text.splitlines()
        self.assertEqual(["compound%d" % i for i in range(20)],
                         [line.split("|")[0] for line in lines])
        self.assertEqual(5, sum(1 for line in lines if line.endswith("|Test")))
        self.assertEqual(15, sum(1 for line in lines if line.endswith("|Train")))
        self.assertEqual(text, evaluation.selection(molecules, 0.25))
        # Not simply the first or the last ones
        tests = [i for i, line in enumerate(lines) if line.endswith("|Test")]
        self.assertNotEqual(list(range(5)), tests)
        self.assertNotEqual(list(range(15, 20)), tests)
        # Without a part, and with one compound, all are for training
        self.assertNotIn("Test", evaluation.selection(molecules))
        self.assertEqual("one|Train\n", evaluation.selection([{"name": "one"}], 0.5))
        # A small part of a few compounds is one compound, a half of two as well
        self.assertEqual(1, evaluation.selection(molecules[:3], 0.01).count("|Test"))
        self.assertEqual(1, evaluation.selection(molecules[:2], 0.5).count("|Test"))
        self.assertEqual(1, evaluation.selection(molecules[:2], 0.5).count("|Train"))

    def test_digest(self):
        """!
        @brief The parameters in their order and the numbers of the
        sensitivity analysis go to the browser, with what an evaluation
        gives; the atoms stay on the server.
        """
        result = {
            "parameters": {
                "10": {"class": "sigma", "name": "c3", "best": 0.3, "min": 0.2, "max": 0.4,
                       "mean": 0.31, "stdev": 0.02, "median": 0.3, "more": 1},
                "2": {"class": "epsilon", "name": "c3", "best": 2.6},
                "0": {"class": "epsilon", "name": "h1", "best": 0.1, "min": 0.1, "max": 0.1,
                      "mean": 0.1, "stdev": 0.0, "median": 0.1}},
            "sensitivity": {"mode": "1D", "minimum": "no", "n_parameters": 3,
                            "n_parameters_converged": 2, "parameters": [{"name": "x"}],
                            "grid": {"a": 1}},
            "molecules": {"0": {"name": "water#water", "dataset": "Test",
                                "atoms": {"0": {"fx": 1.0}},
                                "interaction_energies": {"c1": {}}}},
            "statistics": {"Train": {"INTERACTION": {"N": 2, "RMSD": 1.0}},
                           "Test": {"INTERACTION": {"N": 1, "RMSD": 2.0}}},
            "outliers": {},
        }
        kept = training.digest(result)
        self.assertEqual([("epsilon", "h1"), ("epsilon", "c3"), ("sigma", "c3")],
                         [(p["class"], p["name"]) for p in kept["parameters"]])
        self.assertEqual({"class": "sigma", "name": "c3", "best": 0.3, "min": 0.2, "max": 0.4,
                          "mean": 0.31, "stdev": 0.02, "median": 0.3}, kept["parameters"][2])
        self.assertIsNone(kept["parameters"][1]["stdev"])
        self.assertEqual({"mode": "1D", "minimum": "no", "n_parameters": 3,
                          "n_parameters_converged": 2}, kept["sensitivity"])
        self.assertEqual([{"name": "water#water", "dataset": "Test", "points": 1}],
                         kept["compounds"])
        self.assertEqual(["Train", "Test"], list(kept["statistics"]))
        self.assertNotIn("molecules", kept)
        # A result without them, and something that is no result
        self.assertEqual(([], {}), (training.digest({})["parameters"],
                                    training.digest({})["sensitivity"]))
        self.assertEqual("text", training.digest("text"))

    def test_described_for_the_page(self):
        """!
        @brief The page learns the optimizers and which need two processes.
        """
        described = training.describe()
        self.assertEqual(["MCMC", "HYBRID", "GA"], [o["name"] for o in described["optimizers"]])
        self.assertEqual([False, True, True], [o["parallel"] for o in described["optimizers"]])
        self.assertEqual("MCMC", described["default_optimizer"])
        self.assertEqual(0.5, described["max_test"])


if __name__ == "__main__":
    unittest.main()
