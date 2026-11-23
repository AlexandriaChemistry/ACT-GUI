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
@brief Tests of the installed force fields that can be chosen.
"""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from act_gui import library
from act_gui.app import TOKEN_HEADER, create_app
from act_gui.settings import Settings
from tests.test_api import fake_program_info
from tests.test_jobs import FakeRun
from tests.test_uploads import FF


def forcefield(reference=None, timestamp="2026-11-23 15:43:10"):
    """!
    @brief The text of a small force field file, as ACT writes it.
    @param reference The reference, none if None.
    @param timestamp The time stamp.
    @return The text.
    """
    lines = ['<?xml version="1.0" encoding="ISO-8859-1"?>',
             '<!DOCTYPE alexandria_chemistry.dtd PUBLIC "alexandria_chemistry.dtd" '
             '"alexandria_chemistry.dtd">',
             '<alexandria_chemistry>']
    if reference is not None:
        lines.append('  <option key="reference" value="%s"/>' % reference)
    lines += ['  <version checksum="04da3fb769e618b7dd8e3b6fe70742ed" timestamp="%s"/>' % timestamp,
              '  <particletypes>',
              '    <option key="reference" value="not this one"/>',
              '  </particletypes>',
              '</alexandria_chemistry>', '']
    return "\n".join(lines).encode("iso-8859-1")


def write(path, data):
    """!
    @brief Write a file and the directories it is in.
    @param path The file.
    @param data The contents.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


class LibraryTest(unittest.TestCase):
    """!
    @brief Finding the force fields in directories.
    """

    def setUp(self):
        """!
        @brief Two directories with force fields.
        """
        self._tmp = tempfile.TemporaryDirectory()
        self.first = Path(self._tmp.name) / "first"
        self.second = Path(self._tmp.name) / "second"
        write(self.first / "Water" / "SPC.xml", forcefield("Berendsen1981a"))
        write(self.first / "HF" / "Orabi2020a.xml", forcefield())
        write(self.first / "Top.xml", forcefield("doi:10.1/x", "2026-01-01 00:00:00"))
        # Not force fields of ACT, or not of the library
        write(self.first / "Water" / "molecules.xml", b'<?xml version="1.0"?>\n<molecules/>\n')
        write(self.first / "Water" / "broken.xml", b"<alexandria_chemistry><version")
        write(self.first / ".git" / "Hidden.xml", forcefield())
        write(self.first / "Water" / "notes.txt", b"not xml")
        # The same identifier as in the first directory, and one more
        write(self.second / "Water" / "SPC.xml", forcefield("from the second"))
        write(self.second / "Water" / "TIP3P.xml", forcefield("Jorgensen1983a"))

    def tearDown(self):
        """!
        @brief Remove the files.
        """
        self._tmp.cleanup()

    def test_scan(self):
        """!
        @brief The force fields in the directories and below, by kind and
        name, the first directory first.
        """
        found = library.scan([self.first, self.second, Path(self._tmp.name) / "missing"])
        self.assertEqual(["Top", "HF/Orabi2020a", "Water/SPC", "Water/TIP3P"],
                         [ff.id for ff in found])
        spc = found[2]
        self.assertEqual(self.first / "Water" / "SPC.xml", spc.path)
        self.assertEqual("Berendsen1981a", spc.reference)
        self.assertEqual("2026-11-23 15:43:10", spc.timestamp)
        self.assertEqual({"id": "Water/SPC", "name": "SPC", "category": "Water",
                          "reference": "Berendsen1981a", "timestamp": "2026-11-23 15:43:10"},
                         spc.describe())
        # Without a reference, and directly in the directory
        self.assertEqual("", found[1].reference)
        self.assertEqual(("Top", ""), (found[0].name, found[0].category))
        self.assertEqual("doi:10.1/x", found[0].reference)

    def test_find(self):
        """!
        @brief Only identifiers of the list are found.
        """
        dirs = [self.first, self.second]
        self.assertEqual(self.second / "Water" / "TIP3P.xml", library.find(dirs, "Water/TIP3P").path)
        for wrong in ("Water/molecules", "Water/SPC.xml", "../first/Water/SPC", "/etc/passwd",
                      ".git/Hidden", "Water"):
            self.assertIsNone(library.find(dirs, wrong), wrong)

    def test_default_dirs(self):
        """!
        @brief The directories of the user, then that of ACTDATA, then the installed one.
        """
        env = {library.ENV_DIRS: os.pathsep.join([str(self.first), "", str(self.second)]),
               "ACTDATA": str(self.second.parent)}
        with mock.patch.dict(os.environ, env):
            dirs = library.default_dirs()
        self.assertEqual([self.first, self.second, self.second.parent / "forcefields"], dirs[:3])
        self.assertTrue(str(dirs[-1]).endswith(os.path.join("share", "act", "forcefields")))
        self.assertEqual(len(dirs), len(set(dirs)))


