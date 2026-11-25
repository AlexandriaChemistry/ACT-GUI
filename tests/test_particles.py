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
                                    "v3bw": {"zeta": 13}}}, told)

    def test_what_is_not_as_expected(self):
        """!
        @brief A result without a force field is passed on, and what is no
        number or no particle type is left out.
        """
        for other in (None, [], {"changed": False}, {"forcefield": "ff.xml"}):
            self.assertEqual(other, particles.digest(other))
        self.assertEqual({"distribution": "", "types": {}},
                         particles.digest({"forcefield": {"interactions": []}}))
        told = particles.digest(described([
            "ow", {"type": "Atom"}, {"identifier": 5, "parameters": [parameter("zeta", 4)]},
            {"identifier": "a", "parameters": "zeta"},
            {"identifier": "b", "parameters": [parameter("zeta", "wide"), parameter("zeta", True),
                                               parameter("zetaVS", -3), "zeta"]},
            {"identifier": "c", "parameters": [parameter("zeta", 12.5)]}], options=[]))
        self.assertEqual({"distribution": "", "types": {"c": {"zeta": 12.5}}}, told)
        self.assertEqual({"distribution": "", "types": {}},
                         particles.digest(described("none", {"chargedistribution": 7})))


if __name__ == "__main__":
    unittest.main()
