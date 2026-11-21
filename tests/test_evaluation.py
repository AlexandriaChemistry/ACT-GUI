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
import tempfile
import unittest
from pathlib import Path

from act_gui import evaluation

## A molprop file with a dimer of which two calculations have atoms
MOLPROP = """<?xml version="1.0" encoding="ISO-8859-1"?>
<!DOCTYPE molprops.dtd PUBLIC "molprops.dtd" "molprops.dtd">
<molecules>
  <molecule molname="water">
    <experiment datafile="0001"><atom name="O" obtype="ow" coord_unit="Angstrom">
      <x>0</x><y>0</y><z>0</z></atom></experiment>
  </molecule>
  <molecule molname="w2">
    <fragments>
      <fragment identifier="InChI=1S/H2O/h1H2" formula="H2O"> 1 2 3</fragment>
      <fragment identifier="InChI=1S/H2O/h1H2" formula="H2O"> 4 5 6</fragment>
    </fragments>
    <molinfo iupac="water#water" cas="" cid="" inchi=""/>
    <experiment datafile="0007" method="sapt2+">
      <atom name="O" obtype="ow" coord_unit="Angstrom"><x>0.0</x><y>0.0</y><z>0.0</z></atom>
      <atom name="H" obtype="hw" coord_unit="Angstrom"><x>0.96</x><y>0.0</y><z>0.0</z></atom>
      <atom name="H" obtype="hw" coord_unit="Angstrom"><x>-0.24</x><y>0.93</y><z>0.0</z></atom>
      <atom name="O" obtype="ow" coord_unit="nm"><x>0.3</x><y>0.0</y><z>0.0</z></atom>
      <atom name="H" obtype="hw" coord_unit="pm"><x>396</x><y>0</y><z>0</z></atom>
      <atom name="Cl" obtype="cl" coord_unit="Angstrom"><x>3.0</x><y>1.0</y><z>0</z></atom>
    </experiment>
    <experiment datafile="0008"><atom name="O" obtype="ow" coord_unit="Angstrom">
      <x>1</x><y>2</y><z>3</z></atom></experiment>
  </molecule>
</molecules>
"""