class LibraryApiTest(unittest.TestCase):
    """!
    @brief The installed force fields in the requests of the page.
    """

    def setUp(self):
        """!
        @brief An application with a library of two force fields.
        """
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        default = self.dir / "default.xml"
        default.write_bytes(FF)
        self.lib = self.dir / "forcefields"
        write(self.lib / "Water" / "SPC.xml", forcefield("Berendsen1981a"))
        write(self.lib / "Inorganic" / "N2-Cheung1975a.xml", forcefield())
        self.settings = Settings(data_dir=self.dir / "data", token="secret-token",
                                 default_forcefield=default, forcefield_dirs=[self.lib])
        self.run = FakeRun()
        self.app = create_app(self.settings, run=self.run, program_info=fake_program_info)
        self.client = TestClient(self.app)
        self.headers = {TOKEN_HEADER: "secret-token"}

    def tearDown(self):
        """!
        @brief Stop the jobs and remove the files.
        """
        self.run.release.set()
        self.app.state.jobs.shutdown()
        self._tmp.cleanup()

    def test_session_lists_them(self):
        """!
        @brief The page learns the installed force fields, without their paths.
        """
        session = self.client.get("/api/session", headers=self.headers).json()
        self.assertEqual(["Inorganic/N2-Cheung1975a", "Water/SPC"],
                         [ff["id"] for ff in session["forcefields"]])
        self.assertEqual("Berendsen1981a", session["forcefields"][1]["reference"])
        self.assertNotIn(str(self.lib), str(session["forcefields"]))

    def test_request_with_one(self):
        """!
        @brief A calculation with an installed force field gets its file.
        """
        answer = self.client.post("/api/jobs", headers=self.headers, json={
            "task": "minimize", "molecule": {"smiles": "O"},
            "forcefield": {"library": "Water/SPC"}})
        self.assertEqual(200, answer.status_code, answer.text)
        for _ in range(500):
            job = self.client.get("/api/jobs/" + answer.json()["id"], headers=self.headers).json()
            if job["status"] in ("finished", "failed", "cancelled"):
                break
            time.sleep(0.01)
        self.assertEqual(self.lib / "Water" / "SPC.xml", self.run.calls[0]["options"]["ff"])

    def test_wrong_requests(self):
        """!
        @brief A force field that is not installed, or two at once, is refused.
        """
        for ref in ({"library": "Water/TIP3P"}, {"library": "../default"}, {"library": 3},
                    {"library": "Water/SPC", "default": True, "file": "x"}):
            answer = self.client.post("/api/jobs", headers=self.headers, json={
                "task": "minimize", "molecule": {"smiles": "O"}, "forcefield": ref})
            self.assertEqual(400, answer.status_code, ref)
            self.assertTrue(answer.json()["detail"])
        self.assertEqual([], self.run.calls)


if __name__ == "__main__":
    unittest.main()
