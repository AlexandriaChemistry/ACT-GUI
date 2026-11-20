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
@brief Tests of the settings of the cluster for long jobs: what is
accepted, the file they are kept in, and the login with ssh. A small
program stands in for ssh, so that no test logs in anywhere.
"""
import json
import os
import shlex
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from act_gui import cluster
from act_gui.app import TOKEN_HEADER, create_app
from act_gui.settings import Settings
from tests.test_api import fake_program_info
from tests.test_jobs import FakeRun

## A stand-in for ssh. It writes its arguments in a file next to itself and
## then does what the name of the server says: "good" runs the command of
## the server in a shell on this machine, in a directory that stands for
## the home directory; "nologin" fails as ssh does; "noslurm" has no sbatch.
FAKE_SSH = r'''import json, os, subprocess, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "calls.jsonl"), "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\n")
args = sys.argv[1:]
server, remote = args[args.index("--") + 1], args[args.index("--") + 2]
if server == "nologin":
    sys.stderr.write("user@nologin: Permission denied (publickey,password).\n")
    sys.exit(255)
home = os.path.join(here, "home")
bin_dir = os.path.join(here, "bin" if server != "noslurm" else "nobin")
env = dict(os.environ, PATH=bin_dir + os.pathsep + "/usr/bin" + os.pathsep + "/bin")
sys.exit(subprocess.run(["/bin/sh", "-c", remote], cwd=home, env=env).returncode)
'''


class SettingsTest(unittest.TestCase):
    """!
    @brief What can be the server, the user and the working directory.
    """

    def test_good_settings(self):
        """!
        @brief Names as they occur are taken; spaces around them are not kept.
        """
        taken = cluster.parse({"server": " csb.bmc.uu.se ", "user": "spoel",
                               "workdir": "/home/spoel/act-jobs/"})
        self.assertEqual(("csb.bmc.uu.se", "spoel", "/home/spoel/act-jobs"),
                         (taken.server, taken.user, taken.workdir))
        # A name from the configuration of ssh, and a directory in the home directory
        for workdir, kept in (("act", "act"), ("~/act/jobs", "act/jobs"),
                              ("/proj/snic2026-1/user.name/ACT_runs", None),
                              ("scratch/act+gpu@csb", None)):
            taken = cluster.parse({"server": "csb", "user": "u_1.x-y", "workdir": workdir})
            self.assertEqual(kept or workdir, taken.workdir)

    def test_settings_that_are_refused(self):
        """!
        @brief Nothing that ssh could take for an option, and nothing that
        means something to the shell on the server.
        """
        good = {"server": "csb", "user": "spoel", "workdir": "act"}
        cluster.parse(good)
        wrong = [
            None, "csb", [], {},
            {"server": "csb", "user": "spoel"},
            dict(good, extra="x"),
            dict(good, server=""), dict(good, server="  "), dict(good, server=5),
            dict(good, server="-oProxyCommand=evil"), dict(good, server="csb;reboot"),
            dict(good, server="csb evil"), dict(good, server="user@csb"),
            dict(good, server="csb/x"), dict(good, server="c" * 300),
            dict(good, user=""), dict(good, user="-l"), dict(good, user="a b"),
            dict(good, user="a;b"), dict(good, user="a@b"), dict(good, user="$(id)"),
            dict(good, user="1abc"),
            dict(good, workdir=""), dict(good, workdir="/"), dict(good, workdir="~"),
            dict(good, workdir="my jobs"), dict(good, workdir="act;rm"),
            dict(good, workdir="$HOME/act"), dict(good, workdir="act/$(id)"),
            dict(good, workdir="act/`id`"), dict(good, workdir="act/../.."),
            dict(good, workdir="../other"), dict(good, workdir="act/./x"),
            dict(good, workdir="-rf"), dict(good, workdir="act/-x"),
            dict(good, workdir="act\nx"), dict(good, workdir="act/'x'"),
            dict(good, workdir="act/*"), dict(good, workdir="~other/act"),
            dict(good, workdir="a" * 501), dict(good, workdir=["act"]),
        ]
        for given in wrong:
            with self.subTest(given=str(given)[:70]):
                with self.assertRaises(cluster.ClusterError):
                    cluster.parse(given)

    def test_command_for_ssh(self):
        """!
        @brief ssh is called without a shell, never asks a question, and
        gets the user and the server as words of their own.
        """
        chosen = cluster.ClusterSettings("csb", "spoel", "act jobs")
        remote = cluster.remote_command(["cd", "--", chosen.workdir])
        self.assertEqual("cd -- 'act jobs'", remote)
        self.assertEqual(["cat", "a b", "c;d", "$(e)"],
                         shlex.split(cluster.remote_command(["cat", "a b", "c;d", "$(e)"])))
        command = cluster.ssh_command(chosen, remote, program="/usr/bin/ssh")
        self.assertEqual(["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                          "-l", "spoel", "--", "csb", "cd -- 'act jobs'"], command)

    def test_what_is_proposed_and_what_can_be_chosen(self):
        """!
        @brief Without more than the three settings, jobs load the module
        ACT and go to the partitions of csb; all of it can be chosen.
        """
        base = {"server": "csb", "user": "spoel", "workdir": "act"}
        proposed = cluster.parse(base)
        self.assertEqual(("module", "ACT", "CLUSTER,CLUSTER-AMD", "GPU,CLUSTER-AMD", "gpu:1", "",
                          "srun", 120),
                         (proposed.load, proposed.load_name, proposed.partition,
                          proposed.gpu_partition, proposed.gpu_gres, proposed.account,
                          proposed.launcher, proposed.poll_seconds))
        chosen = cluster.parse(dict(base, load="source", load_name="~/tools/bin/ACTRC",
                                    partition="cpu", gpu_partition="gpu,gpu-long",
                                    gpu_gres="gpu:a100:2", account="naiss2026-1-23",
                                    launcher="mpirun", poll_seconds=600))
        self.assertEqual(("source", "tools/bin/ACTRC", "cpu", "gpu,gpu-long", "gpu:a100:2",
                          "naiss2026-1-23", "mpirun", 600),
                         (chosen.load, chosen.load_name, chosen.partition, chosen.gpu_partition,
                          chosen.gpu_gres, chosen.account, chosen.launcher, chosen.poll_seconds))
        self.assertEqual("ACT/1.9b", cluster.parse(dict(base, load_name="ACT/1.9b")).load_name)
        self.assertEqual("act-env", cluster.parse(dict(base, load="conda",
                                                       load_name="act-env")).load_name)
        # Nothing to load: what was given as a name is not kept
        self.assertEqual("", cluster.parse(dict(base, load="none", load_name="ACT")).load_name)
        wrong = [
            dict(base, load="script"), dict(base, load="module", load_name=""),
            dict(base, load_name="ACT; reboot"), dict(base, load_name="-x"),
            dict(base, load_name="ACT $(id)"), dict(base, load_name="../ACT"),
            dict(base, load="source", load_name="/opt/act/bin/ACTRC; id"),
            dict(base, load="source", load_name=""),
            dict(base, load="conda", load_name="my env"),
            dict(base, load="conda", load_name="a/b"),
            dict(base, partition=""), dict(base, partition="a b"), dict(base, partition="a,"),
            dict(base, partition="a;b"), dict(base, partition="--wrap=id"),
            dict(base, gpu_partition="GPU CLUSTER"), dict(base, gpu_gres="gpu:1 --wrap=id"),
            dict(base, gpu_gres=""), dict(base, gpu_gres=":1"),
            dict(base, account="-A"), dict(base, account="a b"), dict(base, account=5),
            dict(base, launcher="bash -c"), dict(base, launcher=""),
            dict(base, poll_seconds=5), dict(base, poll_seconds=100000),
            dict(base, poll_seconds="120"), dict(base, poll_seconds=120.5),
            dict(base, poll_seconds=True),
        ]
        for given in wrong:
            with self.subTest(given={k: v for k, v in given.items() if k not in base}):
                with self.assertRaises(cluster.ClusterError):
                    cluster.parse(given)

    def test_batch_script(self):
        """!
        @brief The script for Slurm has the partitions, the number of
        processes and the time, loads ACT, runs the command with every
        word quoted and leaves the exit status of the program in a file.
        """
        chosen = cluster.parse({"server": "csb", "user": "spoel", "workdir": "act"})
        words = ["alexandria", "train_ff", "-ff", "ff.xml", "-mp", "my data.xml", "-fc_inter", "1"]
        script = cluster.batch_script(chosen, "act-1a2b", words)
        self.assertEqual("""#!/bin/bash -l
