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
@brief Tests of the script actgui, which starts the interface from the
directory of the source without installing it.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from act_gui import __version__

## The script, in the top directory of the source
SCRIPT = Path(__file__).resolve().parent.parent / "actgui"


@unittest.skipIf(sys.platform.startswith("win"), "the script is for a POSIX shell")
class LauncherTest(unittest.TestCase):
    """!
    @brief The script runs act_gui with the Python that it is given.
    """

    def run_script(self, script, cwd):
        """!
        @brief Run the script with --version, with the Python of the tests.
        @param script The script or a link to it.
        @param cwd    The directory to run it in.
        @return The finished process.
        """
        env = dict(os.environ, ACT_GUI_PYTHON=sys.executable)
        # The script itself makes act_gui importable
        env.pop("PYTHONPATH", None)
        return subprocess.run([str(script), "--version"], cwd=str(cwd), env=env,
                              capture_output=True, text=True, timeout=60, check=False)

    def test_version(self):
        """!
        @brief Started from another directory, the script finds the package.
        """
        self.assertTrue(os.access(SCRIPT, os.X_OK), "actgui is not executable")
        with tempfile.TemporaryDirectory() as tmp:
            done = self.run_script(SCRIPT, tmp)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("act-gui " + __version__, done.stdout.strip())

    def test_through_a_link(self):
        """!
        @brief A link to the script in another directory, such as one in the
        PATH, works as well.
        """
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "actgui"
            link.symlink_to(SCRIPT)
            done = self.run_script(link, tmp)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("act-gui " + __version__, done.stdout.strip())


if __name__ == "__main__":
    unittest.main()