## The result of train_ff for a comparison of dimers and a compound
RESULT = {"molecules": {
    "0": {"name": "w2", "dataset": "Train", "atoms": {"0": {"fx": 1.0}},
          "interaction_energies": {
              "calculation-0007": {"INTERACTION": {"QM": -20, "ACT": -19.5},
                                   "COULOMB": {"QM": -30, "ACT": -28}},
              "calculation-0008": {"INTERACTION": {"QM": -5, "ACT": -6},
                                   "COULOMB": {"QM": 1, "ACT": "x"}}}},
    "1": {"name": "methanol", "dataset": "Test", "interaction_energies": None},
    "2": {"name": "methanol#water", "dataset": "Test",
          "interaction_energies": {"calculation-0100": {"INTERACTION": {"QM": -9, "ACT": -8}}}}}}


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

    def test_what_each_observable_is_compared_on(self):
        """!
        @brief Each property names the statistics and the plots of train_ff
        that are about it, such that the page shows only those of the
        properties that were chosen.
        """
        described = {o["name"]: o for o in evaluation.describe()}
        self.assertEqual(["DISPERSION"], described["disp"]["statistics"])
        self.assertEqual(["DISPERSION"], described["disp"]["plots"])
        self.assertEqual(["INTERACTION"], described["inter"]["plots"])
        self.assertEqual(["COULOMB"], described["elec"]["statistics"])
        self.assertEqual(["EXCHDISP"], described["vdw"]["plots"])
        self.assertEqual(["Potential energy"], described["epot"]["statistics"])
        self.assertEqual(["epotcorr"], described["epot"]["plots"])
        self.assertEqual(["Polariz. components", "Isotropic Polariz.", "Anisotropic Polariz."],
                         described["polar"]["statistics"])
        for observable in described.values():
            self.assertTrue(observable["statistics"], observable["name"])
            self.assertTrue(all(plotted in evaluation.PLOTS for plotted in observable["plots"]))
        # Every file that can be plotted belongs to a property
        self.assertEqual(set(evaluation.PLOTS),
                         {p for o in described.values() for p in o["plots"]})

    def test_points_of_an_energy_term(self):
        """!
        @brief The points of a term of the interaction energy, each with
        its compound, calculation and data set, such that a click on a
        point can show the structure.
        """
        points = evaluation.points(RESULT, "INTERACTION")
        self.assertEqual({"term": "INTERACTION", "truncated": False,
                          "reference": [-20, -5, -9], "forcefield": [-19.5, -6, -8],
                          "dataset": ["Train", "Train", "Test"], "compound": [0, 0, 2],
                          "calculation": ["calculation-0007", "calculation-0008",
                                          "calculation-0100"]}, points)
        # What is no number is left out
        self.assertEqual([-30], evaluation.points(RESULT, "COULOMB")["reference"])
        self.assertEqual([], evaluation.points(RESULT, "DISPERSION")["reference"])
        for wrong in ("epotcorr", "", None, "INTERACTION.xvg", 3):
            with self.assertRaises(evaluation.EvaluationError, msg=repr(wrong)):
                evaluation.points(RESULT, wrong)

    def test_one_calculation(self):
        """!
        @brief The energies of one calculation of a compound, with the
        difference of the force field and the reference for each term.
        """
        told = evaluation.calculation(RESULT, 0, "calculation-0007")
        self.assertEqual({"name": "w2", "dataset": "Train", "calculation": "calculation-0007",
                          "datafile": "0007",
                          "energies": [{"term": "INTERACTION", "reference": -20, "forcefield": -19.5,
                                        "difference": 0.5},
                                       {"term": "COULOMB", "reference": -30, "forcefield": -28,
                                        "difference": 2}]}, told)
        partial = evaluation.calculation(RESULT, 0, "calculation-0008")["energies"]
        self.assertEqual([{"term": "INTERACTION", "reference": -5, "forcefield": -6,
                           "difference": -1},
                          {"term": "COULOMB", "reference": 1, "forcefield": None,
                           "difference": None}], partial)
        for index, key in ((0, "calculation-9999"), (1, "calculation-0007"), (5, "calculation-0007"),
                           (0, "../x"), (0, 7), ("0", "calculation-0007")):
            with self.assertRaises(evaluation.EvaluationError, msg=repr((index, key))):
                evaluation.calculation(RESULT, index, key)

    def test_structure_of_a_calculation(self):
        """!
        @brief The atoms of a calculation in a molprop file, found by the
        name or IUPAC name of the compound and the data file of the
        calculation, in Angstrom, with the compound of the dimer that each
        atom belongs to.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mp.xml"
            path.write_text(MOLPROP)
            atoms = evaluation.structure([path], "w2", "0007")
            self.assertEqual(["O", "H", "H", "O", "H", "Cl"], [a["element"] for a in atoms])
            self.assertEqual([0, 0, 0, 1, 1, 1], [a["fragment"] for a in atoms])
            self.assertEqual(["ow", "hw", "hw", "ow", "hw", "cl"], [a["type"] for a in atoms])
            self.assertAlmostEqual(3.0, atoms[3]["x"])
            self.assertAlmostEqual(3.96, atoms[4]["x"])
            self.assertAlmostEqual(0.93, atoms[2]["y"])
            # By the IUPAC name as well
            self.assertEqual(atoms, evaluation.structure([path], "water#water", "0007"))
            self.assertEqual([2.0], [a["y"] for a in evaluation.structure([path], "w2", "0008")])
            for name, datafile in (("w2", "0001"), ("nothing", "0007"), ("water", "0007")):
                with self.assertRaises(evaluation.EvaluationError, msg=(name, datafile)):
                    evaluation.structure([path], name, datafile)
            broken = Path(tmp) / "broken.xml"
            broken.write_text("<molecules><molecule molname=")
            with self.assertRaises(evaluation.EvaluationError):
                evaluation.structure([broken], "w2", "0007")

    def test_selection_of_the_user(self):
        """!
        @brief A selection file of the user goes to train_ff as it is, after
        a check: every line a name and Train, Test or Ignore, and one
        compound of the molprop file for training at least.
        """
        molecules = [{"name": "w2", "iupac": "water#water"}, {"name": "methanol#water", "iupac": ""}]
        text = "water#water|Train\n\nmethanol#water|Test\nethanol|Train\n"
        self.assertEqual("water#water|Train\nmethanol#water|Test\nethanol|Train\n",
                         evaluation.user_selection(molecules, text))
        self.assertEqual("water#water|Train\n",
                         evaluation.user_selection(molecules, "  water#water | Train  \r\n"))
        for wrong, said in (("water#water|train\n", "Line 1"),
                            ("water#water|Train\nmethanol#water\n", "Line 2"),
                            ("water#water|Test\nmethanol#water|Ignore\n", "training"),
                            ("ethanol|Train\n", "training"),
                            ("", "training"), (None, "text"),
                            ("x|Train\n" * 3 + "a" * evaluation.MAX_SELECTION_SIZE, "larger")):
            with self.subTest(said=said):
                with self.assertRaises(evaluation.EvaluationError) as caught:
                    evaluation.user_selection(molecules, wrong)
                self.assertIn(said, str(caught.exception))

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

    def test_monomers(self):
        """!
        @brief The monomers of a complex are the parts of its name, when
        there are as many as the compounds that the complex consists of.
        """
        two = [{"formula": "CH4O", "iupac": ""}, {"formula": "H2O", "iupac": "oxidane"}]
        self.assertEqual(["methanol", "water"], evaluation.monomers(
            {"name": "methanol#water", "iupac": "methanol#water", "fragments": two}))
        # Each monomer once
        self.assertEqual(["water"], evaluation.monomers({"name": "water#water",
                                                         "fragments": two}))
        self.assertEqual(["water"], evaluation.monomers({"name": "water",
                                                         "fragments": two[:1]}))
        self.assertEqual(["a", "b", "c"], evaluation.monomers({"iupac": "a # b#c"}))
        # A name that does not tell: what edit_mp says of the compounds
        self.assertEqual(["CH4O", "oxidane"], evaluation.monomers({"name": "dimer 12",
                                                                   "fragments": two}))
        self.assertEqual(["a#b"], evaluation.monomers({"name": "a#b", "fragments": two[:1]}))
        self.assertEqual(["?", "H2O"], evaluation.monomers(
            {"name": "x", "fragments": [{}, {"formula": "H2O"}]}))
        for wrong in (None, {}, {"name": ""}, {"name": 3}, "water"):
            self.assertEqual([], evaluation.monomers(wrong))
        # The description of a file gets them for the page
        data = {"summary": {"molecules": 1}, "molecules": [{"name": "methanol#water",
                                                            "fragments": two}]}
        described = evaluation.described(data)
        self.assertEqual(["methanol", "water"], described["molecules"][0]["monomers"])
        self.assertEqual({"molecules": 1}, described["summary"])
        self.assertNotIn("monomers", data["molecules"][0])
        self.assertEqual({"x": 1}, evaluation.described({"x": 1}))

    def test_compounds_chosen_by_monomer(self):
        """!
        @brief Choosing a monomer chooses the complexes it is in: with A#B,
        A#C and B#C, choosing A gives A#B and A#C but not B#C.
        """
        molecules = [{"name": "A#B"}, {"name": "A#C"}, {"name": "B#C"}, {"name": "A#A"},
                     {"name": "C"}]
        self.assertEqual(["B#C", "C"], evaluation.without_monomers(molecules, ["A"]))
        self.assertEqual(["A#A"], evaluation.without_monomers(molecules, ["B", "C"]))
        self.assertEqual([], evaluation.without_monomers(molecules, ["A", "C"]))
        self.assertEqual(["A#B", "A#C", "B#C", "A#A", "C"],
                         evaluation.without_monomers(molecules, []))
        # All monomers of a complex have to be chosen
        self.assertEqual(["A#C", "B#C", "C"],
                         evaluation.without_monomers(molecules, ["A", "B"], every=True))
        self.assertEqual(["A#B", "B#C"],
                         evaluation.without_monomers(molecules, ["A", "C"], every=True))
        for wrong in (["D"], "A", [3], None, {"A": True}):
            with self.assertRaises(evaluation.EvaluationError, msg=repr(wrong)):
                evaluation.without_monomers(molecules, wrong)

    def test_selection_file(self):
        """!
        @brief A selection file from the choices of the user: by monomer, by
        hand, and with a part for testing that is drawn at random.
        """
        molecules = [{"name": "A#B"}, {"name": "A#C"}, {"name": "B#C"}, {"name": "A#D"},
                     {"name": "D#D"}]
        self.assertEqual("A#B|Train\nA#C|Train\nA#D|Train\n", evaluation.selection_file(
            molecules, {"monomers": {"names": ["A"]}}))
        self.assertEqual("A#B|Train\nA#C|Test\n", evaluation.selection_file(
            molecules, {"monomers": {"names": ["A"]},
                        "sets": {"test": ["A#C"], "ignore": ["A#D"]}}))
        self.assertEqual("A#D|Train\nD#D|Train\n", evaluation.selection_file(
            molecules, {"monomers": {"names": ["A", "D"], "every": True}}))
        everything = "A#B|Train\nA#C|Train\nB#C|Train\nA#D|Train\nD#D|Train\n"
        self.assertEqual(everything, evaluation.selection_file(molecules, {}))
        # A part for testing: the same seed gives the same compounds
        first = evaluation.selection_file(molecules, {"seed": 7}, 0.4)
        self.assertEqual(first, evaluation.selection_file(molecules, {"seed": 7}, 0.4))
        self.assertEqual(2, first.count("|Test"))
        self.assertEqual(3, first.count("|Train"))
        drawn = {evaluation.selection_file(molecules, {"seed": seed}, 0.4)
                 for seed in range(20)}
        self.assertGreater(len(drawn), 1)
        by_monomer = evaluation.selection_file(molecules, {"monomers": {"names": ["A"]}}, 0.34)
        self.assertEqual(["A#B", "A#C", "A#D"],
                         [line.split("|")[0] for line in by_monomer.splitlines()])
        self.assertEqual(1, by_monomer.count("|Test"))
        wrong = [
            None, [], {"monomers": ["A"]}, {"monomers": {"names": "A"}},
            {"monomers": {"names": ["A"], "every": "yes"}}, {"monomers": {"names": ["E"]}},
            # Nothing is left
            {"monomers": {"names": []}},
            # For testing, but not chosen
            {"monomers": {"names": ["A"]}, "sets": {"test": ["B#C"]}},
            {"monomers": {"names": ["A"]}, "sets": ["A#B"]},
            {"seed": -1}, {"seed": 1.5}, {"seed": True}, {"seed": 2 ** 31},
        ]
        for spec in wrong:
            with self.assertRaises(evaluation.EvaluationError, msg=repr(spec)):
                evaluation.selection_file(molecules, spec)

    def test_read_selection(self):
        """!
        @brief A selection file of the user says what each compound of the
        molprop file is used for, as alexandria reads it.
        """
        molecules = [{"name": "w2", "iupac": "water#water"}, {"name": "methanol#water"},
                     {"iupac": "ethanol"}, {"name": "propanol"}]
        read = evaluation.read_selection(
            molecules, "water#water|Train\nmethanol#water|Test\nethanol|Ignore\n")
        self.assertEqual({"test": ["methanol#water"], "ignore": ["ethanol", "propanol"]},
                         read["sets"])
        self.assertEqual((1, 1, 2), (read["train"], read["test"], read["ignore"]))
        self.assertEqual(([], 0, 0, ""), (read["unknown"], read["unknown_count"],
                                          read["invalid"], read["first_invalid"]))
        # What is read is what a selection for a job is made from
        self.assertEqual("water#water|Train\nmethanol#water|Test\n",
                         evaluation.selection(molecules, sets=read["sets"]))
        # The compounds of a dimer in the other order, spaces, empty lines
        # and line ends of another system; the first line of a compound counts
        read = evaluation.read_selection(
            molecules, "\r\n water#methanol | Test \r\n\r\nwater#methanol|Train\r\n"
            "propanol|Train\r\nwater#water|Train")
        self.assertEqual({"test": ["methanol#water"], "ignore": ["ethanol"]}, read["sets"])
        # What cannot be used is told
        read = evaluation.read_selection(
            molecules, "ethanol|Train\nacetone|Train\nacetone|Test\nbutanol|Test\n"
            "propanol|train\nno bar\na|b|c\n|Train\nw2|Train\n")
        self.assertEqual(["methanol#water", "propanol"], read["sets"]["ignore"])
        self.assertEqual((["acetone", "butanol"], 2), (read["unknown"],
                                                       read["unknown_count"]))
        self.assertEqual((4, "propanol|train"), (read["invalid"], read["first_invalid"]))
        # A compound is found by its name as well as by its IUPAC name
        named = [{"name": "1-butanol", "iupac": "butan-1-ol"}, {"name": "w2",
                                                                "iupac": "water#water"}]
        read = evaluation.read_selection(named, "1-butanol|Test\nw2|Train\n")
        self.assertEqual({"test": ["butan-1-ol"], "ignore": []}, read["sets"])
        self.assertEqual(0, read["unknown_count"])
        many = "".join("x%d|Train\n" % i for i in range(50)) + "ethanol|Test\n"
        read = evaluation.read_selection(molecules, many)
        self.assertEqual((evaluation.MAX_UNKNOWN, 50), (len(read["unknown"]),
                                                        read["unknown_count"]))
        # A file for other compounds, or no selection file at all
        for wrong in ("acetone|Train\n", "", "<molecules>\n</molecules>\n", None, 3,
                      b"ethanol|Train\n", "x" * (evaluation.MAX_SELECTION_SIZE + 1)):
            with self.assertRaises(evaluation.EvaluationError, msg=repr(wrong)[:40]):
                evaluation.read_selection(molecules, wrong)
        with self.assertRaises(evaluation.EvaluationError) as caught:
            evaluation.read_selection(molecules, "ethanol,Train\n")
        self.assertIn("ethanol,Train", str(caught.exception))
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.read_selection([], "ethanol|Train\n")

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
