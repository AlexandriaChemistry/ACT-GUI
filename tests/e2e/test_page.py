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
@brief Tests of the page in a real browser, with the real program.

Firefox is driven without a window through selenium. The tests run when
selenium is installed and ACT_ALEXANDRIA points to the alexandria program;
they are skipped otherwise. With ACT_GUI_SHOTS set to a directory, pictures
of the page are saved there at the main steps.
"""
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.support.ui import WebDriverWait
    HAVE_SELENIUM = True
except ImportError:
    HAVE_SELENIUM = False

## The program, if it is given
ALEXANDRIA = os.environ.get("ACT_ALEXANDRIA", "")
## Where to save pictures of the page, if anywhere
SHOTS = os.environ.get("ACT_GUI_SHOTS", "")


@unittest.skipUnless(HAVE_SELENIUM and ALEXANDRIA, "selenium or ACT_ALEXANDRIA is missing")
class PageTest(unittest.TestCase):
    """!
    @brief A user loads a molecule and runs the four calculations.
    """

    @classmethod
    def setUpClass(cls):
        """!
        @brief Start the server in a thread and a browser without a window.
        """
        import act  # pylint: disable=import-outside-toplevel
        import uvicorn  # pylint: disable=import-outside-toplevel
        from act_gui.app import create_app  # pylint: disable=import-outside-toplevel
        from act_gui.settings import Settings  # pylint: disable=import-outside-toplevel
        cls._executable = act.config.executable
        act.config.executable = ALEXANDRIA
        cls._tmp = tempfile.TemporaryDirectory()
        cls.settings = Settings(data_dir=Path(cls._tmp.name), token="e2e-token")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        config = uvicorn.Config(create_app(cls.settings), host="127.0.0.1", port=cls.port,
                                log_level="error")
        cls.server = uvicorn.Server(config)
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        for _ in range(100):
            if cls.server.started:
                break
            time.sleep(0.05)
        options = Options()
        options.add_argument("-headless")
        options.add_argument("--width=1440")
        options.add_argument("--height=1000")
        cls.browser = webdriver.Firefox(options=options)
        cls.base = "http://127.0.0.1:%d" % cls.port

    @classmethod
    def tearDownClass(cls):
        """!
        @brief Stop the browser and the server.
        """
        import act  # pylint: disable=import-outside-toplevel
        cls.browser.quit()
        cls.server.should_exit = True
        cls.thread.join(timeout=10)
        act.config.executable = cls._executable
        cls._tmp.cleanup()

    def wait(self, condition, seconds=60):
        """!
        @brief Wait until a condition on the page holds.
        @param condition A function of the browser that returns something true.
        @param seconds   The longest time to wait.
        @return What the function returned.
        """
        return WebDriverWait(self.browser, seconds).until(condition)

    def text(self, selector):
        """!
        @brief The text of an element.
        @param selector The CSS selector.
        @return The text.
        """
        return self.browser.find_element(By.CSS_SELECTOR, selector).text

    def click(self, selector):
        """!
        @brief Click an element.
        @param selector The CSS selector.
        """
        self.browser.find_element(By.CSS_SELECTOR, selector).click()

    def shot(self, name):
        """!
        @brief Save a picture of the page, if pictures are asked for.
        @param name The name of the picture.
        """
        if SHOTS:
            os.makedirs(SHOTS, exist_ok=True)
            self.browser.save_screenshot(os.path.join(SHOTS, name + ".png"))

    def errors(self):
        """!
        @brief The error that the page shows above the workspace.
        @return The text, empty if there is none.
        """
        box = self.browser.find_element(By.ID, "alert")
        return "" if box.get_attribute("hidden") else box.text

    def open_page(self):
        """!
        @brief Open the page with the key, as the user does the first time.
        """
        self.browser.get(self.base + "/?token=e2e-token")
        self.wait(lambda b: "version" in b.find_element(By.ID, "status-text").text)

    def test_1_page_needs_the_key(self):
        """!
        @brief Without the key the page says that it is not connected.
        """
        self.browser.delete_all_cookies()
        self.browser.get(self.base + "/")
        self.wait(lambda b: b.find_element(By.ID, "status-text").text == "Not connected")
        self.assertIn("key", self.errors())

    def test_2_molecule_workspace(self):
        """!
        @brief Load acetone and run the charges, the minimization, the
        normal modes and a simulation, each with its results on the page.
        """
        self.open_page()
        self.assertEqual("", self.errors())
        self.shot("01-start")
        # Load the molecule that is in the input line
        self.click("#load")
        self.wait(lambda b: len(b.find_elements(By.CSS_SELECTOR, "#qtable tr.pick")) == 10)
        self.assertEqual("", self.errors())
        chips = self.text("#molchips")
        self.assertIn("C3H6O", chips)
        self.assertIn("10 atoms and 10 shells", chips)
        self.assertIn("net charge 0 e", chips)
        # The viewer drew something: there is a canvas with a size
        canvas = self.wait(lambda b: b.find_element(By.CSS_SELECTOR, "#gl canvas"))
        self.assertGreater(canvas.size["width"], 300)
        # The charges add up to zero
        cells = self.browser.find_elements(By.CSS_SELECTOR, "#qtable tr.pick td.n")
        total = sum(float(c.text.replace("−", "-")) for c in cells)
        self.assertAlmostEqual(0.0, total, places=2)
        # Only the panel of the selected tab is shown
        self.assertTrue(self.browser.find_element(By.ID, "tab-charges").is_displayed())
        for other in ("tab-minimize", "tab-modes", "tab-simulate"):
            self.assertFalse(self.browser.find_element(By.ID, other).is_displayed(), other)
        self.shot("02-charges")
        # A row selects an atom
        self.click("#qtable tr.pick td")
        self.assertIn("sel", self.browser.find_element(
            By.CSS_SELECTOR, "#qtable tr.pick").get_attribute("class"))

        # Minimize
        self.click('#tasks button[data-tab="minimize"]')
        self.click("#run-min")
        self.wait(lambda b: b.find_element(By.ID, "min-pill").text == "Converged")
        self.assertEqual("", self.errors())
        self.assertRegex(self.text("#rmsd"), r"^0\.\d{3} Å$")
        self.assertIn("kJ/mol", self.text("#dE"))
        self.assertIn("Potential energy", self.text("#etable"))
        self.assertEqual("Minimized and original", self.text("#scene-title"))
        self.shot("03-minimize")

        # Normal modes
        self.click('#tasks button[data-tab="modes"]')
        self.click("#run-nma")
        self.wait(lambda b: b.find_element(By.ID, "nma-pill").text == "24 modes")
        self.assertEqual("", self.errors())
        self.assertEqual(24, len(self.browser.find_elements(By.CSS_SELECTOR, "#modetable tr.pick")))
        self.assertTrue(self.browser.find_elements(By.CSS_SELECTOR, "#irplot svg path"))
        # The style sheet shows this title in capitals
        self.assertIn("thermochemistry at 298.15 k", self.text("#thermo-title").lower())
        self.assertIn("Zero point energy", self.text("#thermo"))
        self.assertRegex(self.text("#scene-title"), r"^Vibration at \d+ cm⁻¹$")
        # Another mode
        self.click("#modetable tr.pick td")
        self.assertIn("sel", self.browser.find_element(
            By.CSS_SELECTOR, "#modetable tr.pick").get_attribute("class"))
        self.shot("04-modes")

        # Simulate
        self.click('#tasks button[data-tab="simulate"]')
        steps = self.browser.find_element(By.ID, "nsteps")
        steps.clear()
        steps.send_keys("400")
        self.click("#run-sim")
        self.wait(lambda b: b.find_element(By.ID, "scene-title").text == "Trajectory")
        self.assertEqual("", self.errors())
        self.assertTrue(self.browser.find_elements(By.CSS_SELECTOR, "#eplot svg path"))
        self.assertIn("ps", self.text("#simtime"))
        self.click('#eseries button[data-v="T"]')
        self.assertIn("Temperature (K)", self.browser.find_element(
            By.CSS_SELECTOR, "#eplot svg").get_attribute("aria-label"))
        self.shot("05-simulate")

    def test_3_wrong_smiles_gives_a_message(self):
        """!
        @brief A string that is no molecule gives the message of the program.
        """
        self.open_page()
        box = self.browser.find_element(By.ID, "smiles")
        box.clear()
        box.send_keys("C(C")
        self.click("#load")
        self.wait(lambda b: not b.find_element(By.ID, "alert").get_attribute("hidden"))
        self.assertIn("SMILES", self.errors())
        self.shot("06-error")

    def test_4_no_messages_in_the_console(self):
        """!
        @brief The content security policy blocks nothing that the page needs.
        """
        self.open_page()
        self.click("#load")
        self.wait(lambda b: len(b.find_elements(By.CSS_SELECTOR, "#qtable tr.pick")) == 10)
        # A style attribute that the policy refused would leave the bars without width
        width = self.browser.execute_script(
            "return document.querySelector('#qtable .qbar i').style.width")
        self.assertTrue(width.endswith("%"))
        blocked = self.browser.execute_script(
            "return window.__cspViolations ? window.__cspViolations.length : 0")
        self.assertEqual(0, blocked)


if __name__ == "__main__":
    unittest.main()
