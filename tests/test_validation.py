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
@brief Tests that names which go into files or onto command lines are
checked as a whole: the pattern of a check must also cover the end of the
text, where a newline would otherwise pass.
"""
import unittest

from act_gui import cluster, cluster_jobs, ffgen, tasks, training


class WholeTextTest(unittest.TestCase):
    """!
    @brief A name with a newline at the end is refused, the same name
    without it is taken.
    """

    def test_name_of_a_virtual_site(self):
        """!
        @brief The name of a site goes into the table and the file of
        gen_ff.
        """
        types = ffgen.read_atom_types()
        site = {"type": "v2x", "interaction": "VSITE2", "atoms": ["c3_b", "hc_b"],
                "parameters": {"vs2a": {"min": 0, "max": 1}}}
        request = {"atomtypes": [{"name": "hc"}, {"name": "c3"}]}
        ffgen.parse_choices(dict(request, vsites={"sites": [site]}), types)
        with self.assertRaises(ffgen.GeneratorError):
            ffgen.parse_choices(dict(request, vsites={"sites": [dict(site, type="v2x\n")]}),
                                types)

    def test_parameters_of_a_training(self):
        """!
        @brief The names of the parameters go onto the command line of
        train_ff.
        """
        self.assertEqual({"fit": "sigma"}, training.options({"fit": ["sigma"]}))
        with self.assertRaises(training.TrainingError):
            training.options({"fit": ["sigma\n"]})

    def test_name_of_a_job_on_a_cluster(self):
        """!
        @brief The name of a job is a directory on the cluster and is in its
        batch script.
        """
        self.assertEqual("job1", cluster_jobs.check_name("job1"))
        with self.assertRaises(cluster.ClusterError):
            cluster_jobs.check_name("job1\n")

    def test_change_of_a_force_field(self):
        """!
        @brief The parameter and the types go onto the command line of
        edit_ff.
        """
        good = {"p": "sigma", "a": "c3", "val": 0.3}
        self.assertEqual("c3", tasks._edit_options(good)["a"])
        for wrong in (dict(good, p="sigma\n"), dict(good, a="c3\n"), dict(good, a=["c3", "hc\n"])):
            with self.subTest(wrong=wrong):
                with self.assertRaises(tasks.RequestError):
                    tasks._edit_options(wrong)


    def test_change_of_the_force_constants_of_the_shells(self):
        """!
        @brief What to do with the force constants of the shells and the
        shell types go onto the command line of edit_ff.
        """
        self.assertEqual({"fixkshell": "charge", "a": "c3_s o3_s", "kshell": 3e5},
                         tasks._shell_options({"fixkshell": "charge", "a": ["c3_s", "o3_s"],
                                               "kshell": 3e5}))
        self.assertEqual({"fixkshell": "release"}, tasks._shell_options({"fixkshell": "release"}))
        for wrong in ({}, {"fixkshell": "keep"}, {"fixkshell": "alpha", "a": "c3_s\n"},
                      {"fixkshell": "alpha", "kshell": 0}, {"fixkshell": "alpha", "kshell": True},
                      {"fixkshell": "release", "kshell": 3e5}):
            with self.subTest(wrong=wrong):
                with self.assertRaises(tasks.RequestError):
                    tasks._shell_options(wrong)

if __name__ == "__main__":
    unittest.main()