#SBATCH --job-name=act-1a2b
#SBATCH --partition=CLUSTER,CLUSTER-AMD
#SBATCH --ntasks=1
#SBATCH --time=0-01:00:00
#SBATCH --output=slurm.out

# Made by the user interface of ACT. The exit status of the program is
# left in a file, so that the interface can tell how the job ended.
module load ACT
alexandria train_ff -ff ff.xml -mp 'my data.xml' -fc_inter 1
status=$?
echo $status > act-exit-status
exit $status
""", script)
        # With a GPU, several processes, an account and more than a day
        chosen = cluster.parse({"server": "csb", "user": "spoel", "workdir": "act",
                                "account": "proj1", "load": "conda", "load_name": "act"})
        script = cluster.batch_script(chosen, "train", words, tasks=16, minutes=36 * 60 + 5,
                                      gpu=True)
        lines = script.splitlines()
        for line in ("#SBATCH --partition=GPU,CLUSTER-AMD", "#SBATCH --gres=gpu:1",
                     "#SBATCH --ntasks=16", "#SBATCH --time=1-12:05:00",
                     "#SBATCH --account=proj1", 'eval "$(conda shell.bash hook)"',
                     "conda activate act",
                     "srun alexandria train_ff -ff ff.xml -mp 'my data.xml' -fc_inter 1"):
            self.assertIn(line, lines)
        # All lines for Slurm come before the first command
        first = min(i for i, line in enumerate(lines[1:], 1) if line and not line.startswith("#"))
        self.assertTrue(all(not line.startswith("#SBATCH") for line in lines[first:]))
        other = cluster.parse({"server": "csb", "user": "spoel", "workdir": "act",
                               "launcher": "mpirun", "load": "source",
                               "load_name": "/opt/act/bin/ACTRC"})
        script = cluster.batch_script(other, "j", ["alexandria", "train_ff"], tasks=4)
        self.assertIn("source /opt/act/bin/ACTRC\nmpirun -n 4 alexandria train_ff\n", script)
        self.assertNotIn("module", cluster.batch_script(
            cluster.parse({"server": "csb", "user": "u", "workdir": "w", "load": "none"}),
            "j", ["alexandria"]))

    def test_words_cannot_leave_the_command(self):
        """!
        @brief Whatever a word of the command is, the shell reads it as one
        word; a job with a name or numbers that are not possible is not made.
        """
        chosen = cluster.parse({"server": "csb", "user": "spoel", "workdir": "act"})
        nasty = ["alexandria", "x; reboot", "$(id)", "`id`", "a'b", "#SBATCH --wrap=id", "-x"]
        script = cluster.batch_script(chosen, "j", nasty)
        command = [line for line in script.splitlines() if line.startswith("alexandria")][0]
        self.assertEqual(nasty, shlex.split(command))
        self.assertEqual(1, script.count("reboot"))
        for wrong in ({"name": "a b"}, {"name": "j\n#SBATCH --wrap=id"}, {"name": ""},
                      {"name": "-x"}, {"tasks": 0}, {"tasks": 10**6}, {"tasks": 2.5},
                      {"tasks": True}, {"minutes": 0}, {"minutes": 10**7}, {"minutes": "60"},
                      {"words": []}, {"words": ["alexandria", ""]},
                      {"words": ["alexandria", "a\nreboot"]}, {"words": ["alexandria", 5]}):
            arguments = {"name": "j", "words": ["alexandria"]}
            arguments.update(wrong)
            with self.subTest(wrong=wrong):
                with self.assertRaises(cluster.ClusterError):
                    cluster.batch_script(chosen, arguments.pop("name"), arguments.pop("words"),
                                         **arguments)


class FileTest(unittest.TestCase):
    """!
    @brief The settings are kept in a file of the user.
    """

    def setUp(self):
        """!
        @brief Make a directory for the file.
        """
        self._tmp = tempfile.TemporaryDirectory()
        self.file = Path(self._tmp.name) / "config" / "act-gui" / "settings.json"
        self.chosen = cluster.ClusterSettings("csb", "spoel", "/home/spoel/act")

    def tearDown(self):
        """!
        @brief Remove the files.
        """
        self._tmp.cleanup()

    def test_save_and_load(self):
        """!
        @brief What is saved is read back; the file is JSON that a person
        can read, and only the user can read it.
        """
        self.assertIsNone(cluster.load(self.file))
        self.assertIsNone(cluster.load(None))
        cluster.save(self.file, self.chosen)
        self.assertEqual(self.chosen, cluster.load(self.file))
        written = json.loads(self.file.read_text())
        self.assertEqual(["cluster"], list(written))
        self.assertEqual(("csb", "spoel", "/home/spoel/act", "module", "ACT"),
                         tuple(written["cluster"][key] for key in
                               ("server", "user", "workdir", "load", "load_name")))
        self.assertIn("\n  ", self.file.read_text())
        self.assertEqual(0o600, stat.S_IMODE(self.file.stat().st_mode))
        self.assertEqual(["settings.json"], [p.name for p in self.file.parent.iterdir()])

    def test_other_settings_are_kept(self):
        """!
        @brief The file can hold more than the cluster; saving and
        forgetting the cluster leaves the rest.
        """
        self.file.parent.mkdir(parents=True)
        self.file.write_text('{"theme": "dark"}')
        cluster.save(self.file, self.chosen)
        self.assertEqual("dark", json.loads(self.file.read_text())["theme"])
        cluster.save(self.file, None)
        self.assertEqual({"theme": "dark"}, json.loads(self.file.read_text()))
        self.assertIsNone(cluster.load(self.file))

    def test_file_that_cannot_be_used(self):
        """!
        @brief A file with settings that are not possible, or that is no
        JSON, is reported and not used; saving replaces it.
        """
        self.file.parent.mkdir(parents=True)
        for content in ('{"cluster": {"server": "-oProxyCommand=x", "user": "u", "workdir": "w"}}',
                        '{"cluster": "csb"}', "{not json", "[1, 2]"):
            self.file.write_text(content)
            with self.assertRaises(cluster.ClusterError) as caught:
                cluster.load(self.file)
            self.assertIn(str(self.file), str(caught.exception))
        cluster.save(self.file, self.chosen)
        self.assertEqual(self.chosen, cluster.load(self.file))

    def test_default_file(self):
        """!
        @brief The file is in the configuration directory of the user.
        """
        saved = os.environ.get("XDG_CONFIG_HOME")
        try:
            os.environ["XDG_CONFIG_HOME"] = "/somewhere/config"
            self.assertEqual(Path("/somewhere/config/act-gui/settings.json"),
                             cluster.default_config_file())
            del os.environ["XDG_CONFIG_HOME"]
            self.assertEqual(Path.home() / ".config" / "act-gui" / "settings.json",
                             cluster.default_config_file())
        finally:
            if saved is not None:
                os.environ["XDG_CONFIG_HOME"] = saved


## The stand-in for the program on the cluster
ALEXANDRIA = r'''#!/bin/sh
echo "{\"asked\": \"$*\"}" > result.json
echo running "$@"
command=$1
status=0
while [ $# -gt 0 ]; do
  case "$1" in
    -json)
      cat > "$2" <<END
{"$command": {"molecules": {"0": {"name": "water#water", "dataset": "Train",
                                  "atoms": {"0": {"fx": 1}},
                                  "interaction_energies": {"c": {}}}},
              "statistics": {"Train": {"INTERACTION": {"N": 2, "RMSD": 1.0}}},
              "outliers": {}}}
END
      printf '@ s0 legend "Train"\n-20 -19\n-5 -6\n&\n' > INTERACTION.xvg;;
    -epotcorr) printf '@ s0 legend "Train"\n1 2\n3 4\n&\n' > "$2";;
    -fail) status=3;;
  esac
  shift
done
exit $status
'''


class FakeServer:
    """!
    @brief A directory that stands for a cluster, with a stand-in for ssh.
    """

    def __init__(self, directory: Path):
        """!
        @brief Make the home directory, a working directory and sbatch.
        @param directory Where to make them.
        """
        self.dir = Path(directory)
        (self.dir / "home" / "act" / "jobs").mkdir(parents=True)
        (self.dir / "bin").mkdir()
        (self.dir / "nobin").mkdir()
        # Stand-ins for the commands of Slurm and for alexandria. sbatch
        # runs the script at once and gives the job the number 4242, unless
        # the file "queue" next to the programs says that jobs wait; squeue
        # prints what that file says; scancel notes what it was asked.
        programs = {
            "sbatch": '#!/bin/sh\n'
                      'if [ "$1" = "--version" ]; then echo slurm 23.02.7; exit 0; fi\n'
                      'here=$(dirname "$0")\n'
                      'echo "$PWD $*" >> "$here/../sbatch.log"\n'
                      'if [ ! -f "$here/../queue" ]; then bash "$2" > slurm.out 2>&1; fi\n'
                      'echo 4242\n',
            "squeue": '#!/bin/sh\nhere=$(dirname "$0")\n'
                      'if [ -f "$here/../queue" ]; then cat "$here/../queue"; fi\n',
            "scancel": '#!/bin/sh\necho "$*" >> "$(dirname "$0")/../scancel.log"\n',
            # Starts the program, as for one process
            "srun": '#!/bin/sh\nexec "$@"\n',
            # Writes what it was asked in result.json, the file of -json
            # with results as train_ff gives them and that of -epotcorr
            # with two points, and fails for -fail
            "alexandria": ALEXANDRIA,
        }
        for name, text in programs.items():
            path = self.dir / "bin" / name
            path.write_text(text)
            path.chmod(0o755)
        script = self.dir / "fake_ssh.py"
        script.write_text(FAKE_SSH)
        # One word that starts the stand-in, as the name of a program
        self.program = self.dir / "ssh"
        self.program.write_text("#!/bin/sh\nexec %s %s \"$@\"\n"
                                % (shlex.quote(sys.executable), shlex.quote(str(script))))
        self.program.chmod(0o755)

    def calls(self):
        """!
        @brief The arguments of each call of the stand-in.
        @return A list of lists of words.
        """
        log = self.dir / "calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.is_file() else []


class CheckTest(unittest.TestCase):
    """!
    @brief One login tells whether jobs can be sent.
    """

    def setUp(self):
        """!
        @brief Make the stand-in for a cluster.
        """
        self._tmp = tempfile.TemporaryDirectory()
        self.server = FakeServer(Path(self._tmp.name))

    def tearDown(self):
        """!
        @brief Remove the files.
        """
        self._tmp.cleanup()

    def check(self, server="good", workdir="act/jobs"):
        """!
        @brief Check settings against the stand-in.
        @param server  The name of the server, which says what the stand-in does.
        @param workdir The working directory.
        @return The answer.
        """
        return cluster.check(cluster.ClusterSettings(server, "spoel", workdir),
                             program=str(self.server.program))

    def test_all_is_well(self):
        """!
        @brief The directory is there and the server has Slurm.
        """
        answer = self.check()
        self.assertTrue(answer["ok"], answer)
        self.assertTrue(answer["directory"].endswith("/home/act/jobs"))
        self.assertEqual("slurm 23.02.7", answer["slurm"])
        self.assertIn("The login works", answer["message"])
        # One login, in batch mode, as the user of the settings
        calls = self.server.calls()
        self.assertEqual(1, len(calls))
        self.assertEqual(["-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-l", "spoel", "--",
                          "good"], calls[0][:8])
        self.assertTrue(calls[0][8].startswith("cd -- act/jobs && "))

    def test_no_login(self):
        """!
        @brief A login that needs a password fails with advice, not a question.
        """
        answer = self.check(server="nologin")
        self.assertFalse(answer["ok"])
        self.assertIn("did not work without a password", answer["message"])
        self.assertIn("Permission denied", answer["message"])

    def test_no_directory(self):
        """!
        @brief A working directory that is not there is said so.
        """
        answer = self.check(workdir="act/nothere")
        self.assertFalse(answer["ok"])
        self.assertIn("act/nothere is not there", answer["message"])
        self.assertNotIn("directory", answer)

    def test_no_slurm(self):
        """!
        @brief A server without sbatch cannot take jobs.
        """
        answer = self.check(server="noslurm")
        self.assertFalse(answer["ok"])
        self.assertIn("sbatch", answer["message"])
        self.assertTrue(answer["directory"].endswith("/home/act/jobs"))

    def test_no_ssh_program_and_no_answer(self):
        """!
        @brief A missing ssh program and a server that does not answer give
        a message, not an exception.
        """
        chosen = cluster.ClusterSettings("good", "spoel", "act")
        answer = cluster.check(chosen, program=str(self.server.dir / "no-such-ssh"))
        self.assertFalse(answer["ok"])
        self.assertIn("could not be started", answer["message"])
        slow = self.server.dir / "slow"
        slow.write_text("#!/bin/sh\nsleep 30\n")
        slow.chmod(0o755)
        answer = cluster.check(chosen, program=str(slow), timeout=0.5)
        self.assertFalse(answer["ok"])
        self.assertIn("did not answer", answer["message"])


class ClusterApiTest(unittest.TestCase):
    """!
    @brief The settings through the interface of the server.
    """

    def setUp(self):
        """!
        @brief Make a stand-in for a cluster and a directory for the file.
        """
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.server = FakeServer(self.dir / "server")
        self.file = self.dir / "config" / "settings.json"
        self.headers = {TOKEN_HEADER: "t"}
        self.apps = []

    def tearDown(self):
        """!
        @brief Stop the jobs and remove the files.
        """
        for app in self.apps:
            app.state.jobs.shutdown()
        self._tmp.cleanup()

    def client(self, **more):
        """!
        @brief Start the interface, as when the program starts.
        @param more Other settings than those of the test.
        @return A client for it.
        """
        options = {"data_dir": self.dir / "data", "token": "t", "config_file": self.file,
                   "ssh_program": str(self.server.program)}
        options.update(more)
        app = create_app(Settings(**options), run=FakeRun(), program_info=fake_program_info)
        self.apps.append(app)
        return TestClient(app)

    def test_settings_are_kept_between_runs(self):
        """!
        @brief What is set is in the file and is there when the interface
        starts again; forgetting takes it out of the file.
        """
        client = self.client()
        self.assertEqual(401, client.get("/api/cluster").status_code)
        first = client.get("/api/cluster", headers=self.headers).json()
        self.assertEqual((False, None, str(self.file), ""),
                         tuple(first[key] for key in ("configured", "cluster", "file",
                                                      "problem")))
        # What the form proposes for the cluster csb
        self.assertEqual({"load": "module", "load_name": "ACT",
                          "partition": "CLUSTER,CLUSTER-AMD", "gpu_partition": "GPU,CLUSTER-AMD",
                          "gpu_gres": "gpu:1", "account": "", "launcher": "srun",
                          "poll_seconds": 120}, first["proposed"])
        self.assertEqual(["module", "source", "conda", "none"], first["load_kinds"])
        self.assertIs(True, client.get("/api/session", headers=self.headers).json()["cluster"])
        chosen = {"server": "good", "user": "spoel", "workdir": "~/act/jobs"}
        # Changing needs the key in the header
        self.assertEqual(401, client.put("/api/cluster", json=chosen).status_code)
        answer = client.put("/api/cluster", headers=self.headers, json=chosen)
        self.assertEqual(200, answer.status_code, answer.text)
        kept = dict(first["proposed"], server="good", user="spoel", workdir="act/jobs")
        self.assertEqual(kept, answer.json()["cluster"])
        self.assertEqual({"cluster": kept}, json.loads(self.file.read_text()))
        # The interface starts again
        again = self.client()
        state = again.get("/api/cluster", headers=self.headers).json()
        self.assertTrue(state["configured"])
        self.assertEqual(kept, state["cluster"])
        gone = again.delete("/api/cluster", headers=self.headers).json()
        self.assertFalse(gone["configured"])
        self.assertEqual({}, json.loads(self.file.read_text()))
        self.assertFalse(self.client().get("/api/cluster", headers=self.headers).json()["configured"])

    def test_settings_that_are_refused(self):
        """!
        @brief Settings that are not possible are not kept.
        """
        client = self.client()
        for wrong in ({"server": "-oProxyCommand=x", "user": "u", "workdir": "w"},
                      {"server": "csb", "user": "u", "workdir": "w; reboot"},
                      {"server": "csb"}, ["csb"]):
            answer = client.put("/api/cluster", headers=self.headers, json=wrong)
            self.assertEqual(400, answer.status_code, wrong)
            self.assertTrue(answer.json()["detail"])
        self.assertEqual(400, client.put("/api/cluster", headers=self.headers,
                                         content=b"{not json").status_code)
        self.assertFalse(self.file.exists())
        self.assertFalse(client.get("/api/cluster", headers=self.headers).json()["configured"])

    def test_file_with_a_problem(self):
        """!
        @brief A file that cannot be used does not stop the interface: the
        page is told, and new settings replace the file.
        """
        self.file.parent.mkdir(parents=True)
        self.file.write_text('{"cluster": {"server": "a b", "user": "u", "workdir": "w"}}')
        client = self.client()
        state = client.get("/api/cluster", headers=self.headers).json()
        self.assertFalse(state["configured"])
        self.assertIn("cannot be the name of a server", state["problem"])
        answer = client.put("/api/cluster", headers=self.headers,
                            json={"server": "csb", "user": "u", "workdir": "w"})
        self.assertEqual(200, answer.status_code)
        self.assertEqual("", answer.json()["problem"])

    def test_check(self):
        """!
        @brief The check logs in once with the settings that were kept.
        """
        client = self.client()
        self.assertEqual(400, client.post("/api/cluster/check", headers=self.headers).status_code)
        self.assertEqual([], self.server.calls())
        client.put("/api/cluster", headers=self.headers,
                   json={"server": "good", "user": "spoel", "workdir": "act/jobs"})
        self.assertEqual(401, client.post("/api/cluster/check").status_code)
        answer = client.post("/api/cluster/check", headers=self.headers).json()
        self.assertTrue(answer["ok"], answer)
        self.assertEqual("slurm 23.02.7", answer["slurm"])
        self.assertEqual(1, len(self.server.calls()))
        client.put("/api/cluster", headers=self.headers,
                   json={"server": "nologin", "user": "spoel", "workdir": "act/jobs"})
        answer = client.post("/api/cluster/check", headers=self.headers).json()
        self.assertFalse(answer["ok"])
        self.assertIn("without a password", answer["message"])

    def test_without_a_file_and_on_a_server(self):
        """!
        @brief Without a file the settings last as long as the program. A
        server for several users has no cluster of one user.
        """
        client = self.client(config_file=None)
        answer = client.put("/api/cluster", headers=self.headers,
                            json={"server": "csb", "user": "u", "workdir": "w"}).json()
        self.assertTrue(answer["configured"])
        self.assertEqual("", answer["file"])
        self.assertFalse(self.file.exists())
        shared = self.client(mode="server")
        self.assertIs(False, shared.get("/api/session", headers=self.headers).json()["cluster"])
        for method, path in (("get", "/api/cluster"), ("delete", "/api/cluster"),
                             ("post", "/api/cluster/check")):
            self.assertEqual(404, getattr(shared, method)(path, headers=self.headers).status_code)
        self.assertEqual(404, shared.put("/api/cluster", headers=self.headers,
                                         json={"server": "csb", "user": "u",
                                               "workdir": "w"}).status_code)


if __name__ == "__main__":
    unittest.main()
