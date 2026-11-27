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
@brief Tests of the input that is made for alexandria gen_ff from the
choices of the user.
"""
import json
import unittest
from types import SimpleNamespace

from act_gui import ffgen


def table_of(text):
    """!
    @brief Read the table that gen_ff gets.
    @param text The text of the file.
    @return For each particle type its columns by name.
    """
    lines = text.splitlines()
    header = lines[0].split("|")
    assert header[0] == "#atomtype"
    rows = {}
    for line in lines[1:]:
        words = line.split("|")
        assert len(words) == len(header), line
        assert words[0] not in rows
        rows[words[0]] = dict(zip(header[1:], words[1:]))
    return rows


class GeneratorTest(unittest.TestCase):
    """!
    @brief From choices to the files and options of gen_ff.
    """

    @classmethod
    def setUpClass(cls):
        """!
        @brief Read the atom types of the package.
        """
        cls.types = ffgen.read_atom_types()

    def choices(self, **request):
        """!
        @brief Check a request with three atom types unless it has its own.
        @param request The parts of the request.
        @return The choices.
        """
        request.setdefault("atomtypes", [{"name": "hc"}, {"name": "c3"}, {"name": "Na+"}])
        return ffgen.parse_choices(request, self.types)

    # --- the data of the package ---

    def test_atom_types(self):
        """!
        @brief The atom types of the package are read, in the order of the
        file.
        """
        self.assertGreater(len(self.types), 50)
        self.assertEqual("hc", next(iter(self.types)))
        carbon = self.types["c3"]
        self.assertEqual(("C", 6), (carbon.element, carbon.atomnr))
        self.assertGreater(carbon.bonds, 0)
        self.assertEqual(0, self.types["Na+"].bonds)
        self.assertEqual((0.0, 0.75), (self.types["hc"].qmin, self.types["hc"].qmax))
        self.assertTrue(carbon.comment)

    def test_virtual_site_files(self):
        """!
        @brief Every file with virtual sites is there and has sites with a
        type, an interaction and atoms.
        """
        for name in ffgen.VSITE_FILES:
            sites = ffgen.read_vsites(name)
            self.assertTrue(sites, name)
            for site in sites:
                self.assertTrue(site["type"])
                self.assertTrue(site["interaction"].startswith("VSITE"))
                self.assertTrue(site["atoms"])
        with self.assertRaises(ffgen.GeneratorError):
            ffgen.read_vsites("../../forcefields/ACS-pg1")

    def test_potentials_have_rules(self):
        """!
        @brief Each potential proposes a known combination rule for each of
        its parameters.
        """
        for group in (ffgen.VDW_POTENTIALS, ffgen.INDUCTION_POTENTIALS):
            for potential in group.values():
                self.assertEqual(set(potential.bounds), set(potential.rules), potential.name)
                for low, high, _unit in potential.bounds.values():
                    self.assertLessEqual(low, high)
                for rule in potential.rules.values():
                    self.assertIn(rule, ffgen.COMBINATION_RULES)

    # --- what is accepted ---

    def test_defaults(self):
        """!
        @brief What a request leaves out is what actgui proposed.
        """
        chosen = self.choices()
        self.assertEqual(("hc", "c3", "Na+"), tuple(t.name for t in chosen.types))
        self.assertFalse(any(t.vsite or t.shell for t in chosen.types))
        self.assertEqual("WANG_BUCKINGHAM", chosen.vdw)
        self.assertEqual("GAUSSIAN", chosen.charge_model)
        self.assertEqual(("HUA_BONDS", "HARMONIC_ANGLES", "FOURIER_DIHEDRALS"),
                         (chosen.bonds, chosen.angles, chosen.dihedrals))
        self.assertEqual((2, 1.0, 1, True), (chosen.nexcl, chosen.epsilonr, chosen.max_row,
                                             chosen.symmetric))
        self.assertEqual("NONE", chosen.induction)
        self.assertEqual({"VANDERWAALS": {"sigma": "InverseSquare",
                                          "epsilon": "HogervorstEpsilon",
                                          "gamma": "MasonGamma"}}, chosen.rules)

    def test_choices_are_taken(self):
        """!
        @brief Everything that can be chosen arrives.
        """
        chosen = self.choices(
            atomtypes=[{"name": "ow", "shell": True}, {"name": "hw", "vsite": True}],
            vdw={"potential": "LJ14_7", "nexcl": 1,
                 "particles": {"hydrogen": False, "heavy": True, "vsite": True, "shell": True}},
            charge={"model": "SLATER", "epsilonr": 1.7, "maxrow": 3, "symmetric": False},
            bonds="MORSE_BONDS", angles="UREY_BRADLEY_ANGLES", dihedrals="PROPER_DIHEDRALS",
            induction={"potential": "MACDANIEL_SCHMIDT", "particles": {"atom": False,
                                                                       "shell": True}},
            vsites={"files": ["vsite3_water_bisector"], "exchange": True},
            rules={"VANDERWAALS": {"sigma": "Arithmetic"},
                   "INDUCTIONCORRECTION": {"bdexp": "Geometric"}})
        self.assertEqual((True, False), (chosen.types[0].shell, chosen.types[0].vsite))
        self.assertEqual((False, True), (chosen.types[1].shell, chosen.types[1].vsite))
        self.assertEqual(("LJ14_7", 1, False, True, True, True),
                         (chosen.vdw, chosen.nexcl, chosen.vdw_hydrogen, chosen.vdw_heavy,
                          chosen.vdw_vsite, chosen.vdw_shell))
        self.assertEqual(("SLATER", 1.7, 3, False), (chosen.charge_model, chosen.epsilonr,
                                                     chosen.max_row, chosen.symmetric))
        self.assertEqual(("MORSE_BONDS", "UREY_BRADLEY_ANGLES", "PROPER_DIHEDRALS"),
                         (chosen.bonds, chosen.angles, chosen.dihedrals))
        self.assertEqual(("MACDANIEL_SCHMIDT", False, True),
                         (chosen.induction, chosen.induction_atom, chosen.induction_shell))
        self.assertEqual((("vsite3_water_bisector",), True), (chosen.vsite_files,
                                                              chosen.exchange))
        # The rule that was given, the others as proposed
        self.assertEqual("Arithmetic", chosen.rules["VANDERWAALS"]["sigma"])
        self.assertEqual("Geometric", chosen.rules["VANDERWAALS"]["epsilon"])
        self.assertEqual("Geometric", chosen.rules["INDUCTIONCORRECTION"]["bdexp"])
        self.assertEqual("Geometric", chosen.rules["INDUCTIONCORRECTION"]["a1dexp"])

    def test_what_is_refused(self):
        """!
        @brief Only values from the lists are taken: nothing else can get
        into the files or onto the command line of gen_ff.
        """
        refused = [
            {"atomtypes": []},
            {"atomtypes": "hc"},
            {"atomtypes": [{"name": "unobtainium"}]},
            {"atomtypes": [{"name": "hc|H|1"}]},
            {"atomtypes": [{"name": "hc"}, {"name": "hc"}]},
            {"atomtypes": [{"name": "hc", "shell": "yes"}]},
            {"atomtypes": ["hc"]},
            {"vdw": {"potential": "LJ12_6 -o /tmp/x"}},
            {"vdw": {"potential": "MORSE_BONDS"}},
            {"vdw": {"nexcl": 7}},
            {"vdw": {"nexcl": 1.5}},
            {"vdw": {"nexcl": "2"}},
            {"vdw": {"particles": {"hydrogen": 1}}},
            {"vdw": "LJ12_6"},
            {"charge": {"model": "DIPOLE"}},
            {"charge": {"epsilonr": 0}},
            {"charge": {"epsilonr": True}},
            {"charge": {"maxrow": 9}},
            {"charge": {"symmetric": "no"}},
            {"bonds": "HARMONIC_ANGLES"},
            {"angles": ["HARMONIC_ANGLES"]},
            {"dihedrals": "-h"},
            {"induction": {"potential": "LJ12_6"}},
            {"vsites": {"files": ["../../../etc/passwd"]}},
            {"vsites": {"files": "vsite4_amine"}},
            {"vsites": {"files": ["vsite4_amine", "vsite4_amine"]}},
            {"vsites": {"exchange": "true"}},
            {"vsites": {"charge": [-4, 4]}},
            {"vsites": {"charge": {"min": "-4"}}},
            {"vsites": {"charge": {"min": 1, "max": -1}}},
            {"vsites": {"charge": {"min": -40}}},
            {"vsites": {"charge": {"max": float("nan")}}},
            {"vsites": {"charge": {"min": -1, "value": 0}}},
            {"vdw": {"sites": "v2cl1"}},
            {"vdw": {"sites": ["cl"]}},
            {"vdw": {"sites": ["v2cl1|x"]}},
            {"vdw": {"sites": ["v2cl1 -o x"]}},
            {"vdw": {"sites": [{"type": "v2cl1"}]}},
            {"vdw": {"sites": ["v2cl1", "v2cl1"]}},
            {"rules": {"BONDS": {"kb": "Geometric"}}},
            {"rules": {"VANDERWAALS": {"delta": "Geometric"}}},
            {"rules": {"VANDERWAALS": {"sigma": "Geometric VANDERWAALS:x:y"}}},
            {"rules": {"INDUCTIONCORRECTION": {"bdexp": "Geometric"}}},
            {"output": "/tmp/ff.xml"},
        ]
        for request in refused:
            with self.subTest(request=request):
                with self.assertRaises(ffgen.GeneratorError):
                    self.choices(**request)
        for request in (None, [], "LJ"):
            with self.assertRaises(ffgen.GeneratorError):
                ffgen.parse_choices(request, self.types)

    # --- what gen_ff gets ---

    def test_plain_force_field(self):
        """!
        @brief Atoms with Gaussian charges and nothing else: one line for
        each type, the bounds of the potential, and the options.
        """
        made = ffgen.build(self.choices(), self.types)
        self.assertIsNone(made.vsites)
        rows = table_of(made.atomtypes)
        self.assertEqual(["hc", "c3", "Na+"], list(rows))
        # gen_ff wants at least thirty columns
        self.assertGreaterEqual(len(made.atomtypes.splitlines()[0].split("|")), 30)
        carbon = rows["c3"]
        self.assertEqual(("C", "6", "c3_b", "c3_z", "c3_z", "", "1"),
                         (carbon["element"], carbon["atomicnumber"], carbon["bondtype"],
                          carbon["zetatype"], carbon["acmtype"], carbon["poltype"],
                          carbon["row"]))
        self.assertEqual("0.076", carbon["radius"])
        self.assertEqual(self.types["c3"].comment, carbon["comment"])
        # The charge follows from the charge model, within the bounds of the type
        self.assertEqual(("ACM", "e", "False"),
                         (carbon[":q:mut"], carbon[":q:unit"], carbon[":q:nonneg"]))
        self.assertEqual(float(carbon[":q:min"]), self.types["c3"].qmin)
        self.assertEqual(("4", "20", "Bounded", "1/nm", "True"),
                         tuple(carbon["COULOMB:zeta:" + k] for k in
                               ("min", "max", "mut", "unit", "nonneg")))
        self.assertEqual(("1", "25", "Bounded"),
                         tuple(carbon["VANDERWAALS:gamma:" + k] for k in ("min", "max", "mut")))
        self.assertEqual("Bounded", carbon["ELECTRONEGATIVITYEQUALIZATION:chi:mut"])
        # An ion makes no bonds: no bond type, and nothing to train for the charge
        sodium = rows["Na+"]
        self.assertEqual("Na+", sodium["bondtype"])
        self.assertEqual("Fixed", sodium["ELECTRONEGATIVITYEQUALIZATION:chi:mut"])
        self.assertEqual("Fixed", sodium["ELECTRONEGATIVITYEQUALIZATION:eta:mut"])
        # No columns of what was not chosen
        header = made.atomtypes.splitlines()[0]
        for absent in ("POLARIZATION", "INDUCTIONCORRECTION", "VDWCORRECTION", "chargeVS"):
            self.assertNotIn(absent, header)
        self.assertEqual({"nexcl": 2, "epsilonr": 1.0, "vdwfn": "WANG_BUCKINGHAM",
                          "qdist": "GAUSSIAN", "qsymm": True, "bondfn": "HUA_BONDS",
                          "anglefn": "HARMONIC_ANGLES", "dihfn": "FOURIER_DIHEDRALS",
                          "cr": "VANDERWAALS:sigma:InverseSquare "
                                "VANDERWAALS:epsilon:HogervorstEpsilon "
                                "VANDERWAALS:gamma:MasonGamma"}, made.options)

    def test_which_particles_are_trained(self):
        """!
        @brief Van der Waals parameters of hydrogen can be fixed while those
        of the other atoms are trained.
        """
        made = ffgen.build(self.choices(vdw={"particles": {"hydrogen": False}}), self.types)
        rows = table_of(made.atomtypes)
        self.assertEqual("Fixed", rows["hc"]["VANDERWAALS:sigma:mut"])
        self.assertEqual("Bounded", rows["c3"]["VANDERWAALS:sigma:mut"])

    def test_point_charges(self):
        """!
        @brief Point charges have no width.
        """
        made = ffgen.build(self.choices(charge={"model": "POINT"}), self.types)
        carbon = table_of(made.atomtypes)["c3"]
        self.assertEqual(("0", "0", "Fixed"),
                         tuple(carbon["COULOMB:zeta:" + k] for k in ("min", "max", "mut")))
        self.assertEqual("POINT", made.options["qdist"])

    def test_shells(self):
        """!
        @brief A polarizable type gets a shell with a polarizability; the
        atom itself becomes a point charge.
        """
        chosen = self.choices(atomtypes=[{"name": "c3", "shell": True}, {"name": "hc"}])
        rows = table_of(ffgen.build(chosen, self.types).atomtypes)
        self.assertEqual(["c3", "c3_s", "hc"], list(rows))
        self.assertEqual("c3_s", rows["c3"]["poltype"])
        self.assertEqual("Fixed", rows["c3"]["COULOMB:zeta:mut"])
        shell = rows["c3_s"]
        self.assertEqual(("X", "c3", "c3_s_z", "", ""),
                         (shell["element"], shell["bondtype"], shell["zetatype"],
                          shell["acmtype"], shell["radius"]))
        self.assertEqual(("-6", "-1", "Bounded"),
                         tuple(shell[":q:" + k] for k in ("min", "max", "mut")))
        self.assertEqual("Bounded", shell["COULOMB:zeta:mut"])
        self.assertEqual(("1e-06", "12", "Bounded", "Angstrom3"),
                         tuple(shell["POLARIZATION:alpha:" + k] for k in
                               ("min", "max", "mut", "unit")))
        self.assertEqual("Fixed", shell["POLARIZATION:rhyper:mut"])
        # Shells have no Van der Waals interactions unless that is chosen
        self.assertEqual("Fixed", shell["VANDERWAALS:sigma:mut"])
        self.assertEqual("", shell["ELECTRONEGATIVITYEQUALIZATION:chi:mut"])
        # The type without a shell has no columns of the shell
        self.assertEqual("", rows["hc"]["POLARIZATION:alpha:min"])
        self.assertEqual("", rows["hc"]["poltype"])

    def test_fixed_force_constant_of_the_shells(self):
        """!
        @brief A fixed force constant of the shells is an option of gen_ff,
        with or without its value; it needs a shell.
        """
        shell = [{"name": "c3", "shell": True}, {"name": "hc"}]
        made = ffgen.build(self.choices(atomtypes=shell), self.types)
        self.assertNotIn("fixkshell", made.options)
        made = ffgen.build(self.choices(atomtypes=shell, shells={"follows": "charge"}), self.types)
        self.assertEqual("charge", made.options["fixkshell"])
        self.assertNotIn("kshell", made.options)
        made = ffgen.build(self.choices(atomtypes=shell, shells={"follows": "alpha", "kshell": 3e5}),
                           self.types)
        self.assertEqual(("alpha", 3e5), (made.options["fixkshell"], made.options["kshell"]))
        for wrong in ({"follows": "both"}, {"follows": "no", "kshell": 3e5},
                      {"follows": "alpha", "kshell": -1}, {"follows": "alpha", "kshell": "3e5"},
                      {"follows": "alpha", "other": 1}):
            with self.subTest(wrong=wrong):
                with self.assertRaises(ffgen.GeneratorError):
                    self.choices(atomtypes=shell, shells=wrong)
        # Without a shell there is nothing to fix
        with self.assertRaises(ffgen.GeneratorError):
            self.choices(shells={"follows": "alpha"})

    def test_second_charge(self):
        """!
        @brief A distributed charge on top of an atom is a second charge of
        the atom type, with its own width and row.
        """
        chosen = self.choices(atomtypes=[{"name": "cl", "vsite": True}],
                              charge={"model": "P+G", "maxrow": 2})
        made = ffgen.build(chosen, self.types)
        self.assertIsNone(made.vsites)
        rows = table_of(made.atomtypes)
        self.assertEqual(["cl"], list(rows))
        chlorine = rows["cl"]
        # The atom is a point charge, the second charge is Gaussian
        self.assertEqual("0", chlorine["row"])
        self.assertEqual("Fixed", chlorine["COULOMB:zeta:mut"])
        self.assertEqual(("-17", "0", "Bounded", "False"),
                         tuple(chlorine[":chargeVS:" + k] for k in
                               ("min", "max", "mut", "nonneg")))
        self.assertEqual(("4", "20", "Bounded"),
                         tuple(chlorine[":zetaVS:" + k] for k in ("min", "max", "mut")))
        self.assertEqual("2", chlorine["rowVS"])
        self.assertEqual("GAUSSIAN", made.options["qdist"])

    def test_virtual_site_on_atom_with_van_der_waals(self):
        """!
        @brief If virtual sites have Van der Waals interactions, the site
        on top of an atom is a particle of its own.
        """
        chosen = self.choices(atomtypes=[{"name": "cl", "vsite": True}],
                              vdw={"particles": {"vsite": True}})
        made = ffgen.build(chosen, self.types)
        rows = table_of(made.atomtypes)
        self.assertEqual(["cl", "v1cl"], list(rows))
        self.assertNotIn("chargeVS", made.atomtypes)
        site = rows["v1cl"]
        self.assertEqual(("VS", "v1cl", "v1cl_z"),
                         (site["element"], site["bondtype"], site["zetatype"]))
        self.assertEqual(("-17", "0"), (site[":q:min"], site[":q:max"]))
        self.assertEqual("Bounded", site["VANDERWAALS:sigma:mut"])
        self.assertEqual([{"type": "v1cl", "interaction": "VSITE1", "atoms": ["cl"]}],
                         json.loads(made.vsites)["virtual_sites"])

    def test_van_der_waals_on_some_virtual_sites(self):
        """!
        @brief The virtual sites that are named have Van der Waals
        parameters that are trained, the others do not; the second charge
        of an atom type that is named is a particle of its own.
        """
        chosen = self.choices(
            atomtypes=[{"name": "ow"}, {"name": "hw"}, {"name": "cl", "vsite": True},
                       {"name": "f", "vsite": True}, {"name": "c3"}],
            vdw={"sites": ["v2cl1", "vx1", "v1cl"]},
            vsites={"files": ["vsite2_halogen_sigma_holes"],
                    "sites": [{"type": "vx1", "interaction": "VSITE3S",
                               "atoms": ["hw_b", "ow_b", "hw_b"],
                               "parameters": {"vs3sa": {"min": 0, "max": 0.4}}},
                              {"type": "vx2", "interaction": "VSITE2",
                               "atoms": ["ow_b", "hw_b"],
                               "parameters": {"vs2a": {"min": -0.5, "max": 0.5}}}]})
        self.assertEqual(("v2cl1", "vx1", "v1cl"), chosen.vdw_sites)
        self.assertFalse(chosen.vdw_vsite)
        made = ffgen.build(chosen, self.types)
        rows = table_of(made.atomtypes)
        for name, trained in (("v2cl1", True), ("vx1", True), ("v1cl", True),
                              ("v2f1", False), ("v2br1", False), ("vx2", False)):
            self.assertEqual("Bounded" if trained else "Fixed",
                             rows[name]["VANDERWAALS:sigma:mut"], name)
            self.assertEqual("Bounded" if trained else "Fixed",
                             rows[name]["VANDERWAALS:epsilon:mut"], name)
        # The second charge of chlorine is a particle, that of fluorine is not
        self.assertEqual("", rows["cl"][":chargeVS:min"])
        self.assertEqual(("-9", "0"), (rows["f"][":chargeVS:min"], rows["f"][":chargeVS:max"]))
        self.assertNotIn("v1f", rows)
        self.assertIn({"type": "v1cl", "interaction": "VSITE1", "atoms": ["cl"]},
                      json.loads(made.vsites)["virtual_sites"])
        # A site that the force field does not have cannot get parameters
        for missing in ("v3bw", "v1ow", "v1br"):
            chosen = self.choices(atomtypes=[{"name": "ow"}, {"name": "hw"},
                                             {"name": "cl", "vsite": True}],
                                  vdw={"sites": ["v1cl", missing]})
            with self.assertRaises(ffgen.GeneratorError) as caught:
                ffgen.build(chosen, self.types)
            self.assertIn(missing, str(caught.exception))
        # All sites, as before, when that is asked for
        chosen = self.choices(atomtypes=[{"name": "cl"}, {"name": "c3"}],
                              vdw={"particles": {"vsite": True}},
                              vsites={"files": ["vsite2_halogen_sigma_holes"]})
        rows = table_of(ffgen.build(chosen, self.types).atomtypes)
        self.assertEqual({"Bounded"}, {rows[name]["VANDERWAALS:sigma:mut"]
                                       for name in ("v2f1", "v2cl1", "v2br1", "v2i1")})

    def test_charge_of_virtual_sites(self):
        """!
        @brief The bounds of the charge of the virtual sites can be chosen;
        the second charge of an atom keeps its own.
        """
        sites = {"files": ["vsite3_water_bisector"],
                 "sites": [{"type": "vx1", "interaction": "VSITE2", "atoms": ["ow_b", "hw_b"],
                            "parameters": {"vs2a": {"min": -0.5, "max": 0.5}}}]}
        types = [{"name": "ow", "vsite": True}, {"name": "hw"}]
        self.assertEqual(ffgen.VSITE_CHARGE_RANGE, self.choices(atomtypes=types).vsite_charge)
        chosen = self.choices(atomtypes=types, vdw={"sites": ["v1ow"]},
                              vsites=dict(sites, charge={"min": -1.5, "max": 0.25}))
        self.assertEqual((-1.5, 0.25), chosen.vsite_charge)
        rows = table_of(ffgen.build(chosen, self.types).atomtypes)
        for name in ("v3bw", "vx1"):
            self.assertEqual(("-1.5", "0.25", "Bounded"),
                             tuple(rows[name][":q:" + k] for k in ("min", "max", "mut")), name)
        self.assertEqual(("-8", "0"), (rows["v1ow"][":q:min"], rows["v1ow"][":q:max"]))
        # One bound, the other as proposed
        self.assertEqual((-4.0, 2.0),
                         self.choices(atomtypes=types,
                                      vsites={"charge": {"max": 2}}).vsite_charge)

    def test_predefined_virtual_sites(self):
        """!
        @brief Predefined virtual sites come with their particle types, and
        sigma-holes with the exchange correction if that is chosen.
        """
        chosen = self.choices(
            atomtypes=[{"name": "ow"}, {"name": "hw"}, {"name": "cl"}, {"name": "c3"}],
            vsites={"files": ["vsite2_halogen_sigma_holes", "vsite3_water_bisector"],
                    "exchange": True})
        made = ffgen.build(chosen, self.types)
        sites = json.loads(made.vsites)["virtual_sites"]
        self.assertEqual(len(ffgen.read_vsites("vsite2_halogen_sigma_holes")) + 1, len(sites))
        rows = table_of(made.atomtypes)
        # The particle types of the sites come first, each once
        self.assertEqual(["v2f1", "v2cl1", "v2br1", "v2i1", "v3bw", "ow", "hw", "cl", "c3"],
                         list(rows))
        hole = rows["v2cl1"]
        self.assertEqual(("VS", "0", "v2cl1", "v2cl1_z", "0"),
                         (hole["element"], hole["mass"], hole["bondtype"], hole["zetatype"],
                          hole["atomicnumber"]))
        # The charge of a site can have either sign, and starts without one
        self.assertEqual(("-4", "4", "Bounded", "False"),
                         tuple(hole[":q:" + k] for k in ("min", "max", "mut", "nonneg")))
        self.assertEqual("Fixed", hole["VANDERWAALS:sigma:mut"])
        # What the file says about its sites goes along to gen_ff
        self.assertIn("Kriz and van der Spoel", json.loads(made.vsites)["description"])
        # The exchange correction is on the sigma-holes only
        self.assertEqual(("0", "50000", "Bounded"),
                         tuple(hole["VDWCORRECTION:aexp:" + k] for k in ("min", "max", "mut")))
        self.assertEqual(("1", "1"), (hole["VDWCORRECTION:vsite:min"],
                                      hole["VDWCORRECTION:vsite:max"]))
        for other in ("v3bw", "ow", "cl"):
            self.assertEqual(("0", "0", "Fixed"),
                             tuple(rows[other]["VDWCORRECTION:aexp:" + k]
                                   for k in ("min", "max", "mut")), other)
            self.assertEqual("0", rows[other]["VDWCORRECTION:vsite:max"])
        self.assertIs(True, made.options["vdwcorr"])
        self.assertIn("VDWCORRECTION:aexp:Kronecker VDWCORRECTION:bexp:Arithmetic "
                      "VDWCORRECTION:vsite:Geometric", made.options["cr"])
        # Without the correction there are no columns for it
        chosen = self.choices(vsites={"files": ["vsite2_halogen_sigma_holes"]})
        made = ffgen.build(chosen, self.types)
        self.assertNotIn("VDWCORRECTION", made.atomtypes)
        self.assertNotIn("vdwcorr", made.options)
        self.assertNotIn("VDWCORRECTION", made.options["cr"])

    def test_induction_correction(self):
        """!
        @brief The induction correction adds its parameters, its function
        and its combination rules.
        """
        chosen = self.choices(atomtypes=[{"name": "c3", "shell": True}],
                              induction={"potential": "MACDANIEL_SCHMIDT"})
        made = ffgen.build(chosen, self.types)
        rows = table_of(made.atomtypes)
        self.assertEqual(("5", "50", "Bounded", "1/nm"),
                         tuple(rows["c3"]["INDUCTIONCORRECTION:bdexp:" + k]
                               for k in ("min", "max", "mut", "unit")))
        self.assertEqual("Fixed", rows["c3_s"]["INDUCTIONCORRECTION:bdexp:mut"])
        self.assertEqual("MACDANIEL_SCHMIDT", made.options["icfn"])
        self.assertIn("INDUCTIONCORRECTION:bdexp:Arithmetic", made.options["cr"])

    def test_extra_virtual_sites(self):
        """!
        @brief Virtual sites of the user are added to the predefined ones.
        """
        mine = {"type": "v2x", "interaction": "VSITE2", "atoms": ["c3_b", "hc_b"],
                "parameters": {"vs2a": {"min": 0, "max": 1}}}
        made = ffgen.build(self.choices(), self.types, extra_vsites=[mine])
        self.assertEqual([mine], json.loads(made.vsites)["virtual_sites"])
        self.assertIn("v2x", table_of(made.atomtypes))

    def test_sites_of_the_user(self):
        """!
        @brief Virtual sites that the user defined are written for gen_ff
        with their particle types, after the predefined ones.
        """
        mine = [{"type": "v2x", "interaction": "VSITE2", "atoms": ["c3_b", "hc_b"],
                 "parameters": {"vs2a": {"min": -0.5, "max": 1}}},
                {"type": "v3y", "interaction": "VSITE3OUT", "atoms": ["hc_b", "c3_b", "hc_b"],
                 "parameters": {"vs3out_a": {"min": 0, "max": 0.5},
                                "vs3out_b": {"min": 0, "max": 0.5},
                                "vs3out_c": {"min": -10, "max": 10}}}]
        chosen = self.choices(vsites={"files": ["vsite4_amine"], "sites": mine})
        self.assertEqual(tuple(mine), chosen.sites)
        made = ffgen.build(chosen, self.types)
        written = json.loads(made.vsites)["virtual_sites"]
        self.assertEqual(mine, written[-2:])
        self.assertEqual(len(ffgen.read_vsites("vsite4_amine")) + 2, len(written))
        rows = table_of(made.atomtypes)
        self.assertEqual(["v4n", "v2x", "v3y", "hc", "c3", "Na+"], list(rows))
        self.assertEqual(("VS", "v2x", "v2x_z"), (rows["v2x"]["element"], rows["v2x"]["bondtype"],
                                                  rows["v2x"]["zetatype"]))
        # Without sites nothing changes
        self.assertEqual((), self.choices().sites)

    def test_fixed_parameters(self):
        """!
        @brief Parameters of the user, as the file of actgui gave them, are
        fixed at their value; those of types that are not selected are left
        out, as actgui did.
        """
        fixed = [{"interaction": "VANDERWAALS", "type": "c3", "parameter": "sigma",
                  "value": 0.35, "unit": "nm"},
                 {"interaction": "ELECTRONEGATIVITYEQUALIZATION", "type": "c3",
                  "parameter": "chi", "value": 7.5, "unit": ""},
                 {"interaction": "", "type": "hc", "parameter": "q", "value": -0.1, "unit": "e"},
                 {"interaction": "VANDERWAALS", "type": "oh", "parameter": "sigma",
                  "value": 0.3, "unit": "nm"}]
        chosen = self.choices(fixed=fixed)
        self.assertEqual(4, len(chosen.fixed))
        rows = table_of(ffgen.build(chosen, self.types).atomtypes)
        self.assertEqual(("0.35", "0.35", "Fixed", "nm", "True"),
                         tuple(rows["c3"]["VANDERWAALS:sigma:" + k] for k in
                               ("min", "max", "mut", "unit", "nonneg")))
        # Without a unit the unit of the table
        self.assertEqual(("7.5", "7.5", "Fixed", "eV/e"),
                         tuple(rows["c3"]["ELECTRONEGATIVITYEQUALIZATION:chi:" + k] for k in
                               ("min", "max", "mut", "unit")))
        # A negative value may be negative
        self.assertEqual(("-0.1", "-0.1", "Fixed", "False"),
                         tuple(rows["hc"][":q:" + k] for k in ("min", "max", "mut", "nonneg")))
        # The rest is as without them
        plain = table_of(ffgen.build(self.choices(), self.types).atomtypes)
        self.assertEqual(plain["Na+"], rows["Na+"])
        self.assertEqual(plain["c3"]["VANDERWAALS:epsilon:mut"], rows["c3"]["VANDERWAALS:epsilon:mut"])
        self.assertEqual((), self.choices().fixed)

    def test_fixed_parameters_that_are_refused(self):
        """!
        @brief A fixed parameter names a parameter that the selected type
        has, in its unit, with a value that is a number, once.
        """
        good = {"interaction": "VANDERWAALS", "type": "c3", "parameter": "sigma",
                "value": 0.35, "unit": "nm"}
        refused = [
            ("not a list", "fixed"),
            ([dict(good, value="0.35")], "number"),
            ([dict(good, value=float("nan"))], "number"),
            ([dict(good, value=True)], "number"),
            ([dict(good, parameter="sigma|x")], "parameter"),
            ([dict(good, type="c3\n")], "type"),
            ([dict(good, interaction=3)], "interaction"),
            ([dict(good, unit="Angstrom")], "unit"),
            ([dict(good, other=1)], "other"),
            ([good, dict(good, value=0.4)], "twice"),
            ([dict(good, parameter="kappa")], "kappa"),
            ([dict(good, interaction="POLARIZATION", parameter="alpha")], "alpha"),
            ([good] * (ffgen.MAX_FIXED + 1), "At most"),
        ]
        for fixed, said in refused:
            with self.subTest(said=said):
                with self.assertRaises(ffgen.GeneratorError) as caught:
                    ffgen.build(self.choices(fixed=fixed), self.types)
                self.assertIn(said, str(caught.exception))

    def test_sites_that_are_refused(self):
        """!
        @brief A site must be complete, of a known kind, on atoms that are
        in the force field and make bonds, and with bounds that are numbers.
        """
        good = {"type": "v2x", "interaction": "VSITE2", "atoms": ["c3_b", "hc_b"],
                "parameters": {"vs2a": {"min": 0, "max": 1}}}
        self.choices(vsites={"sites": [good]})

        def changed(**change):
            return dict(good, **change)
        refused = [
            "v2x", {"sites": good}, [good] * 51,
            ["v2x"],
            [changed(type="x2")], [changed(type="v")], [changed(type="v2x; rm")],
            [changed(type="v" + "a" * 15)], [changed(type=7)],
            # The name of the site that is made for an atom type itself
            [changed(type="v1hc")],
            [changed(interaction="BONDS")], [changed(interaction="VSITE1")],
            [changed(interaction=None)],
            [changed(atoms=["c3_b"])], [changed(atoms=["c3_b", "hc_b", "hc_b"])],
            [changed(atoms="c3_b hc_b")],
            # Not selected, no bond type, or an ion that makes no bonds
            [changed(atoms=["c3_b", "ow_b"])], [changed(atoms=["c3_b", "hc"])],
            [changed(atoms=["c3_b", "Na+_b"])], [changed(atoms=["c3_b", 5])],
            [changed(parameters={})], [changed(parameters={"vs3a": {"min": 0, "max": 1}})],
            [changed(parameters={"vs2a": {"min": 0, "max": 1}, "vs3a": {"min": 0, "max": 1}})],
            [changed(parameters={"vs2a": {"min": 0}})],
            [changed(parameters={"vs2a": {"min": 0, "max": 1, "value": 0.5}})],
            [changed(parameters={"vs2a": {"min": 1, "max": 0}})],
            [changed(parameters={"vs2a": {"min": "0", "max": 1}})],
            [changed(parameters={"vs2a": {"min": True, "max": 1}})],
            [changed(parameters={"vs2a": {"min": 0, "max": 1e9}})],
            [changed(parameters={"vs2a": {"min": float("nan"), "max": 1}})],
            [changed(parameters={"vs2a": [0, 1]})],
            [changed(comment="mine")],
        ]
        for sites in refused:
            with self.subTest(sites=str(sites)[:80]):
                with self.assertRaises(ffgen.GeneratorError):
                    self.choices(vsites={"sites": sites} if not isinstance(sites, dict)
                                 or "sites" not in sites else sites)

    def test_kinds_of_sites(self):
        """!
        @brief Each kind of site that can be defined is known with its
        atoms and parameters, also to the browser.
        """
        described = ffgen.describe(self.types)["site_kinds"]
        self.assertEqual(list(ffgen.SITE_KINDS), [kind["name"] for kind in described])
        for kind in described:
            self.assertIn(kind["atoms"], (2, 3, 4))
            self.assertTrue(kind["parameters"])
            atoms = ["c3_b", "hc_b", "hc_b", "hc_b"][:kind["atoms"]]
            site = {"type": "vx", "interaction": kind["name"], "atoms": atoms,
                    "parameters": {name: {"min": 0, "max": 0.3} for name in kind["parameters"]}}
            self.assertEqual((site,), self.choices(vsites={"sites": [site]}).sites)

    def test_cross_term_angles(self):
        """!
        @brief The cross terms of the bonds and the angle can be chosen as
        the function for angles, which gen_ff is told.
        """
        self.assertIn("CROSS_BOND_ANGLES", ffgen.ANGLE_FUNCTIONS)
        self.assertEqual("HARMONIC_ANGLES", ffgen.ANGLE_FUNCTIONS[0])
        chosen = self.choices(atomtypes=[{"name": "ow"}, {"name": "hw"}],
                              angles="CROSS_BOND_ANGLES")
        self.assertEqual("CROSS_BOND_ANGLES", chosen.angles)
        made = ffgen.build(chosen, self.types)
        self.assertEqual("CROSS_BOND_ANGLES", made.options["anglefn"])
        self.assertIn("CROSS_BOND_ANGLES", ffgen.describe(self.types)["angles"])

    def test_only_the_functions_of_the_program(self):
        """!
        @brief A function that the program does not have is not offered, in
        the order of the interface. A program that does not tell what it
        accepts gets all of them.
        """
        def program(**values):
            """A gen_ff with these values of its options"""
            table = {name: SimpleNamespace(name=name, values=tuple(allowed))
                     for name, allowed in values.items()}
            return SimpleNamespace(options=table, has_option=lambda name: name in table)

        # An older program, without the cross terms and with a function
        # that the interface does not have
        older = program(anglefn=("UREY_BRADLEY_ANGLES", "QUARTIC_ANGLES", "HARMONIC_ANGLES"),
                        bondfn=ffgen.BOND_FUNCTIONS)
        described = ffgen.describe(self.types, older)
        self.assertEqual(["HARMONIC_ANGLES", "UREY_BRADLEY_ANGLES"], described["angles"])
        self.assertEqual(list(ffgen.BOND_FUNCTIONS), described["bonds"])
        # It has no -dihfn that tells its values
        self.assertEqual(list(ffgen.DIHEDRAL_FUNCTIONS), described["dihedrals"])
        newer = program(anglefn=ffgen.ANGLE_FUNCTIONS)
        self.assertEqual(list(ffgen.ANGLE_FUNCTIONS),
                         ffgen.describe(self.types, newer)["angles"])
        for silent in (program(anglefn=()), program(anglefn=("QUARTIC_ANGLES",))):
            self.assertEqual(list(ffgen.ANGLE_FUNCTIONS),
                             ffgen.describe(self.types, silent)["angles"])
        self.assertEqual(list(ffgen.ANGLE_FUNCTIONS), ffgen.describe(self.types)["angles"])

    def test_description(self):
        """!
        @brief The description for the browser has everything that can be
        chosen, and what it proposes is accepted.
        """
        described = ffgen.describe(self.types)
        json.dumps(described)
        self.assertEqual(len(self.types), len(described["atomtypes"]))
        self.assertEqual({"name", "element", "bonds", "charge", "default", "vsite", "shell",
                          "comment"}, set(described["atomtypes"][0]))
        names = [p["name"] for p in described["vdw"]["potentials"]]
        self.assertIn(described["vdw"]["default"], names)
        request = {
            "atomtypes": [{"name": t["name"], "vsite": t["vsite"], "shell": t["shell"]}
                          for t in described["atomtypes"] if t["default"]],
            "vdw": {"potential": described["vdw"]["default"]},
            "charge": {"model": described["charge"]["default"]},
            "bonds": described["bonds"][0], "angles": described["angles"][0],
            "dihedrals": described["dihedrals"][0],
            "induction": {"potential": described["induction"]["default"]},
        }
        chosen = ffgen.parse_choices(request, self.types)
        self.assertGreater(len(chosen.types), 10)
        # The predefined virtual sites, with what they are and their sites
        self.assertEqual(list(ffgen.VSITE_FILES), [v["name"] for v in described["vsites"]])
        for sites in described["vsites"]:
            self.assertTrue(sites["description"], sites["name"])
            self.assertEqual(ffgen.read_vsites(sites["name"]), sites["sites"])
        halogens = described["vsites"][0]
        self.assertIn("Kriz and van der Spoel", halogens["description"])
        self.assertEqual({"v2f1", "v2cl1", "v2br1", "v2i1"},
                         {site["type"] for site in halogens["sites"]})
        self.assertEqual({"min": -4.0, "max": 4.0, "limits": [-10.0, 10.0]},
                         described["vsite_charge"])
        request["vsites"] = {"charge": {"min": described["vsite_charge"]["min"],
                                        "max": described["vsite_charge"]["max"]}}
        self.assertEqual(ffgen.VSITE_CHARGE_RANGE,
                         ffgen.parse_choices(request, self.types).vsite_charge)
        # Each potential with the rules it proposes
        for potential in described["vdw"]["potentials"]:
            request["vdw"] = {"potential": potential["name"]}
            request["rules"] = {"VANDERWAALS": potential["rules"]}
            ffgen.parse_choices(request, self.types)


if __name__ == "__main__":
    unittest.main()
