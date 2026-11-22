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
@brief Tests of why a job on the cluster failed: the outputs are shortened
copies of those of real jobs, and of what Slurm, the shell, modules, MPI
and Python say.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from act_gui import diagnosis

## The banner of ACT, which tells nothing
BANNER = """     Welcome to the Alexandria Chemistry Toolkit

       Version 1.95b

Mohammad M. Ghahremanpour, Paul J. van Maaren and David van der Spoel

See https://github.com/AlexandriaChemistry/ACT for details.
"""

## mpirun with fewer cores than processes
SLOTS = """--------------------------------------------------------------------------
There are not enough slots available in the system to satisfy the 16
slots that were requested by the application:

  alexandria

Either request fewer procs for your application, or make more slots
available for use.

Alternatively, you can use the --map-by :OVERSUBSCRIBE option to ignore the
number of available slots when deciding the number of processes to
launch.
--------------------------------------------------------------------------
"""


def act_block(header, message, rank):
    """!
    @brief The block that ACT prints when it stops.
    @param header  The kind of error.
    @param message What it says.
    @param rank    The MPI rank.
    @return The text.
    """
    return """
-------------------------------------------------------
Program:     alexandria train_ff, version 1.95b
Source file: msg_handler.cpp (line 326)
Function:    void alexandria::MsgHandler::fatal(alexandria::ACTMessage, const std::string&)
MPI rank:    %d (out of 8)

%s:
%s

For more information and tips for troubleshooting, please check
the ACT website at https://github.com/AlexandriaChemistry/ACT/
-------------------------------------------------------
""" % (rank, header, message)


## A training on eight processes of which compounds got no topology
EMPTY = BANNER + """There are 8 threads/processes and 0 parameter types to optimize.
Error - Missing parameter in force field: HARMONIC_BONDS c2_b~n3_b
Error - Missing parameter in force field: HARMONIC_ANGLES n2_b:c2_b~n3_b
Error - Missing parameter in force field: HARMONIC_BONDS c2_b~n3_b
Error - Fragment Handler could not make topologies: adenine
Error: Cannot make topology for adenine
Error - Missing parameter in force field: FOURIER_DIHEDRALS c2_b:n2_b:c2_b~n3_b
Error: Cannot make topology for cytosine
""" + "Fatal: Training set is empty on one or more of the nodes, check your input\n" * 7 \
    + "".join(act_block("Error in user input", "See message above", rank) for rank in (6, 1)) \
    + """Please check output in file train_ff.log.
MPI_ABORT was invoked on rank 6 in communicator MPI_COMM_WORLD
with errorcode 1.
"""


