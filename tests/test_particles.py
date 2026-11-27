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
@brief Tests of what goes to the browser of the charge distributions of a
force field.
"""
import unittest

from act_gui import particles


def parameter(name, value):
    """!
    @brief A parameter as edit_ff describes it.
    @param name  The name.
    @param value The value.
    @return The parameter.
    """
    return {"type": name, "unit": "1/nm", "value": value, "minimum": 0, "maximum": 20,
            "mutability": "Bounded"}


def described(types, options=None):
    """!
    @brief The result of edit_ff -json for a force field.
    @param types   The particle types.
    @param options The options of the particle types, those of Gaussian charges if None.
    @return The result.
    """
    options = {"chargedistribution": "Gaussian", "nexcl": "2"} if options is None else options
    return {"changed": False, "validation": {"ok": True, "issues": []},
            "forcefield": {"polarizable": True, "interactions": [{"type": "BONDS"}],
                           "particletypes": {"options": options, "types": types}}}


class ParticlesTest(unittest.TestCase):
    """!
    @brief The kind of distribution and the widths of the particle types.
    """

    def test_widths(self):
        """!
        @brief The zeta of each particle type and of its second charge, and
        nothing else of the force field.
        """
        told = particles.digest(described([
            {"identifier": "ow", "type": "Atom",
             "parameters": [parameter("charge", 0.5), parameter("mass", 15.999),
                            parameter("zeta", 0), parameter("chargeVS", -1.2),
                            parameter("zetaVS", 4)]},
            {"identifier": "ow_s", "type": "Shell",
             "parameters": [parameter("charge", -1), parameter("zeta", 6.5)]},
            {"identifier": "v3bw", "type": "Vsite", "parameters": [parameter("zeta", 13)]},
            {"identifier": "he", "type": "Atom", "parameters": [parameter("mass", 4)]}]))
        self.assertEqual({"distribution": "Gaussian",
                          "types": {"ow": {"zeta": 0, "zeta_vs": 4}, "ow_s": {"zeta": 6.5},
                                    "v3bw": {"zeta": 13}}, "shell_max_distance": 0.0}, told)

    def test_wall_of_the_shells(self):
        """!
        @brief The largest distance between a shell and its atom: the
        option of the polarization, 0.02 nm without it as in ACT, and none
        if it is zero or if the force field has no polarization.
        """
        def wall(interactions):
            return particles.wall({"interactions": interactions})
        self.assertEqual(0.0, wall([{"type": "BONDS", "options": {"shellmaxdistance": "0.05"}}]))
        self.assertEqual(0.02, wall([{"type": "BONDS"}, {"type": "POLARIZATION", "options": {}}]))
        self.assertEqual(0.02, wall([{"type": "POLARIZATION"}]))
        self.assertEqual(0.035, wall([{"type": "POLARIZATION",
                                       "options": {"shellmaxdistance": "0.035"}}]))
        self.assertEqual(0.0, wall([{"type": "POLARIZATION", "options": {"shellmaxdistance": "0"}}]))
        self.assertEqual(0.0, wall([{"type": "POLARIZATION",
                                     "options": {"shellmaxdistance": "-1"}}]))
        self.assertEqual(0.02, wall([{"type": "POLARIZATION",
                                      "options": {"shellmaxdistance": "far"}}]))
        for other in (None, "POLARIZATION", [None, 3]):
            self.assertEqual(0.0, wall(other))
        told = particles.digest({"forcefield": {
            "interactions": [{"type": "POLARIZATION", "options": {"shellmaxdistance": "0.03"}}],
            "particletypes": {"options": {}, "types": []}}})
        self.assertEqual(0.03, told["shell_max_distance"])

    def test_what_is_not_as_expected(self):
        """!
        @brief A result without a force field is passed on, and what is no
        number or no particle type is left out.
        """
        for other in (None, [], {"changed": False}, {"forcefield": "ff.xml"}):
            self.assertEqual(other, particles.digest(other))
        self.assertEqual({"distribution": "", "types": {}, "shell_max_distance": 0.0},
                         particles.digest({"forcefield": {"interactions": []}}))
        told = particles.digest(described([
            "ow", {"type": "Atom"}, {"identifier": 5, "parameters": [parameter("zeta", 4)]},
            {"identifier": "a", "parameters": "zeta"},
            {"identifier": "b", "parameters": [parameter("zeta", "wide"), parameter("zeta", True),
                                               parameter("zetaVS", -3), "zeta"]},
            {"identifier": "c", "parameters": [parameter("zeta", 12.5)]}], options=[]))
        self.assertEqual({"distribution": "", "types": {"c": {"zeta": 12.5}},
                          "shell_max_distance": 0.0}, told)
        self.assertEqual({"distribution": "", "types": {}, "shell_max_distance": 0.0},
                         particles.digest(described("none", {"chargedistribution": 7})))


if __name__ == "__main__":
    unittest.main()
