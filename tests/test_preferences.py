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
@brief Tests of the preferences of the user, such as the screen mode: what
is accepted, how they are kept in the file of the settings next to those
of the cluster, and how the page gets them.
"""
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from act_gui import cluster, preferences
from act_gui.app import TOKEN_HEADER, create_app
from act_gui.settings import Settings
from tests.test_api import fake_program_info
from tests.test_jobs import FakeRun


class PreferencesTest(unittest.TestCase):
    """!
    @brief The preferences and their file.
    """

    def setUp(self):
        """!
        @brief A directory for the file.
        """
        self._tmp = tempfile.TemporaryDirectory()
        self.file = Path(self._tmp.name) / "config" / "settings.json"

    def tearDown(self):
        """!
        @brief Remove the file.
        """
        self._tmp.cleanup()

    def test_themes(self):
        """!
        @brief Three screen modes, light first, which is the default.
        """
        self.assertEqual(("light", "dark", "hippy"), preferences.THEMES)
        self.assertEqual({"theme": "light"}, preferences.DEFAULTS)
        for theme in preferences.THEMES:
            self.assertEqual({"theme": theme}, preferences.parse({"theme": theme}))

    def test_what_is_refused(self):
        """!
        @brief Only known keys with known values.
        """
        for wrong in ({"theme": "pink"}, {"theme": 1}, {"theme": None}, {"colour": "dark"},
                      {}, [], "dark", None):
            with self.assertRaises(preferences.PreferenceError, msg=repr(wrong)):
                preferences.parse(wrong)

    def test_save_and_load(self):
        """!
        @brief The preferences are kept under their own key, next to the
        settings of the cluster, which stay as they are.
        """
        self.assertEqual(preferences.DEFAULTS, preferences.load(self.file))
        self.assertEqual(preferences.DEFAULTS, preferences.load(None))
        chosen = cluster.parse({"server": "csb", "user": "u", "workdir": "w"})
        cluster.save(self.file, chosen)
        preferences.save(self.file, {"theme": "hippy"})
        content = json.loads(self.file.read_text())
        self.assertEqual({"theme": "hippy"}, content["appearance"])
        self.assertEqual(chosen, cluster.load(self.file))
        self.assertEqual({"theme": "hippy"}, preferences.load(self.file))
        # Saving the cluster again keeps the preferences
        cluster.save(self.file, None)
        self.assertEqual({"appearance": {"theme": "hippy"}}, json.loads(self.file.read_text()))
        self.assertEqual(0o600, self.file.stat().st_mode & 0o777)

    def test_file_that_cannot_be_used(self):
        """!
        @brief Preferences that are not possible, or a file that is no JSON,
        give the defaults: they never stop the interface.
        """
        self.file.parent.mkdir(parents=True)
        for text in ('{"appearance": {"theme": "pink"}}', '{"appearance": 3}', "not json", "[1]"):
            self.file.write_text(text)
            self.assertEqual(preferences.DEFAULTS, preferences.load(self.file), text)


class PreferencesApiTest(unittest.TestCase):
    """!
    @brief The preferences through the interface of the server.
    """

    def setUp(self):
        """!
        @brief A directory for the file.
        """
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
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
        options = {"data_dir": self.dir / "data", "token": "t", "config_file": self.file}
        options.update(more)
        app = create_app(Settings(**options), run=FakeRun(), program_info=fake_program_info)
        self.apps.append(app)
        return TestClient(app)

    def test_theme_is_kept_between_runs(self):
        """!
        @brief The chosen screen mode is in the file, in the session and in
        the page itself when the interface starts again.
        """
        client = self.client()
        self.assertEqual(401, client.get("/api/preferences").status_code)
        self.assertEqual({"theme": "light"},
                         client.get("/api/preferences", headers=self.headers).json())
        session = client.get("/api/session", headers=self.headers).json()
        self.assertEqual({"theme": "light"}, session["preferences"])
        self.assertIs(True, session["keeps_preferences"])
        self.assertIn('data-theme="light"', client.get("/", headers=self.headers).text)
        # Changing needs the key in the header
        self.assertEqual(401, client.put("/api/preferences", json={"theme": "dark"}).status_code)
        answer = client.put("/api/preferences", headers=self.headers, json={"theme": "dark"})
        self.assertEqual(200, answer.status_code, answer.text)
        self.assertEqual({"theme": "dark"}, answer.json())
        self.assertEqual({"appearance": {"theme": "dark"}}, json.loads(self.file.read_text()))
        again = self.client()
        self.assertEqual({"theme": "dark"},
                         again.get("/api/session", headers=self.headers).json()["preferences"])
        page = again.get("/", headers=self.headers).text
        self.assertIn('<html lang="en" data-theme="dark">', page)
        self.assertEqual(1, page.count("data-theme="))

    def test_what_is_refused(self):
        """!
        @brief Unknown modes and bodies that are no JSON are refused and
        change nothing.
        """
        client = self.client()
        for body in ({"theme": "pink"}, {"theme": "dark", "more": 1}, [1], "dark"):
            self.assertEqual(400, client.put("/api/preferences", headers=self.headers,
                                             json=body).status_code, body)
        self.assertEqual(400, client.put(
            "/api/preferences", headers=dict(self.headers, **{"Content-Type": "application/json"}),
            content=b"{not json").status_code)
        self.assertFalse(self.file.exists())

    def test_without_a_file_and_on_a_server(self):
        """!
        @brief Without a file the choice lasts as long as the program. A
        server for several users keeps no choice of one user: the page
        keeps it in the browser.
        """
        client = self.client(config_file=None)
        self.assertEqual({"theme": "hippy"}, client.put(
            "/api/preferences", headers=self.headers, json={"theme": "hippy"}).json())
        self.assertEqual({"theme": "hippy"},
                         client.get("/api/preferences", headers=self.headers).json())
        self.assertFalse(self.file.exists())
        shared = self.client(mode="server")
        session = shared.get("/api/session", headers=self.headers).json()
        self.assertIs(False, session["keeps_preferences"])
        self.assertEqual({"theme": "light"}, session["preferences"])
        self.assertEqual(404, shared.put("/api/preferences", headers=self.headers,
                                         json={"theme": "dark"}).status_code)
        self.assertFalse(self.file.exists())


if __name__ == "__main__":
    unittest.main()