class DiagnoseTest(unittest.TestCase):
    """!
    @brief The reason why a job failed, what to change and the lines that tell.
    """

    def diagnose(self, output, state="failed", exit_status=1, **more):
        """!
        @brief Why a job failed of which Slurm wrote an output.
        @param output      The output of Slurm, or the files by name.
        @param state       How the job ended.
        @param exit_status The exit status.
        @param more        The processes, the minutes and so on.
        @return The diagnosis.
        """
        texts = output if isinstance(output, dict) else {"slurm.out": output}
        return diagnosis.diagnose(texts, state=state, exit_status=exit_status, **more)

    def test_too_few_cores_for_mpirun(self):
        """!
        @brief The line that matters comes first, before the long advice of mpirun.
        """
        found = self.diagnose(SLOTS, tasks=16)
        self.assertIn("could not start the 16 processes", found["reason"])
        self.assertIn("fewer processes", found["hint"])
        self.assertEqual("processes", found["fix"])
        self.assertEqual([{"file": "slurm.out", "line": 2, "count": 1,
                           "text": "There are not enough slots available in the system to "
                                   "satisfy the 16"}], found["lines"])

    def test_training_set_that_is_empty(self):
        """!
        @brief A message of all processes is told once with its count, the
        compounds that got no topology are told as well, and the message
        of the block that says to look above is not the reason.
        """
        found = self.diagnose(EMPTY, tasks=8)
        self.assertEqual("alexandria stopped: Training set is empty on one or more of the "
                         "nodes, check your input", found["reason"])
        self.assertIn("Each of the 8 processes", found["hint"])
        self.assertEqual("processes", found["fix"])
        fatal = found["lines"][0]
        self.assertEqual((7, "slurm.out"), (fatal["count"], fatal["file"]))
        self.assertEqual(1, len(found["details"]))
        self.assertIn("2 compounds got no topology with this force field: adenine, cytosine.",
                      found["details"][0])
        self.assertIn("parameters for 3 interactions such as HARMONIC_BONDS c2_b~n3_b",
                      found["details"][0])
        texts = [line["text"] for line in found["lines"]]
        self.assertIn("Error: Cannot make topology for adenine", texts)
        self.assertNotIn("See message above", " ".join(texts))
        # On one process the training set is empty for another reason
        self.assertNotIn("processes", self.diagnose(EMPTY, tasks=1)["hint"])

    def test_message_in_the_block_and_in_the_log(self):
        """!
        @brief The message of a block, and compounds that the log file
        tells about: the force field lacks their atom types.
        """
        log = "\n".join("Error - Atom type problem: Cannot find atomtype %s (atom 5) in "
                        "forcefield, there are 77 atomtypes.\nError: Cannot make topology for %s"
                        % (atomtype, compound) for atomtype, compound in
                        (("hn", "adenine"), ("hn", "guanine"), ("hw", "water")))
        output = BANNER + act_block("Error in user input", "No molecules to train on", 0)
        found = self.diagnose({"slurm.out": output, "train_ff.log": log})
        self.assertEqual("alexandria stopped: No molecules to train on", found["reason"])
        self.assertEqual("input", found["fix"])
        # What the force field lacks is what to change
        self.assertIn("tab Force field", found["hint"])
        self.assertIn("3 compounds got no topology", found["details"][0])
        self.assertIn("the atom types hn, hw", found["details"][0])
        self.assertEqual({"file": "slurm.out", "line": 16, "count": 1,
                          "text": "No molecules to train on"}, found["lines"][0])
        self.assertIn("train_ff.log", {line["file"] for line in found["lines"]})
        self.assertEqual(["slurm.out", "train_ff.log"], found["files"])

    def test_compounds_alone(self):
        """!
        @brief Without a message, compounds without a topology are the reason.
        """
        found = self.diagnose("Error: Cannot make topology for water\nProgram finished\n")
        self.assertEqual("1 compound got no topology with this force field: water.",
                         found["reason"])
        self.assertEqual(([], "input"), (found["details"], found["fix"]))

    def test_what_the_job_needs_is_not_there(self):
        """!
        @brief A program, a module or an environment that is not there is
        told before what follows from it.
        """
        cases = {
            "/var/spool/slurmd/job77/slurm_script: line 12: alexandria: command not found\n"
            + SLOTS: ("alexandria was not found", "how ACT is made available"),
            "/var/spool/slurmd/job77/slurm_script: line 9: /home/u/act/bin/ACTRC: No such "
            "file or directory\n": ("/home/u/act/bin/ACTRC was not found", "Check the file"),
            "slurm_script: line 9: conda: command not found\n": ("conda was not found",
                                                                 "Conda is not known"),
            "slurm_script: line 13: mpirun: command not found\n": ("mpirun was not found",
                                                                   "mpirun or srun"),
            "srun: error: node1: task 0: execve(): alexandria: No such file or directory\n":
                ("alexandria was not found", "name of the program"),
            "Lmod has detected the following error:  The following module(s) are unknown: "
            "\"act/1.95\"\n": ("The module act/1.95 is not known", "module avail"),
            "ERROR: Unable to locate a modulefile for 'act'\n": ("The module act is not known",
                                                                  "module avail"),
            "module: command not found\n": ("no command module", "conda environment"),
            "EnvironmentNameNotFound: Could not find conda environment: act195\n":
                ("The conda environment act195 is not there", "conda env list"),
        }
        for output, (reason, hint) in cases.items():
            found = self.diagnose(output)
            self.assertIn(reason, found["reason"], output)
            self.assertIn(hint, found["hint"], output)
            self.assertEqual("settings", found["fix"], output)
        # A file that the program does not find is no file of the settings
        found = self.diagnose("foo.xml: No such file or directory\n"
                              + act_block("File input/output error", "foo.xml", 0))
        self.assertEqual(("alexandria stopped: foo.xml", "input"),
                         (found["reason"], found["fix"]))

    def test_time_memory_and_gpu(self):
        """!
        @brief Slurm stops a job at its time limit: it gets twice the time,
        not more than a job can have. Memory and the GPU are told.
        """
        stopped = ("slurmstepd: error: *** JOB 1234 ON node7 CANCELLED AT 2026-11-21T21:00:00 "
                   "DUE TO TIME LIMIT ***\n")
        found = self.diagnose(stopped, state="lost", exit_status=None, minutes=90)
        self.assertIn("the 90 minutes it asked for were up", found["reason"])
        self.assertEqual(("time", {"minutes": 180}), (found["fix"], found["suggest"]))
        longest = self.diagnose(stopped, state="lost", exit_status=None, minutes=1000,
                                most_minutes=1440)
        self.assertEqual({"minutes": 1440}, longest["suggest"])
        self.assertEqual({}, self.diagnose(stopped, state="lost", exit_status=None,
                                           minutes=1440, most_minutes=1440)["suggest"])
        for output in ("slurmstepd: error: Detected 1 oom-kill event(s) in StepId=1234.0\n",
                       "terminate called after throwing an instance of 'std::bad_alloc'\n"):
            self.assertEqual("memory", self.diagnose(output)["fix"], output)
        found = self.diagnose("CUDA error: no CUDA-capable device is detected\n")
        self.assertEqual(("gpu", {"gpu": False}), (found["fix"], found["suggest"]))
        # A GPU that runs out of memory is a matter of the GPU
        self.assertEqual("gpu", self.diagnose("CUDA error: out of memory\n")["fix"])

    def test_errors_of_the_program(self):
        """!
        @brief An inconsistency, an exception and a crash are errors of the
        program, to report; a script of Python without its module is a
        matter of the settings.
        """
        found = self.diagnose(act_block("Software inconsistency error", "Bond 3 not found", 2))
        self.assertEqual(("report", "alexandria stopped with an error of the program: Bond 3 "
                          "not found"), (found["fix"], found["reason"]))
        found = self.diagnose("terminate called after throwing an instance of "
                              "'std::out_of_range'\n  what():  map::at\nAborted (core dumped)\n")
        self.assertEqual(("report", "alexandria stopped with an exception: map::at"),
                         (found["fix"], found["reason"]))
        found = self.diagnose("[node3:12345] *** Process received signal ***\n"
                              "[node3:12345] Signal: Segmentation fault (11)\n")
        self.assertEqual("report", found["fix"])
        self.assertIn("crashed", found["reason"])
        found = self.diagnose('Traceback (most recent call last):\n  File "run.py", line 3, in '
                              '<module>\n    import openmm\nModuleNotFoundError: No module '
                              'named \'openmm\'\n')
        self.assertEqual(("settings", "The script stopped: No module named 'openmm'"),
                         (found["fix"], found["reason"]))
        found = self.diagnose('Traceback (most recent call last):\n  File "run.py", line 9\n'
                              'ValueError: the temperature is negative\n')
        self.assertEqual("The script stopped with ValueError: the temperature is negative",
                         found["reason"])

    def test_nothing_known(self):
        """!
        @brief Without a known line the exit status and the last lines
        that tell something; a job that ended without a status may have
        run out of time, or was stopped.
        """
        found = self.diagnose(BANNER + "Reading the force field\nComputing\nStep 12\n"
                              "MPI_ABORT was invoked on rank 3\nThanks for using the "
                              "Alexandria Chemistry Toolkit.\n    https://github.com/x\n",
                              exit_status=134)
        self.assertEqual("The program ended with status 134 after one process stopped all "
                         "(MPI_ABORT).", found["reason"])
        texts = [line["text"] for line in found["lines"]]
        self.assertEqual("MPI_ABORT was invoked on rank 3", texts[0])
        self.assertIn("Step 12", texts)
        self.assertFalse(any("Thanks" in text or "https" in text for text in texts))
        lost = self.diagnose("", state="lost", exit_status=None, minutes=30)
        self.assertIn("longer than the 30 minutes", lost["reason"])
        self.assertEqual({"minutes": 60}, lost["suggest"])
        found = self.diagnose("slurmstepd: error: *** JOB 99 ON n1 CANCELLED AT "
                              "2026-11-21T21:00:00 ***\n", state="lost", exit_status=None)
        self.assertIn("stopped on the cluster", found["reason"])
        self.assertEqual(("", {}), (found["fix"], found["suggest"]))
        self.assertEqual("The program ended with status 2.",
                         self.diagnose({}, exit_status=2)["reason"])

    def test_large_file(self):
        """!
        @brief Of a large file the start and the end are read; lines of the
        end have no number.
        """
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(diagnosis, "MAX_READ", 200):
            path = Path(tmp) / "slurm.out"
            path.write_text("Fatal: early\n" + "x" * 50 + "\n" + "y\n" * 300
                            + "Error: Cannot make topology for water\n")
            found = diagnosis.diagnose({"slurm.out": diagnosis.read_text(path)}, state="failed",
                                       exit_status=1)
        self.assertEqual("alexandria stopped: early", found["reason"])
        lines = {line["text"]: line["line"] for line in found["lines"]}
        self.assertEqual({"Fatal: early": 1, "Error: Cannot make topology for water": None},
                         lines)


class SubmitHintTest(unittest.TestCase):
    """!
    @brief What to change when sbatch does not take a job.
    """

    def test_hints(self):
        """!
        @brief The messages of sbatch that are known, and one that is not.
        """
        cases = {
            "sbatch: error: invalid partition specified: GPUX": "sinfo",
            "sbatch: error: Batch job submission failed: Invalid account or account/partition "
            "combination specified": "account",
            "sbatch: error: Batch job submission failed: Requested time limit is invalid "
            "(missing or exceeds some limit)": "less time",
            "sbatch: error: Batch job submission failed: Requested node configuration is not "
            "available": "fewer processes",
            "sbatch: error: Invalid generic resource (gres) specification": "gres",
            "mkdir: cannot create directory 'x': Disk quota exceeded": "no room",
            "bash: sbatch: command not found": "no Slurm",
        }
        for said, hint in cases.items():
            self.assertIn(hint, diagnosis.submit_hint(said), said)
        self.assertEqual("", diagnosis.submit_hint("something else"))


if __name__ == "__main__":
    unittest.main()
