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
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

try:
    from selenium import webdriver
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.common.by import By
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.support.ui import Select, WebDriverWait
    HAVE_SELENIUM = True
except ImportError:
    HAVE_SELENIUM = False

## The program, if it is given
ALEXANDRIA = os.environ.get("ACT_ALEXANDRIA", "")
## Where to save pictures of the page, if anywhere
SHOTS = os.environ.get("ACT_GUI_SHOTS", "")
## The directory src/act/testdata of the source of ACT, if it is given
TESTDATA = os.environ.get("ACT_TESTDATA", "")


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
        from tests.test_alexandria import require_forcefield  # pylint: disable=import-outside-toplevel
        forcefield = require_forcefield()
        cls._executable = act.config.executable
        act.config.executable = ALEXANDRIA
        cls._tmp = tempfile.TemporaryDirectory()
        # The settings of the user go to a file of the test, and a small
        # program stands in for ssh: no test logs in anywhere
        from tests.test_cluster import FakeServer  # pylint: disable=import-outside-toplevel
        cls.cluster = FakeServer(Path(cls._tmp.name) / "cluster")
        cls.config = Path(cls._tmp.name) / "config" / "settings.json"
        cls.settings = Settings(data_dir=Path(cls._tmp.name), token="e2e-token",
                                config_file=cls.config, default_forcefield=forcefield,
                                ssh_program=str(cls.cluster.program))
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
        # Files that the page saves go to a directory of the test, without
        # a question and without the viewer for PDF files of the browser
        cls.downloads = Path(cls._tmp.name) / "downloads"
        cls.downloads.mkdir()
        options.set_preference("browser.download.folderList", 2)
        options.set_preference("browser.download.dir", str(cls.downloads.resolve()))
        options.set_preference("browser.download.useDownloadDir", True)
        options.set_preference("browser.download.alwaysOpenPanel", False)
        options.set_preference("browser.helperApps.neverAsk.saveToDisk",
                               "application/pdf,application/zip")
        options.set_preference("pdfjs.disabled", True)
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

    def saved_pdf(self, button, name):
        """!
        @brief Press a button that saves a plot and check the file. Without
        plotXVG the button is not shown, and that is checked instead.
        @param button The identifier of the button.
        @param name   The name the file should get.
        """
        from act_gui import plots  # pylint: disable=import-outside-toplevel
        element = self.browser.find_element(By.ID, button)
        if not plots.available():
            self.assertFalse(element.is_displayed())
            return
        self.assertTrue(element.is_displayed())
        target = self.downloads / name
        if target.exists():
            target.unlink()
        element.click()
        self.confirm_save(name)

        def complete(_browser):
            return (target.is_file() and target.stat().st_size > 2000
                    and not list(self.downloads.glob("*.part")))
        self.wait(complete, 60)
        self.assertTrue(target.read_bytes().startswith(b"%PDF"))
        self.assertEqual("", self.errors())
        self.assertEqual("Save as PDF", element.text)

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
        @brief Save a picture of the page, if pictures are asked for. The
        message that comes and goes at the bottom is not in the picture,
        which can then go into the manual as it is.
        @param name The name of the picture.
        """
        if SHOTS:
            os.makedirs(SHOTS, exist_ok=True)
            self.browser.execute_script(
                "const t = document.getElementById('toast');"
                "t.classList.remove('on'); t.style.transition = 'none';")
            self.browser.save_screenshot(os.path.join(SHOTS, name + ".png"))
            self.browser.execute_script("document.getElementById('toast').style.transition = '';")

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

    def test_1b_logo(self):
        """!
        @brief The header has the logo of ACT, and the page its icon.
        """
        self.open_page()
        script = self.browser.execute_script
        self.wait(lambda b: script("const i = document.querySelector('.brand img.mark');"
                                   "return i.complete && i.naturalWidth > 0"))
        self.assertEqual(128, script("return document.querySelector('.brand img.mark').naturalWidth"))
        self.assertEqual(32, script("return document.querySelector('.brand img.mark').width"))
        status = self.browser.execute_async_script("const done = arguments[0];"
                        "fetch('/favicon.ico').then(r => done(r.status), () => done(0))")
        self.assertEqual(200, status)
        self.assertEqual("", self.errors())

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
        for other in ("tab-minimize", "tab-modes", "tab-simulate", "tab-openmm"):
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
        # The spectrum on paper, and as data
        self.saved_pdf("ir-pdf", "nma-ir.pdf")
        spectrum = self.saved_file("#ir-xvg", "nma-ir.xvg", "")
        points = [line.split() for line in spectrum.splitlines()
                  if line.strip() and line.lstrip()[0] not in "#@&"]
        self.assertGreater(len(points), 100)
        self.assertTrue(all(len(point) == 2 for point in points))
        # The wavenumbers from high to low, on the screen and on paper

        def tick(label):
            for text in self.browser.find_elements(By.CSS_SELECTOR, '#irplot svg text[text-anchor="middle"]'):
                if text.text == label:
                    return float(text.get_attribute("x"))
            self.fail("The spectrum has no tick %s" % label)
            return 0.0
        self.assertLess(tick("0"), tick("3000"))
        flip = self.browser.find_element(By.ID, "ir-flip")
        self.assertEqual("false", flip.get_attribute("aria-pressed"))
        flip.click()
        self.assertEqual("true", flip.get_attribute("aria-pressed"))
        self.assertGreater(tick("0"), tick("3000"))
        # The lines under the spectrum still select a mode
        self.click("#modetable tr.pick td")
        self.assertRegex(self.text("#scene-title"), r"^Vibration at \d+ cm⁻¹$")
        self.shot("04b-modes-reversed")
        self.saved_pdf("ir-pdf", "nma-ir-reversed.pdf")
        # The choice is remembered when the page is opened again
        self.assertEqual("1", self.browser.execute_script(
            "return localStorage.getItem('act-spectrum-reverse')"))
        flip.click()
        self.assertLess(tick("0"), tick("3000"))

        # Simulate
        self.click('#tasks button[data-tab="simulate"]')
        steps = self.browser.find_element(By.ID, "nsteps")
        steps.clear()
        steps.send_keys("400")
        # Nothing to save before there is a simulation
        self.assertFalse(self.browser.find_element(By.ID, "e-pdf").is_displayed())
        self.click("#run-sim")
        self.wait(lambda b: b.find_element(By.ID, "scene-title").text == "Trajectory")
        self.assertEqual("", self.errors())
        self.assertTrue(self.browser.find_elements(By.CSS_SELECTOR, "#eplot svg path"))
        self.assertIn("ps", self.text("#simtime"))
        self.click('#eseries button[data-v="T"]')
        self.assertIn("Temperature (K)", self.browser.find_element(
            By.CSS_SELECTOR, "#eplot svg").get_attribute("aria-label"))
        self.shot("05-simulate")
        # The plot on paper has the series that is on the screen
        self.saved_pdf("e-pdf", "simulate-e-TEMPERATURE.pdf")

    def test_2b_openmm_package(self):
        """!
        @brief The archive for OpenMM of the molecule that was loaded is
        saved, with the settings of the form.
        """
        import zipfile  # pylint: disable=import-outside-toplevel
        self.open_page()
        # Nothing to make before a molecule is loaded
        self.assertFalse(self.browser.find_element(By.ID, "run-omm").is_enabled())
        self.click("#load")
        self.wait(lambda b: len(b.find_elements(By.CSS_SELECTOR, "#qtable tr.pick")) == 10)
        self.click('#tasks button[data-tab="openmm"]')
        self.assertTrue(self.browser.find_element(By.ID, "tab-openmm").is_displayed())
        self.assertEqual("Structure", self.text("#scene-title"))
        steps = self.browser.find_element(By.ID, "omm-steps")
        steps.clear()
        steps.send_keys("2500")
        self.shot("05b-openmm")
        target = self.downloads / "act-openmm.zip"
        if target.exists():
            target.unlink()
        self.click("#run-omm")
        self.confirm_save("act-openmm.zip")
        self.wait(lambda b: b.find_element(By.ID, "omm-pill").text == "Saved")
        self.wait(lambda b: target.is_file() and not list(self.downloads.glob("*.part")))
        self.assertEqual("", self.errors())
        with zipfile.ZipFile(target) as archive:
            self.assertEqual(5, len(archive.namelist()))
            self.assertIn("= 2500\n", archive.read("act-openmm/simulation.dat").decode())
            self.assertIn("<ForceField", archive.read("act-openmm/openmm.xml").decode())
            # The atoms of acetone; OpenMM adds the shells
            atoms = [line for line in archive.read("act-openmm/conf.pdb").decode().splitlines()
                     if line.startswith(("ATOM", "HETATM"))]
            self.assertEqual(10, len(atoms))

    @unittest.skipUnless(os.path.isdir(TESTDATA), "ACT_TESTDATA is not set")
    def test_2b2_forcefield_file_of_molecule(self):
        """!
        @brief The force field file for a molecule is opened with a button,
        is then the choice, and can be chosen again after another.
        """
        self.open_page()
        self.assertTrue(self.browser.find_element(By.ID, "ffopen").is_displayed())
        choice = Select(self.browser.find_element(By.ID, "ffsel"))
        self.assertFalse(self.browser.find_element(By.CSS_SELECTOR, '#ffsel option[value="upload"]').is_enabled())
        self.browser.find_element(By.ID, "fffile").send_keys(str(Path(TESTDATA) / "forcefields" / "ACS-g1.xml"))
        self.assertEqual("ACS-g1.xml", choice.first_selected_option.text)
        self.click("#load")
        self.wait(lambda b: "ACS-g1.xml" in b.find_element(By.ID, "molchips").text)
        choice.select_by_value("default")
        self.click("#load")
        self.wait(lambda b: "ACS-g1.xml" not in b.find_element(By.ID, "molchips").text
                  and "force field" in b.find_element(By.ID, "molchips").text)
        choice.select_by_value("upload")
        self.click("#load")
        self.wait(lambda b: "ACS-g1.xml" in b.find_element(By.ID, "molchips").text)
        self.assertEqual("", self.errors())

    @unittest.skipUnless(os.path.isdir(TESTDATA), "ACT_TESTDATA is not set")
    def test_2c_dimer_scan(self):
        """!
        @brief The interaction energy of a water dimer at three distances,
        with the atoms that are closest chosen; a click on a point shows the
        minimized dimer, and the energies can be saved.
        """
        self.open_page()
        # A single compound cannot be scanned
        self.click("#load")
        self.wait(lambda b: b.find_elements(By.CSS_SELECTOR, "#qtable tr.pick"))
        self.click('#tasks button[data-tab="scan"]')
        self.assertIn("needs a structure of two compounds", self.text("#scan-note"))
        self.assertFalse(self.browser.find_element(By.ID, "run-scan").is_enabled())
        self.browser.find_element(By.ID, "molfile").send_keys(
            str(Path(TESTDATA) / "molecules" / "water_dimer.pdb"))
        self.wait(lambda b: "compounds of 3 and 3 atoms" in b.find_element(By.ID, "scan-note").text)
        # The closest atoms, a hydrogen of the first and the oxygen of the second
        first = Select(self.browser.find_element(By.ID, "scan-atom1"))
        second = Select(self.browser.find_element(By.ID, "scan-atom2"))
        self.assertEqual(["1", "2", "3"], [o.get_attribute("value") for o in first.options])
        self.assertEqual(["4", "5", "6"], [o.get_attribute("value") for o in second.options])
        self.assertEqual(("3", "4"), (first.first_selected_option.get_attribute("value"),
                                      second.first_selected_option.get_attribute("value")))
        for field, value in (("scan-rmin", "0.25"), ("scan-rmax", "0.35"), ("scan-step", "0.05")):
            box = self.browser.find_element(By.ID, field)
            box.clear()
            box.send_keys(value)
        self.click("#run-scan")
        self.wait(lambda b: b.find_element(By.ID, "scan-pill").text in ("Done", "Failed"), 300)
        self.assertEqual("Done", self.text("#scan-pill"), self.errors())
        self.assertIn("3 distances", self.text("#scan-plotnote"))
        # A click on the point at the shortest distance shows that structure
        self.browser.execute_script("document.getElementById('scanplot').scrollIntoView({ block: 'center' })")
        place = self.browser.execute_script("""
            const svg = document.querySelector('#scanplot svg'), box = svg.getBoundingClientRect();
            const scale = box.width / svg.viewBox.baseVal.width;
            const path = [...svg.querySelectorAll('g path')].filter(p => p.getAttribute('stroke-linecap') === 'round')[0];
            const [x, y] = path.getAttribute('d').slice(1).split('h')[0].split(' ').map(Number);
            return [x * scale - box.width / 2, y * scale - box.height / 2];""")
        ActionChains(self.browser).move_to_element_with_offset(
            self.browser.find_element(By.CSS_SELECTOR, "#scanplot svg"),
            int(round(place[0])), int(round(place[1]))).click().perform()
        self.wait(lambda b: "Minimized at 0.250 nm" in b.find_element(By.ID, "scene-title").text)
        self.assertEqual("", self.errors())
        self.shot("05c-dimer-scan")
        saved = self.saved_file("#scan-xvg", "dimer-scan.xvg", "@")
        self.assertIn('legend "EPOT"', saved)
        self.assertEqual(3, len([line for line in saved.splitlines() if line and line[0] not in "#@"]))

    @unittest.skipUnless(os.path.isdir(TESTDATA), "ACT_TESTDATA is not set")
    def test_2d_second_virial(self):
        """!
        @brief The second virial coefficient of a water dimer with SPC/E at
        three temperatures, here; a single compound cannot have one.
        """
        self.open_page()
        self.click("#load")
        self.wait(lambda b: b.find_elements(By.CSS_SELECTOR, "#qtable tr.pick"))
        self.click('#tasks button[data-tab="b2"]')
        self.assertIn("this structure has 1", self.text("#b2-note"))
        self.assertFalse(self.browser.find_element(By.ID, "run-b2").is_enabled())
        self.browser.find_element(By.ID, "fffile").send_keys(str(Path(TESTDATA) / "forcefields" / "SPCE.xml"))
        self.browser.find_element(By.ID, "molfile").send_keys(str(Path(TESTDATA) / "molecules" / "water_dimer.pdb"))
        self.wait(lambda b: b.find_element(By.ID, "run-b2").is_enabled())
        for field, value in (("b2-t1", "300"), ("b2-t2", "400"), ("b2-dt", "50"), ("b2-maxdimer", "16")):
            box = self.browser.find_element(By.ID, field)
            box.clear()
            box.send_keys(value)
        self.click("#run-b2")
        self.wait(lambda b: b.find_element(By.ID, "b2-pill").text in ("Done", "Failed"), 300)
        self.assertEqual("Done", self.text("#b2-pill"), self.errors())
        rows = self.browser.execute_script(
            "return [...document.querySelectorAll('#b2-table tbody tr')].map(r => r.cells[0].textContent)")
        self.assertEqual(["300", "350", "400"], rows)
        self.assertIn("3 temperatures", self.text("#b2-plotnote"))
        self.assertEqual(["Total", "Classical"], [g.text for g in self.browser.find_elements(
            By.CSS_SELECTOR, "#b2plot g.legend")])
        self.shot("05d-second-virial")
        saved = self.saved_file("#b2-xvg", "B2T.xvg", "")
        self.assertEqual(3, len([line for line in saved.splitlines()
                                 if line.strip() and line.lstrip()[0] not in "#@&"]))

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

    # --- the force field workspace ---

    def open_force_fields(self):
        """!
        @brief Open the page and go to the force field workspace.
        """
        self.open_page()
        self.click('#nav button[data-view="force-field"]')
        self.wait(lambda b: len(b.find_elements(By.CSS_SELECTOR, "#g-types tbody tr")) > 50)
        self.assertEqual("", self.errors())

    def confirm_save(self, name, rename=None):
        """!
        @brief Answer the dialog in which the name of a file that is saved
        is chosen.
        @param name   The name the dialog should propose.
        @param rename Another name to type, None to take the one proposed.
        """
        self.wait(lambda b: b.find_element(By.ID, "save-dialog").get_attribute("open") is not None)
        box = self.browser.find_element(By.ID, "save-name")
        self.assertEqual(name, box.get_attribute("value"))
        if rename:
            box.clear()
            box.send_keys(rename)
        self.click('#save-form button[value="save"]')

    def confirm_cancel(self):
        """!
        @brief Cancel the dialog of a file that is saved: it closes, and no
        file is saved.
        """
        self.wait(lambda b: b.find_element(By.ID, "save-dialog").get_attribute("open") is not None)
        before = sorted(p.name for p in self.downloads.iterdir())
        self.click('#save-form button[value="cancel"]')
        self.wait(lambda b: b.find_element(By.ID, "save-dialog").get_attribute("open") is None)
        self.assertEqual(before, sorted(p.name for p in self.downloads.iterdir()))

    def saved_file(self, selector, name, start, rename=None):
        """!
        @brief Press a button that saves a file, take or change the name
        that the dialog proposes, and check the file.
        @param selector The CSS selector of the button.
        @param name     The name the dialog should propose.
        @param start    What the file starts with.
        @param rename   Another name to type in the dialog, None to take the
        one that is proposed.
        @return The text of the file.
        """
        target = self.downloads / (rename or name)
        if target.exists():
            target.unlink()
        self.click(selector)
        self.confirm_save(name, rename)
        self.wait(lambda b: target.is_file() and target.stat().st_size > 0
                  and not list(self.downloads.glob("*.part")))
        text = target.read_text(errors="replace")
        self.assertTrue(text.startswith(start), text[:80])
        self.assertEqual("", self.errors())
        return text

    def parameter_rows(self):
        """!
        @brief The table of parameters as it is on the page.
        @return For each row the texts of its cells.
        """
        return self.browser.execute_script(
            "return [...document.querySelectorAll('#p-table tbody tr')]"
            ".map(r => [...r.cells].map(c => c.textContent))")

    def show_parameters(self, interaction, find):
        """!
        @brief Choose what the table of parameters shows.
        @param interaction The interaction.
        @param find        The text that the types must contain.
        """
        Select(self.browser.find_element(By.ID, "p-filter")).select_by_value(interaction)
        box = self.browser.find_element(By.ID, "p-search")
        box.clear()
        box.send_keys(find)

    def test_5_generate_a_force_field(self):
        """!
        @brief A force field is made from choices in the form, saved, and
        one of its parameters is changed.
        """
        self.open_force_fields()
        self.assertTrue(self.browser.find_element(By.ID, "fftab-gen").is_displayed())
        for other in ("fftab-geom", "fftab-edit"):
            self.assertFalse(self.browser.find_element(By.ID, other).is_displayed(), other)
        selected = self.text("#g-ntypes")
        self.assertRegex(selected, r"^\d+ of \d+ selected$")
        self.shot("10-ff-start")
        # Without atom types there is nothing to make
        self.click("#g-none")
        self.assertTrue(self.text("#g-ntypes").startswith("0 of"))
        self.assertFalse(self.browser.find_element(By.ID, "run-gen").is_enabled())
        for name in ("hc", "c3", "o1", "cl"):
            self.click('#g-types input[data-name="%s"][data-key="on"]' % name)
        self.assertTrue(self.text("#g-ntypes").startswith("4 of"))
        # Carbon gets a shell; the boxes of types that are not selected are off
        self.click('#g-types input[data-name="c3"][data-key="shell"]')
        self.assertFalse(self.browser.find_element(
            By.CSS_SELECTOR, '#g-types input[data-name="hw"][data-key="shell"]').is_enabled())
        # Another potential brings its own combination rules
        Select(self.browser.find_element(By.ID, "g-vdw")).select_by_value("LJ14_7")
        self.assertIn("Halgren", self.text("#g-vdwhelp"))
        rules = self.browser.execute_script(
            "return [...document.querySelectorAll('#g-vdwrules select')]"
            ".map(s => s.dataset.param + ':' + s.value)")
        self.assertEqual(["sigma:Volumetric", "epsilon:Geometric", "gamma:Arithmetic",
                          "delta:Arithmetic"], rules)
        self.browser.execute_script(
            "const s = document.querySelector('#g-vdwrules select[data-param=\"delta\"]');"
            "s.value = 'Geometric';")
        # The charge model with a second charge on each atom ticks those boxes
        self.click('#g-qmodel button[data-v="P+G"]')
        self.assertTrue(self.browser.find_element(
            By.CSS_SELECTOR, '#g-types input[data-name="cl"][data-key="vsite"]').is_selected())
        self.click('#g-vsites input[data-name="vsite2_halogen_sigma_holes"]')
        self.shot("11-ff-choices")
        self.click("#run-gen")
        self.wait(lambda b: b.find_element(By.ID, "gen-pill").text == "Done")
        self.assertEqual("", self.errors())
        chips = self.text("#ffchips")
        self.assertIn("New force field", chips)
        self.assertIn("polarizable", chips)
        self.assertNotIn("not polarizable", chips)
        made = self.text("#g-inter")
        self.assertIn("LJ14_7", made)
        self.assertIn("delta: Geometric", made)
        self.assertIn("VSITE2", made)
        self.assertIn("shell types 1", self.text("#g-facts"))
        self.browser.execute_script("document.getElementById('g-result').scrollIntoView()")
        self.shot("12-ff-generated")
        # The files
        saved = self.saved_file("#ff-save", "forcefield.xml", "<?xml")
        self.assertIn("<alexandria_chemistry>", saved)
        table = self.saved_file("#g-save-f", "atomtypes.csv", "#atomtype|")
        self.assertEqual(["v2f1", "v2cl1", "v2br1", "v2i1", "hc", "c3", "c3_s", "o1", "cl"],
                         [line.split("|")[0] for line in table.splitlines()[1:]])
        # The name that is proposed can be changed, and a save can be cancelled
        self.saved_file("#g-save-vs", "vsites.json", "{", rename="halogen-sites.json")
        self.click("#g-save-vs")
        self.confirm_cancel()
        self.assertEqual("", self.errors())

        # Its parameters, and a change of one of them
        self.click('#fftabs button[data-tab="edit"]')
        self.assertTrue(self.browser.find_element(By.ID, "fftab-edit").is_displayed())
        self.assertEqual("Valid", self.text("#v-pill"))
        self.assertIn("No problems found", self.text("#v-list"))
        self.show_parameters("ELECTRONEGATIVITYEQUALIZATION", "hc")
        rows = self.parameter_rows()
        self.assertEqual(["hc_z", "chi"], rows[0][:2])
        self.assertEqual(("4", "eV/e", "4", "12"), tuple(rows[0][2:6]))
        self.assertEqual("1 type", self.text("#p-count"))
        self.assertFalse(self.browser.find_element(By.ID, "p-edit").is_displayed())
        self.click("#p-table tbody tr.pick td")
        self.assertTrue(self.browser.find_element(By.ID, "p-edit").is_displayed())
        self.assertIn("hc_z", self.text("#p-what"))
        # Applying without a change does nothing
        self.click("#p-apply")
        self.wait(lambda b: "Nothing was changed" in b.find_element(By.ID, "toast").text)
        value = self.browser.find_element(By.ID, "p-val")
        value.clear()
        value.send_keys("6.25")
        self.shot("13-ff-edit")
        self.click("#p-apply")
        self.wait(lambda b: "(1 change)" in b.find_element(By.ID, "ffchips").text)
        self.assertEqual("", self.errors())
        self.assertEqual("6.25", self.parameter_rows()[0][2])
        self.assertFalse(self.browser.find_element(By.ID, "p-edit").is_displayed())
        self.assertIn("6.25", self.saved_file("#ff-save", "forcefield.xml", "<?xml"))
        # Of a particle type the charge and its width can be changed, not the mass
        self.show_parameters("@particles", "c3_s")
        rows = self.parameter_rows()
        self.assertEqual("c3_s", rows[0][0])
        kinds = [row[1] for row in rows]
        picks = self.browser.execute_script(
            "return [...document.querySelectorAll('#p-table tbody tr')]"
            ".map(r => r.classList.contains('pick'))")
        self.assertEqual([kind != "mass" for kind in kinds], picks)
        self.assertIn("charge", kinds)
        self.browser.execute_script(
            "document.querySelectorAll('#p-table tbody tr')[arguments[0]].click()",
            kinds.index("charge"))
        self.assertTrue(self.browser.find_element(By.ID, "p-edit").is_displayed())
        self.assertIn("Particle type", self.text("#p-what"))
        low = self.browser.find_element(By.ID, "p-min")
        before = float(low.get_attribute("value"))
        low.clear()
        low.send_keys(str(before - 1.5))
        self.click("#p-apply")
        self.wait(lambda b: "(2 changes)" in b.find_element(By.ID, "ffchips").text)
        self.assertEqual("", self.errors())
        charge = self.parameter_rows()[kinds.index("charge")]
        self.assertAlmostEqual(before - 1.5, float(charge[4].replace("\u2212", "-")), places=6)
        self.show_parameters("@particles", "no such type")
        self.assertIn("No type", self.text("#p-empty"))

    def test_5b_virtual_sites_by_picking_atoms(self):
        """!
        @brief Atoms of a molecule are picked, a site is defined on them and
        a force field is made with it.
        """
        import json  # pylint: disable=import-outside-toplevel
        self.open_force_fields()
        self.click('#fftabs button[data-tab="vsite"]')
        self.assertTrue(self.browser.find_element(By.ID, "fftab-vsite").is_displayed())
        self.assertFalse(self.browser.find_element(By.ID, "vs-add").is_enabled())
        box = self.browser.find_element(By.ID, "vs-smiles")
        box.clear()
        box.send_keys("CO")
        self.click("#vs-load")
        self.wait(lambda b: len(b.find_elements(By.CSS_SELECTOR, "#vs-atoms tbody tr")) == 6)
        self.assertEqual("", self.errors())
        rows = self.browser.execute_script(
            "return [...document.querySelectorAll('#vs-atoms tbody tr')]"
            ".map(r => [...r.cells].map(c => c.textContent))")
        self.assertEqual(["", "C1"], rows[0][:2])
        oxygen = [i for i, row in enumerate(rows) if row[1].startswith("O")][0]
        hydroxyl = [i for i, row in enumerate(rows) if row[1].startswith("H")
                    and rows[oxygen][1] in row[3].split(", ")][0]
        types = [rows[i][2] for i in (0, oxygen, hydroxyl)]

        def pick(i):
            self.browser.execute_script(
                "document.querySelectorAll('#vs-atoms tbody tr')[arguments[0]].cells[1].click()", i)

        def kinds():
            return [o.text for o in Select(self.browser.find_element(By.ID, "vs-kind")).options]

        def order(i):
            return self.browser.execute_script(
                "return document.querySelectorAll('#vs-atoms tbody tr')[arguments[0]]"
                ".cells[0].textContent", i)
        # Two atoms: a site on their line
        pick(oxygen)
        self.assertEqual([], kinds())
        pick(0)
        self.assertEqual(["VSITE2", "VSITE2FD"], kinds())
        self.assertIn("line", self.text("#vs-help"))
        self.assertEqual(["vs2a"], [e.text for e in self.browser.find_elements(
            By.CSS_SELECTOR, "#vs-params .name")])
        # A third atom: sites in or out of the plane. An atom that is
        # picked again is let go of.
        pick(hydroxyl)
        self.assertEqual(["1", "2", "3"], [order(i) for i in (oxygen, 0, hydroxyl)])
        self.assertEqual(["VSITE3S", "VSITE3", "VSITE3FD", "VSITE3OUTS", "VSITE3OUT"], kinds())
        pick(0)
        self.assertEqual(["1", "", "2"], [order(i) for i in (oxygen, 0, hydroxyl)])
        self.click("#vs-clear")
        self.assertEqual([], kinds())
        # The lone pairs of the oxygen: carbon, oxygen in the middle, hydrogen
        for atom in (0, oxygen, hydroxyl):
            pick(atom)
        # The default force field has a hydrogen type that a new force field
        # has not: one of its own is proposed, and the user chooses another
        choice = Select(self.browser.find_element(
            By.CSS_SELECTOR, '#vs-atoms select[data-atom="%d"]' % hydroxyl))
        self.assertEqual("hc", choice.first_selected_option.text)
        self.assertIn("ho", [o.text for o in choice.options])
        self.assertNotIn("c3", [o.text for o in choice.options])
        choice.select_by_value("ho")
        # Choosing a type does not let go of the atom
        self.assertEqual("3", order(hydroxyl))
        types = [types[0], types[1], "ho"]
        Select(self.browser.find_element(By.ID, "vs-kind")).select_by_value("VSITE3OUTS")
        self.assertEqual(["vs3outs_a", "vs3outs_c"], [e.text for e in self.browser.find_elements(
            By.CSS_SELECTOR, "#vs-params .name")])
        low = self.browser.find_element(
            By.CSS_SELECTOR, '#vs-params input[data-name="vs3outs_a"][data-part="min"]')
        low.clear()
        low.send_keys("-0.3")
        name = self.browser.find_element(By.ID, "vs-type")
        name.clear()
        name.send_keys("not a name")
        self.click("#vs-add")
        self.wait(lambda b: "name of the site type" in b.find_element(By.ID, "toast").text)
        name.clear()
        name.send_keys("v3lp")
        self.shot("17-ff-vsite")
        self.click("#vs-add")
        self.wait(lambda b: len(b.find_elements(By.CSS_SELECTOR, "#vs-list li button")) == 1)
        listed = self.text("#vs-list")
        self.assertIn("v3lp · VSITE3OUTS · %s" % ", ".join(t + "_b" for t in types), listed)
        self.assertIn("vs3outs_a −0.3 to 0.4", listed)
        saved = json.loads(self.saved_file("#vs-save", "vsites.json", "{"))
        self.assertEqual([{"type": "v3lp", "interaction": "VSITE3OUTS",
                           "atoms": [t + "_b" for t in types],
                           "parameters": {"vs3outs_a": {"min": -0.3, "max": 0.4},
                                          "vs3outs_c": {"min": -12, "max": 12}}}],
                         saved["virtual_sites"])
        # The force field is made with it, if it has the types of these atoms
        self.click('#fftabs button[data-tab="gen"]')
        self.assertIn("1 of your own", self.text("#g-ownsites"))
        self.click("#g-none")
        for atomtype in sorted(set(types) | {"hc"}):
            self.click('#g-types input[data-name="%s"][data-key="on"]' % atomtype)
        self.click("#run-gen")
        self.wait(lambda b: b.find_element(By.ID, "gen-pill").text in ("Done", "Failed"))
        self.assertEqual("", self.errors())
        self.assertIn("VSITE3OUTS", self.text("#g-inter"))
        self.assertIn("v3lp", self.saved_file("#g-save-vs", "vsites.json", "{"))
        # Without one of its atom types the site cannot be made, which is said
        self.click('#g-types input[data-name="%s"][data-key="on"]' % types[1])
        self.click("#run-gen")
        self.wait(lambda b: b.find_element(By.ID, "gen-pill").text == "Failed")
        self.assertIn("v3lp", self.errors())
        self.assertIn("not the bond type", self.errors())
        # Taking the site off the list
        self.click('#fftabs button[data-tab="vsite"]')
        self.click("#vs-list li button")
        self.assertIn("None yet", self.text("#vs-list"))
        self.assertFalse(self.browser.find_element(By.ID, "vs-save").is_displayed())
        # Sites of a file, as this page saves them and gen_ff -vs reads them
        sites = [{"type": "v2f1", "interaction": "VSITE2", "atoms": ["f_b", "hf_b"],
                  "parameters": {"vs2a": {"min": -1, "max": 0.5}}},
                 {"type": "v3ow", "interaction": "VSITE3OUTS", "atoms": ["hw_b", "ow_b", "hw_b"],
                  "parameters": {"vs3outs_a": {"min": -0.3, "max": 0.3},
                                 "vs3outs_c": {"min": -12, "max": 12}}}]
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "my-sites.json"
            good.write_text(json.dumps({"description": "Two sites", "virtual_sites": sites}))
            chooser = self.browser.find_element(By.ID, "vs-file")
            chooser.send_keys(str(good))
            self.wait(lambda b: len(b.find_elements(By.CSS_SELECTOR, "#vs-list li button")) == 2)
            self.assertEqual("", self.errors())
            listed = self.text("#vs-list")
            self.assertIn("v2f1 · VSITE2 · f_b, hf_b · vs2a −1 to 0.5", listed)
            self.assertIn("v3ow · VSITE3OUTS · hw_b, ow_b, hw_b", listed)
            self.assertTrue(self.browser.find_element(By.ID, "vs-save").is_displayed())
            # The same file again adds nothing
            chooser.send_keys(str(good))
            self.wait(lambda b: "were there already" in b.find_element(By.ID, "toast").text)
            self.assertEqual(2, len(self.browser.find_elements(By.CSS_SELECTOR,
                                                               "#vs-list li button")))
            # What is no file with sites is told, and changes nothing
            wrong = {"no-list.json": json.dumps({"sites": sites}),
                     "no-json.json": "<vsites/>",
                     "kind.json": json.dumps({"virtual_sites": [dict(sites[0],
                                                                     interaction="VSITE9")]}),
                     "atoms.json": json.dumps({"virtual_sites": [dict(sites[0],
                                                                      atoms=["f_b"])]}),
                     "bounds.json": json.dumps({"virtual_sites": [dict(
                         sites[0], parameters={"vs2a": {"min": 2, "max": 1}})]})}
            for name, text in wrong.items():
                bad = Path(tmp) / name
                bad.write_text(text)
                chooser.send_keys(str(bad))
                self.wait(lambda b, name=name: name in self.errors())
                self.assertIn("could not be read", self.errors())
            self.assertEqual(2, len(self.browser.find_elements(By.CSS_SELECTOR,
                                                               "#vs-list li button")))

    def test_5c_fixed_parameters(self):
        """!
        @brief Parameters of a file, as actgui read them, are fixed at their
        value in the force field that is made; a file that is not one is
        told, and a parameter that a type does not have stops the generator.
        """
        self.open_force_fields()
        self.click('#fftabs button[data-tab="gen"]')
        # Without the sites of the test before
        self.click('#fftabs button[data-tab="vsite"]')
        while self.browser.find_elements(By.CSS_SELECTOR, "#vs-list li button"):
            self.click("#vs-list li button")
        self.click('#fftabs button[data-tab="gen"]')
        self.click("#g-none")
        for name in ("hc", "c3"):
            self.click('#g-types input[data-name="%s"][data-key="on"]' % name)
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "fixed.csv"
            good.write_text("# interaction|potential|atomtype|parameter|value|unit\n"
                            "VANDERWAALS|WANG_BUCKINGHAM|c3|sigma|0.35|nm\n"
                            "VANDERWAALS|WANG_BUCKINGHAM|oh|sigma|0.3|nm\n")
            chooser = self.browser.find_element(By.ID, "g-fixed-file")
            chooser.send_keys(str(good))
            self.wait(lambda b: "fixed.csv: 2 parameters" in b.find_element(By.ID, "g-fixed").text)
            self.assertEqual("", self.errors())
            # What is no such file is told, and changes nothing
            bad = Path(tmp) / "bad.csv"
            bad.write_text("VANDERWAALS|WANG_BUCKINGHAM|c3|sigma|small|nm\n")
            chooser.send_keys(str(bad))
            self.wait(lambda b: "bad.csv" in self.errors())
            self.assertIn("not a number", self.errors())
            self.assertEqual(1, len(self.browser.find_elements(By.CSS_SELECTOR, "#g-fixed li")))
            self.click("#run-gen")
            self.wait(lambda b: b.find_element(By.ID, "gen-pill").text in ("Done", "Failed"))
            self.assertEqual("", self.errors())
            table = self.saved_file("#g-save-f", "atomtypes.csv", "#atomtype|")
            lines = table.splitlines()
            header = lines[0].split("|")
            rows = {line.split("|")[0]: dict(zip(header, line.split("|"))) for line in lines[1:]}
            self.assertEqual(["hc", "c3"], list(rows))
            self.assertEqual(("0.35", "0.35", "Fixed"),
                             tuple(rows["c3"]["VANDERWAALS:sigma:" + k] for k in ("min", "max", "mut")))
            self.assertEqual("Bounded", rows["hc"]["VANDERWAALS:sigma:mut"])
            # The force field has it so
            self.click('#fftabs button[data-tab="edit"]')
            self.show_parameters("VANDERWAALS", "c3")
            # Only c3 is shown, its name is on its first row
            self.wait(lambda b: self.parameter_rows() and self.parameter_rows()[0][0] == "c3")
            sigma = [r for r in self.parameter_rows() if r[1] == "sigma"][0]
            self.assertEqual("0.35", sigma[2])
            self.assertIn("Fixed", sigma)
            # A parameter that the type does not have
            self.click('#fftabs button[data-tab="gen"]')
            other = Path(tmp) / "kappa.csv"
            other.write_text("VANDERWAALS|WANG_BUCKINGHAM|hc|kappa|1|nm\n")
            chooser.send_keys(str(other))
            self.wait(lambda b: "kappa.csv: 1 parameter" in b.find_element(By.ID, "g-fixed").text)
            self.click("#run-gen")
            self.wait(lambda b: b.find_element(By.ID, "gen-pill").text == "Failed")
            self.assertIn("kappa", self.errors())
        # Taking the files off the list
        while self.browser.find_elements(By.CSS_SELECTOR, "#g-fixed li button"):
            self.click("#g-fixed li button")
        self.assertEqual("", self.text("#g-fixed"))

    def test_6_change_a_force_field_and_use_it(self):
        """!
        @brief The default force field is opened, one parameter is fixed,
        and the molecule workspace computes with the result.
        """
        self.open_force_fields()
        self.click("#ff-default")
        self.wait(lambda b: "particle types" in b.find_element(By.ID, "ffchips").text)
        self.assertEqual("", self.errors())
        # The default cannot be saved: the user did not change it
        self.assertFalse(self.browser.find_element(By.ID, "ff-save").is_displayed())
        self.click('#fftabs button[data-tab="edit"]')
        self.show_parameters("VANDERWAALS", "")
        rows = self.parameter_rows()
        self.assertGreater(len(rows), 10)
        target = [row for row in rows if row[7] == "Bounded"][0]
        index = rows.index(target)
        self.browser.execute_script(
            "document.querySelectorAll('#p-table tbody tr')[arguments[0]].click()", index)
        Select(self.browser.find_element(By.ID, "p-mut")).select_by_value("Fixed")
        self.click("#p-apply")
        self.wait(lambda b: "(1 change)" in b.find_element(By.ID, "ffchips").text)
        self.assertEqual("", self.errors())
        self.assertEqual("Fixed", self.parameter_rows()[index][7])
        self.assertTrue(self.browser.find_element(By.ID, "ff-save").is_displayed())
        self.shot("14-ff-default-changed")
        # Compared with the default: the one parameter that is fixed now
        self.click('#fftabs button[data-tab="diff"]')
        self.assertFalse(self.browser.find_element(By.ID, "fd-result").is_displayed())
        self.click("#run-diff")
        self.wait(lambda b: b.find_element(By.ID, "fd-pill").text == "Done")
        self.assertEqual("", self.errors())

        def chips():
            return self.browser.execute_script(
                "return [...document.querySelectorAll('#fd-chips .chip')].map(c => c.textContent)")

        def differences():
            return self.browser.execute_script(
                "return [...document.querySelectorAll('#fd-table tbody tr')]"
                ".map(r => [...r.cells].map(c => c.textContent))")
        self.assertEqual(["another value 0", "only other bounds or training 1"], chips()[:2])
        self.assertEqual("0", chips()[2].split()[-1])
        self.assertEqual("0", chips()[3].split()[-1])
        self.assertGreater(int(chips()[4].split()[-1]), 100)
        found = differences()
        self.assertEqual(1, len(found))
        self.assertEqual(("VANDERWAALS", target[0] or found[0][1], target[1]), tuple(found[0][:3]))
        self.assertEqual(found[0][3], found[0][4])
        self.assertEqual("", found[0][5])
        self.assertEqual("training: Bounded and Fixed", found[0][7])
        self.shot("14b-ff-compared")
        if os.path.isdir(TESTDATA):
            # With another force field, of which the file is chosen with a
            # button, and then is the choice
            self.assertTrue(self.browser.find_element(By.ID, "fd-open").is_displayed())
            self.browser.find_element(By.ID, "fd-file").send_keys(
                str(Path(TESTDATA) / "forcefields" / "ACS-g1.xml"))
            self.assertEqual("upload", Select(self.browser.find_element(
                By.ID, "fd-other")).first_selected_option.get_attribute("value"))
            self.assertEqual("ACS-g1.xml", self.text('#fd-other option[value="upload"]'))
            self.click("#run-diff")
            self.wait(lambda b: b.find_element(By.ID, "fd-pill").text == "Done")
            self.assertEqual("", self.errors())
            self.assertIn("only in ACS-g1.xml", chips()[2])
            listed = len(differences())
            only = int(chips()[2].split()[-1]) + int(chips()[3].split()[-1])
            self.assertGreater(only, 0)
            self.click("#fd-all")
            self.assertEqual(min(500, listed + only), len(differences()))
            self.click("#fd-all")
        # In the molecule workspace
        self.click('#nav button[data-view="molecule"]')
        choice = self.browser.find_element(By.CSS_SELECTOR, '#ffsel option[value="workspace"]')
        self.assertIn("(1 change)", choice.text)
        Select(self.browser.find_element(By.ID, "ffsel")).select_by_value("workspace")
        self.click("#load")
        self.wait(lambda b: len(b.find_elements(By.CSS_SELECTOR, "#qtable tr.pick")) == 10)
        self.assertEqual("", self.errors())
        self.assertIn("(1 change)", self.text("#molchips"))
        self.click('#tasks button[data-tab="minimize"]')
        self.click("#run-min")
        self.wait(lambda b: b.find_element(By.ID, "min-pill").text == "Converged")
        self.assertEqual("", self.errors())

    @unittest.skipUnless(os.path.isdir(TESTDATA), "ACT_TESTDATA is not set")
    def test_7_geometry_from_a_molprop_file(self):
        """!
        @brief A force field file is opened and gets its bonds from the
        compounds of a molprop file. A force field with other atom types
        than the compounds is left as it is, with a message.
        """
        data = Path(TESTDATA)
        self.open_force_fields()
        self.click('#fftabs button[data-tab="geom"]')
        run = self.browser.find_element(By.ID, "run-geo")
        self.assertFalse(run.is_enabled())
        self.browser.find_element(By.ID, "ff-openfile").send_keys(
            str(data / "forcefields" / "ACS-g1.xml"))
        self.wait(lambda b: "ACS-g1.xml" in b.find_element(By.ID, "ffchips").text)
        self.assertFalse(run.is_enabled())
        self.browser.find_element(By.ID, "mp-file").send_keys(str(data / "molprop" / "molprop.xml"))
        self.wait(lambda b: b.find_element(By.ID, "mp-name").text == "molprop.xml")
        self.assertEqual("", self.errors())
        self.assertTrue(run.is_enabled())
        run.click()
        self.wait(lambda b: b.find_element(By.ID, "geo-pill").text == "Done")
        self.assertEqual("", self.errors())
        self.assertEqual("3", self.text("#geo-ncomp"))
        self.assertIn("BONDS", self.text("#geo-table"))
        self.assertIn("(1 change)", self.text("#ffchips"))
        self.shot("15-ff-geometry")
        # The bond lengths are those of the compounds, and with SQE the
        # electronegativity of hydrogen is fixed
        self.click('#fftabs button[data-tab="edit"]')
        self.show_parameters("BONDS", "c3_b~h_b")
        length = [row for row in self.parameter_rows() if row[1] == "bondlength"][0]
        self.assertAlmostEqual(109.4, float(length[2]), delta=0.1)
        self.assertEqual("24", length[8])
        self.show_parameters("ELECTRONEGATIVITYEQUALIZATION", "h_z")
        chi = [row for row in self.parameter_rows() if row[1] == "chi"][0]
        self.assertEqual("Fixed", chi[7])
        self.shot("16-ff-parameters")
        # A new force field has the atom types of the generator, which the
        # compounds of this file do not have
        self.click('#fftabs button[data-tab="gen"]')
        self.click("#run-gen")
        self.wait(lambda b: b.find_element(By.ID, "gen-pill").text == "Done")
        self.click('#fftabs button[data-tab="geom"]')
        self.click('#geo-qalg button[data-v="EEM"]')
        run.click()
        self.wait(lambda b: b.find_element(By.ID, "geo-pill").text == "No compound could be used")
        self.assertIn("None of the compounds", self.errors())
        self.assertEqual("0", self.text("#geo-ncomp"))
        self.assertIn("New force field", self.text("#ffchips"))
        self.assertNotIn("change", self.text("#ffchips"))
        # The log, while the page still says what went wrong
        log = self.downloads / "geometry_ff.log"
        if log.exists():
            log.unlink()
        self.click("#geo-log")
        self.confirm_save("geometry_ff.log")
        self.wait(lambda b: log.is_file() and log.stat().st_size > 0)
        self.assertIn("Cannot find atomtype", log.read_text(errors="replace"))

    # --- the molprop workspace ---

    @unittest.skipUnless(os.path.isdir(TESTDATA), "ACT_TESTDATA is not set")
    def test_8_molprop_file(self):
        """!
        @brief A molprop file is opened: what is in it, the details of a
        compound and the result of the validation.
        """
        self.open_page()
        self.click('#nav button[data-view="molprop"]')
        self.assertFalse(self.browser.find_element(By.ID, "mpw-content").is_displayed())
        self.browser.find_element(By.ID, "mpw-file").send_keys(
            str(Path(TESTDATA) / "molprop" / "mpdimer.xml"))
        self.wait(lambda b: b.find_element(By.ID, "mpw-content").is_displayed())
        self.assertEqual("", self.errors())
        chips = self.text("#mpw-chips")
        self.assertIn("mpdimer.xml", chips)
        self.assertIn("3 entries", chips)
        self.assertIn("dimers 3", chips)
        self.assertNotIn("monomers", chips)
        self.assertEqual("Valid", self.text("#mpw-vpill"))
        self.assertEqual("3 compounds", self.text("#mpw-count"))
        self.assertIn("InteractionEnergy", self.text("#mpw-props"))
        self.assertEqual("Valid", self.text("#mpw-vpill"))
        self.assertTrue(self.browser.find_element(By.ID, "mpw-pickhint").is_displayed())
        rows = self.browser.find_elements(By.CSS_SELECTOR, "#mpw-table tbody tr")
        self.assertEqual(3, len(rows))
        self.assertIn("1-propanol#ethane", rows[0].text)
        # The details of a compound
        self.assertFalse(self.browser.find_element(By.ID, "mpw-detail").is_displayed())
        self.click("#mpw-table tbody tr.pick td:nth-child(2)")
        self.assertEqual("1-propanol#ethane", self.text("#mpw-dname"))
        self.assertEqual(2, len(self.browser.find_elements(By.CSS_SELECTOR, "#mpw-dfrag tbody tr")))
        self.assertIn("InChI=1S/C2H6", self.text("#mpw-dfrag"))
        self.assertFalse(self.browser.find_element(By.ID, "mpw-pickhint").is_displayed())
        self.shot("20-molprop")
        # The details are under the table, not next to it
        table = self.browser.find_element(By.ID, "mpw-table").rect
        detail = self.browser.find_element(By.ID, "mpw-detail").rect
        self.assertGreater(detail["y"], table["y"] + table["height"] - 1)
        self.assertLess(abs(detail["x"] - table["x"]), 40)
        self.click("#mpw-dclose")
        self.assertFalse(self.browser.find_element(By.ID, "mpw-detail").is_displayed())
        self.assertTrue(self.browser.find_element(By.ID, "mpw-pickhint").is_displayed())
        self.assertEqual(0, len(self.browser.find_elements(By.CSS_SELECTOR, "#mpw-table tr.sel")))
        # A property shows the compounds that have it, and again all of them
        chip = '#mpw-props button[data-prop="InteractionEnergy"]'
        self.click(chip)
        self.assertEqual("true", self.browser.find_element(By.CSS_SELECTOR, chip).get_attribute("aria-pressed"))
        self.assertEqual("InteractionEnergy", self.browser.find_element(By.ID, "mpw-prop").get_attribute("value"))
        self.assertEqual(3, len(self.browser.find_elements(By.CSS_SELECTOR, "#mpw-table tbody tr")))
        self.click(chip)
        self.assertEqual("", self.browser.find_element(By.ID, "mpw-prop").get_attribute("value"))
        self.assertEqual("false", self.browser.find_element(By.CSS_SELECTOR, chip).get_attribute("aria-pressed"))
        # Finding a compound
        box = self.browser.find_element(By.ID, "mpw-search")
        box.send_keys("no such compound")
        self.assertTrue(self.browser.find_element(By.ID, "mpw-empty").is_displayed())
        box.clear()
        box.send_keys("ETHANE")
        self.assertGreaterEqual(len(self.browser.find_elements(By.CSS_SELECTOR, "#mpw-table tbody tr")), 1)
        # Another file takes its place
        self.browser.find_element(By.ID, "mpw-file").send_keys(
            str(Path(TESTDATA) / "molprop" / "molprop.xml"))
        self.wait(lambda b: "molprop.xml" in b.find_element(By.ID, "mpw-chips").text)
        self.assertIn("monomers 3", self.text("#mpw-chips"))
        self.assertFalse(self.browser.find_element(By.ID, "mpw-detail").is_displayed())
        # A file that is no molprop file is refused with a message
        self.browser.find_element(By.ID, "mpw-file").send_keys(
            str(Path(TESTDATA) / "forcefields" / "ACS-g1.xml"))
        self.wait(lambda b: "not" in self.errors())
        self.assertIn("molprop", self.errors())
        self.assertIn("monomers 3", self.text("#mpw-chips"))

    @unittest.skipUnless(os.path.isdir(TESTDATA), "ACT_TESTDATA is not set")
    def test_9_compare_a_force_field_with_a_molprop_file(self):
        """!
        @brief A force field is compared with the interaction energies in
        a molprop file: the statistics, the plot and the files.
        """
        data = Path(TESTDATA)
        self.open_page()
        self.click('#nav button[data-view="molprop"]')
        self.assertFalse(self.browser.find_element(By.ID, "ev-card").is_displayed())
        self.browser.find_element(By.ID, "mpw-file").send_keys(str(data / "molprop" / "mpdimer.xml"))
        self.wait(lambda b: b.find_element(By.ID, "ev-card").is_displayed())
        # A file with dimers offers the properties of dimers, the first is ticked
        offered = self.browser.execute_script(
            "return [...document.querySelectorAll('#ev-observables input[type=checkbox]')]"
            ".map(b => b.dataset.name + (b.checked ? '*' : ''))")
        self.assertEqual("inter*", offered[0])
        self.assertIn("elec", offered)
        self.assertNotIn("epot", offered)
        self.click('#ev-observables input[data-name="elec"]')
        # The force field of the test data, since the default is for other
        # compounds. The file is chosen with a button, as a choice in the list
        # cannot open the file chooser in every browser, and is then chosen.
        choice = Select(self.browser.find_element(By.ID, "ev-ff"))
        upload_option = '#ev-ff option[value="upload"]'
        self.assertFalse(self.browser.find_element(By.CSS_SELECTOR, upload_option).is_enabled())
        self.assertTrue(self.browser.find_element(By.ID, "ev-ffopen").is_displayed())
        self.browser.find_element(By.ID, "ev-fffile").send_keys(
            str(data / "forcefields" / "ACS-g1.xml"))
        self.assertEqual("upload", choice.first_selected_option.get_attribute("value"))
        self.assertEqual("ACS-g1.xml", self.browser.find_element(By.CSS_SELECTOR, upload_option).text)
        # Another file takes its place; after another choice it can be chosen again
        self.browser.find_element(By.ID, "ev-fffile").send_keys(
            str(data / "forcefields" / "ACS-pol.xml"))
        self.assertEqual("ACS-pol.xml", self.browser.find_element(By.CSS_SELECTOR, upload_option).text)
        choice.select_by_value("default")
        choice.select_by_value("upload")
        self.assertEqual("ACS-pol.xml", choice.first_selected_option.text)
        self.assertFalse(self.browser.find_element(By.ID, "ev-result").is_displayed())
        self.click("#run-ev")
        self.wait(lambda b: b.find_element(By.ID, "ev-pill").text == "Done")
        self.assertEqual("", self.errors())
        self.assertTrue(self.browser.find_element(By.ID, "ev-result").is_displayed())
        self.assertEqual("3 compounds", self.text("#ev-ncomp"))
        rows = self.browser.execute_script(
            "return [...document.querySelectorAll('#ev-stats tbody tr')]"
            ".map(r => [...r.cells].map(c => c.textContent))")
        interaction = [row for row in rows if row[0] == "Interaction energy"][0]
        self.assertEqual(["Train", "243"], interaction[1:3])
        self.assertEqual("kJ/mol", interaction[6])
        self.assertGreater(float(interaction[3]), 0)
        # Four digits are enough for a deviation
        self.assertLessEqual(len(interaction[3].replace(".", "")), 4)
        # The plot: the line where both are equal, and the points
        self.wait(lambda b: len(b.find_elements(By.CSS_SELECTOR, "#evplot svg path")) >= 2)
        self.assertIn("243 Train", self.text("#ev-plotnote"))
        self.assertIn("Interaction energy", self.browser.find_element(
            By.CSS_SELECTOR, "#evplot svg").get_attribute("aria-label"))
        self.browser.execute_script("document.getElementById('ev-result').scrollIntoView()")
        self.shot("21-molprop-comparison")
        # The energies of one compound: how often which value, for the
        # reference and for the force field
        self.assertTrue(self.browser.find_element(By.ID, "ev-comp").is_displayed())
        self.wait(lambda b: len(b.find_elements(By.CSS_SELECTOR, "#evhist svg path")) >= 2)
        compounds = Select(self.browser.find_element(By.ID, "ev-compsel"))
        listed = [option.text for option in compounds.options]
        self.assertEqual(3, len(listed))
        self.assertEqual({"37", "52", "154"}, {text.split()[-2] for text in listed})
        first = listed[0].split()[-2]
        self.assertTrue(self.text("#ev-compnote").startswith(first + " structures"))
        self.assertIn("RMSD", self.text("#ev-compnote"))
        terms = Select(self.browser.find_element(By.ID, "ev-compterm"))
        self.assertEqual("INTERACTION", terms.first_selected_option.get_attribute("value"))
        self.assertIn("COULOMB", [option.get_attribute("value") for option in terms.options])
        terms.select_by_value("COULOMB")
        self.wait(lambda b: "Electrostatics (kJ/mol)" in b.find_element(By.ID, "evhist").text)
        compounds.select_by_index(1)
        second = listed[1].split()[-2]
        self.wait(lambda b: b.find_element(By.ID, "ev-compnote").text.startswith(
            second + " structures"))
        # The term stays the one that was chosen
        self.assertEqual("COULOMB", Select(self.browser.find_element(
            By.ID, "ev-compterm")).first_selected_option.get_attribute("value"))
        self.assertEqual("", self.errors())
        self.browser.execute_script("document.getElementById('ev-comp').scrollIntoView()")
        self.shot("21b-compound-energies")
        # Another property
        Select(self.browser.find_element(By.ID, "ev-plotsel")).select_by_value("COULOMB")
        self.wait(lambda b: "Electrostatics" in b.find_element(
            By.CSS_SELECTOR, "#evplot svg").get_attribute("aria-label"))
        # The files
        saved = self.saved_file("#ev-data", "evaluate-COULOMB.xvg", "#")
        self.assertIn('legend "Train"', saved)
        log = self.saved_file("#ev-log", "evaluate.log", "")
        self.assertIn("train_ff", log)
        from act_gui import plots  # pylint: disable=import-outside-toplevel
        if plots.available():
            target = self.downloads / "evaluate-COULOMB.pdf"
            if target.exists():
                target.unlink()
            self.click("#ev-pdf")
            self.confirm_save("evaluate-COULOMB.pdf")
            self.wait(lambda b: target.is_file() and target.stat().st_size > 2000
                      and not list(self.downloads.glob("*.part")))
            self.assertTrue(target.read_bytes().startswith(b"%PDF"))
        else:
            self.assertFalse(self.browser.find_element(By.ID, "ev-pdf").is_displayed())
        self.assertEqual("", self.errors())
        # Nothing chosen, nothing computed
        for name in ("inter", "elec"):
            self.click('#ev-observables input[data-name="%s"]' % name)
        self.click("#run-ev")
        self.wait(lambda b: "at least one property" in b.find_element(By.ID, "toast").text)
        # The default force field has other atom types than these compounds
        self.click('#ev-observables input[data-name="inter"]')
        Select(self.browser.find_element(By.ID, "ev-ff")).select_by_value("default")
        self.click("#run-ev")
        self.wait(lambda b: b.find_element(By.ID, "ev-pill").text == "Failed")
        self.assertIn("None of the compounds", self.errors())
        # The result of the comparison before this one is gone
        self.assertFalse(self.browser.find_element(By.ID, "ev-result").is_displayed())
        self.assertFalse(self.browser.find_element(By.ID, "ev-comp").is_displayed())
        self.shot("22-molprop-default")

    @unittest.skipUnless(os.path.isdir(TESTDATA), "ACT_TESTDATA is not set")
    def test_9b_train_a_force_field(self):
        """!
        @brief A force field is trained on the energies of the compounds in
        a molprop file: the page asks what to train, follows the training,
        lists the trained parameters, compares the trained force field with
        the data and hands it to the force field workspace.
        """
        data = Path(TESTDATA)
        self.open_page()
        self.click('#nav button[data-view="molprop"]')
        self.browser.find_element(By.ID, "mpw-file").send_keys(str(data / "molprop" / "booze-mono.xml"))
        self.wait(lambda b: b.find_element(By.ID, "ev-card").is_displayed())
        self.assertFalse(self.browser.find_element(By.ID, "tr-opts").is_displayed())
        self.assertEqual("Compare", self.text("#run-ev"))
        self.click('#ev-what button[data-v="train"]')
        self.assertTrue(self.browser.find_element(By.ID, "tr-opts").is_displayed())
        self.assertEqual("Train", self.text("#run-ev"))
        self.assertEqual("Properties to train on", self.text("#ev-obslab"))
        # Monte Carlo is one generation on one process, the genetic
        # algorithm has generations and needs two processes
        optimizer = Select(self.browser.find_element(By.ID, "tr-optimizer"))
        self.assertEqual("MCMC", optimizer.first_selected_option.get_attribute("value"))
        self.assertFalse(self.browser.find_element(By.ID, "tr-genfield").is_displayed())
        optimizer.select_by_value("HYBRID")
        self.assertTrue(self.browser.find_element(By.ID, "tr-genfield").is_displayed())
        self.assertGreaterEqual(int(self.browser.find_element(By.ID, "tr-procs").get_attribute("value")), 2)
        self.assertIn("an even number", self.text("#tr-note"))
        optimizer.select_by_value("MCMC")
        procs = self.browser.find_element(By.ID, "tr-procs")
        procs.clear()
        procs.send_keys("1")
        self.browser.find_element(By.ID, "ev-fffile").send_keys(
            str(data / "forcefields" / "halgren-PG.xml"))
        Select(self.browser.find_element(By.ID, "ev-ff")).select_by_value("upload")
        # Nothing to train, nothing started
        self.click("#run-ev")
        self.wait(lambda b: "kinds of parameters" in b.find_element(By.ID, "toast").text)
        self.assertFalse(self.browser.find_element(By.ID, "tr-run").is_displayed())
        # The force field tells which kinds of parameters can be changed
        self.wait(lambda b: len(b.find_elements(By.CSS_SELECTOR, "#tr-kinds button")) > 0)

        def kinds(pressed=False):
            return self.browser.execute_script(
                "return [...document.querySelectorAll('#tr-kinds button')]"
                ".filter(b => !arguments[0] || b.getAttribute('aria-pressed') === 'true')"
                ".map(b => b.dataset.kind)", pressed)
        wanted = ["De", "b", "c", "bondlength", "angle", "kt"]
        self.assertLessEqual(set(wanted), set(kinds()))
        self.assertEqual([], kinds(True))
        self.assertIn("Click the kinds", self.text("#tr-kindnote"))
        counts = self.text('#tr-kinds button[data-kind="De"]').split()
        self.assertEqual("De", counts[0])
        self.assertGreater(int(counts[1]), 0)
        # By a click or typed, and a click takes a kind away again
        fit = self.browser.find_element(By.ID, "tr-fit")
        for kind in ("De", "b"):
            self.click('#tr-kinds button[data-kind="%s"]' % kind)
        self.assertEqual("De b", fit.get_attribute("value"))
        fit.send_keys(" c bondlength angle kt charge")
        self.assertEqual(set(wanted), set(kinds(True)))
        self.click('#tr-kinds button[data-kind="De"]')
        self.assertEqual("b c bondlength angle kt charge", fit.get_attribute("value"))
        fit.clear()
        fit.send_keys("De b c bondlength angle kt")
        self.assertEqual(wanted, sorted(kinds(True), key=wanted.index))
        for field, value in (("tr-iter", "5"), ("tr-seed", "1993")):
            element = self.browser.find_element(By.ID, field)
            element.clear()
            element.send_keys(value)
        self.shot("23-train-form")
        self.click("#run-ev")
        self.wait(lambda b: b.find_element(By.ID, "ev-pill").text == "Done")
        self.assertEqual("", self.errors())
        # How it went
        self.assertTrue(self.browser.find_element(By.ID, "tr-run").is_displayed())
        status = self.text("#tr-status")
        self.assertIn("MCMC", status)
        self.assertIn("5 iterations", status)
        self.assertIn("χ²", status)
        self.assertEqual("100%", self.browser.execute_script(
            "return document.getElementById('tr-bar').style.width"))
        self.assertGreaterEqual(len(self.browser.find_elements(By.CSS_SELECTOR, "#trplot svg path, #trplot svg polyline")), 1)
        self.assertFalse(self.browser.find_element(By.ID, "tr-stop").is_displayed())
        # The parameters and the comparison of the trained force field
        self.assertTrue(self.browser.find_element(By.ID, "tr-result").is_displayed())
        rows = self.browser.execute_script(
            "return [...document.querySelectorAll('#tr-params tbody tr')]"
            ".map(r => [...r.cells].map(c => c.textContent))")
        self.assertGreater(len(rows), 0)
        self.assertEqual("%d parameters" % len(rows), self.text("#tr-count"))
        self.assertLessEqual({row[0] for row in rows},
                             {"De", "b", "c", "bondlength", "angle", "kt"})
        self.assertTrue(all(row[2] for row in rows))
        self.assertIn("Sensitivity analysis", self.text("#tr-sens"))
        self.assertTrue(self.browser.find_element(By.ID, "ev-result").is_displayed())
        self.assertIn("trained force field", self.text("#ev-source"))
        self.assertIn("Potential energy", self.text("#ev-stats"))
        self.browser.execute_script("document.getElementById('tr-run').scrollIntoView()")
        self.shot("24-train-done")
        # What the training changed: the trained force field against the one it started from
        self.browser.execute_script("document.getElementById('tr-result').scrollIntoView()")
        self.click("#tr-diff")
        self.wait(lambda b: b.find_element(By.ID, "fd-result").is_displayed() or self.errors())
        self.assertEqual("", self.errors())
        self.assertTrue(self.browser.find_element(By.ID, "view-force-field").is_displayed())
        self.assertIn("Trained force field", self.text("#ffchips"))
        heads = self.browser.execute_script(
            "return [...document.querySelectorAll('#fd-table thead th')].map(c => c.textContent)")
        self.assertEqual(["halgren-PG.xml", "Trained force field", "Difference"], heads[3:6])
        changed = self.browser.execute_script(
            "return [...document.querySelectorAll('#fd-table tbody tr')]"
            ".map(r => [...r.cells].map(c => c.textContent))")
        self.assertGreater(len(changed), 0)
        self.assertLessEqual({row[2] for row in changed},
                             {"De", "b", "c", "bondlength", "angle", "kt"})
        self.assertTrue(all(row[5] for row in changed))
        self.shot("24b-train-changed")
        # The trained force field in the force field workspace
        self.click('#nav button[data-view="molprop"]')
        self.browser.execute_script("document.getElementById('tr-result').scrollIntoView()")
        self.click("#tr-use")
        self.wait(lambda b: b.find_element(By.ID, "view-force-field").is_displayed()
                  or self.errors())
        self.assertEqual("", self.errors())
        self.assertIn("Trained force field", self.text("#ffchips"))
        self.assertEqual("", self.errors())
        # Back to comparing, the form for training is gone
        self.click('#nav button[data-view="molprop"]')
        self.click('#ev-what button[data-v="compare"]')
        self.assertFalse(self.browser.find_element(By.ID, "tr-opts").is_displayed())
        self.assertEqual("Compare", self.text("#run-ev"))

    @unittest.skipUnless(os.path.isdir(TESTDATA), "ACT_TESTDATA is not set")
    def test_9bb_continue_a_training(self):
        """!
        @brief After a training the next one continues from the trained
        force field, without random values, and can start anew.
        """
        data = Path(TESTDATA)
        self.open_page()
        self.click('#nav button[data-view="molprop"]')
        self.browser.find_element(By.ID, "mpw-file").send_keys(str(data / "molprop" / "booze-mono.xml"))
        self.wait(lambda b: b.find_element(By.ID, "ev-card").is_displayed())
        self.click('#ev-what button[data-v="train"]')
        self.browser.find_element(By.ID, "ev-fffile").send_keys(
            str(data / "forcefields" / "halgren-PG.xml"))
        choice = Select(self.browser.find_element(By.ID, "ev-ff"))
        choice.select_by_value("upload")
        self.assertFalse(self.browser.find_element(By.ID, "tr-contnote").is_displayed())
        fit = self.browser.find_element(By.ID, "tr-fit")
        fit.send_keys("sigma epsilon")
        iterations = self.browser.find_element(By.ID, "tr-iter")
        iterations.clear()
        iterations.send_keys("3")
        script = self.browser.execute_script
        script("window.asked = []; const send = window.fetch;"
               "window.fetch = (url, init) => { if (init && typeof init.body === 'string')"
               " window.asked.push(init.body); return send(url, init); };")
        self.click("#run-ev")
        self.wait(lambda b: b.find_element(By.ID, "ev-pill").text in ("Done", "Failed"))
        self.assertEqual("Done", self.text("#ev-pill"), self.errors())
        random = self.browser.find_element(By.ID, "tr-random")
        self.assertTrue(random.is_selected())

        # Further from where this training ended
        script("document.getElementById('tr-continue').click()")
        self.wait(lambda b: b.find_element(By.ID, "tr-contnote").is_displayed())
        self.assertEqual("trained", choice.first_selected_option.get_attribute("value"))
        self.assertIn("The trained force field of the training that ended at",
                      choice.first_selected_option.text)
        self.assertFalse(random.is_selected())
        note = self.text("#tr-conttext")
        self.assertIn("from its trained force field", note)
        self.assertNotIn("individuals", note)
        # The genetic algorithm would need individuals, which Monte Carlo alone does not leave
        optimizer = Select(self.browser.find_element(By.ID, "tr-optimizer"))
        optimizer.select_by_value("HYBRID")
        self.assertIn("no individuals to continue from", self.text("#tr-conttext"))
        optimizer.select_by_value("MCMC")
        self.shot("24b-training-continued")
        self.wait(lambda b: len(b.find_elements(By.CSS_SELECTOR, "#tr-kinds button")) > 0)
        script("document.getElementById('run-ev').scrollIntoView()")
        self.click("#run-ev")
        self.wait(lambda b: len([body for body in b.execute_script("return window.asked")
                                 if '"train"' in body]) == 2)
        self.wait(lambda b: b.find_element(By.ID, "ev-pill").text in ("Done", "Failed"))
        self.assertEqual("Done", self.text("#ev-pill"), self.errors())
        requests = [body for body in map(json.loads, script("return window.asked"))
                    if body.get("task") == "train"]
        self.assertIn("file", requests[0]["forcefield"])
        self.assertIn("job", requests[1]["forcefield"])
        self.assertFalse(requests[1]["options"]["random_init"])
        self.assertNotIn("continue", requests[1]["training"])
        # What changed is told against the force field it continued from
        self.assertTrue(self.browser.find_element(By.ID, "tr-diff").is_displayed())

        # Or anew
        script("document.getElementById('ev-card').scrollIntoView()")
        script("document.getElementById('tr-anew').click()")
        self.assertFalse(self.browser.find_element(By.ID, "tr-contnote").is_displayed())
        self.assertEqual("default", choice.first_selected_option.get_attribute("value"))
        self.assertTrue(random.is_selected())

        # With the genetic algorithm the individuals go along, where
        # several processes can be started
        import act  # pylint: disable=import-outside-toplevel
        try:
            act.config.launcher_words(2)
        except act.ActError:
            return
        procs = self.browser.find_element(By.ID, "tr-procs")
        if int(procs.get_attribute("max")) < 2:
            return
        choice.select_by_value("upload")
        optimizer.select_by_value("HYBRID")
        generations = self.browser.find_element(By.ID, "tr-gen")
        generations.clear()
        generations.send_keys("2")
        procs.clear()
        procs.send_keys("2")
        # The individuals: an even number of them, here more than processes,
        # which then compute them in turn
        individuals = self.browser.find_element(By.ID, "tr-pop")
        self.assertEqual("", individuals.get_attribute("value"))
        individuals.send_keys("3")
        script("document.getElementById('run-ev').scrollIntoView()")
        self.click("#run-ev")
        self.wait(lambda b: "even number of individuals" in b.find_element(By.ID, "toast").text)
        individuals.clear()
        individuals.send_keys("4")
        script("window.asked = []")
        script("document.getElementById('run-ev').scrollIntoView()")
        self.click("#run-ev")
        self.wait(lambda b: any('"train"' in body for body in b.execute_script("return window.asked")))
        self.wait(lambda b: b.find_element(By.ID, "ev-pill").text in ("Done", "Failed"))
        self.assertEqual("Done", self.text("#ev-pill"), self.errors())
        request = [body for body in map(json.loads, script("return window.asked"))
                   if body.get("task") == "train"][0]
        self.assertEqual((2, 4), (request["processes"], request["training"]["population"]))
        script("document.getElementById('tr-continue').click()")
        self.wait(lambda b: "4 individuals" in b.find_element(By.ID, "tr-conttext").text)
        self.assertIn("That needs 4 individuals", self.text("#tr-conttext"))
        self.assertEqual("4", individuals.get_attribute("value"))
        self.assertEqual("2", procs.get_attribute("value"))
        self.shot("24c-training-continued-population")
        # Another number of individuals than the training ended with
        individuals.clear()
        individuals.send_keys("2")
        script("document.getElementById('run-ev').scrollIntoView()")
        self.click("#run-ev")
        self.wait(lambda b: "needs 4 individuals" in b.find_element(By.ID, "toast").text)
        individuals.clear()
        individuals.send_keys("4")
        script("window.asked = []")
        script("document.getElementById('run-ev').scrollIntoView()")
        self.click("#run-ev")
        self.wait(lambda b: any('"train"' in body for body in b.execute_script("return window.asked")))
        self.wait(lambda b: b.find_element(By.ID, "ev-pill").text in ("Done", "Failed"))
        self.assertEqual("Done", self.text("#ev-pill"), self.errors())
        request = [body for body in map(json.loads, script("return window.asked"))
                   if body.get("task") == "train"][0]
        self.assertEqual(request["forcefield"]["job"], request["training"]["continue"])
        self.assertEqual((2, 4), (request["processes"], request["training"]["population"]))
        # Monte Carlo alone leaves the individuals where they are
        optimizer.select_by_value("MCMC")
        self.assertIn("does not use the 4 individuals", self.text("#tr-conttext"))

    @unittest.skipUnless(os.path.isdir(TESTDATA), "ACT_TESTDATA is not set")
    def test_9f_sorted_table_own_selection_and_points(self):
        """!
        @brief The table of compounds is sorted by a column. A comparison
        uses a selection file of the user as it is, shows only the
        properties that were chosen, the first one first, tells training
        and testing apart, zooms, and shows the structure and the energies
        of a point that is clicked.
        """
        data = Path(TESTDATA)
        self.open_page()
        self.click('#nav button[data-view="molprop"]')
        self.browser.find_element(By.ID, "mpw-file").send_keys(str(data / "molprop" / "mpdimer.xml"))
        self.wait(lambda b: b.find_element(By.ID, "ev-card").is_displayed())
        script = self.browser.execute_script

        def column(index):
            return script("return [...document.querySelectorAll('#mpw-table tbody tr')]"
                          ".map(r => r.cells[arguments[0]].textContent)", index)
        in_file = column(1)
        self.assertEqual(["1-propanol#ethane", "guanidinium#water", "water#1-butanol"], in_file)
        # By the number of atoms, up, down and back to the order of the file
        atoms = [int(n) for n in column(4)]
        self.click('#mpw-table button[data-sort="natom"]')
        self.assertEqual(sorted(atoms), [int(n) for n in column(4)])
        self.assertIn("Atoms", self.text('#mpw-table th[aria-sort="ascending"]'))
        self.click('#mpw-table button[data-sort="natom"]')
        self.assertEqual(sorted(atoms, reverse=True), [int(n) for n in column(4)])
        self.click('#mpw-table button[data-sort="natom"]')
        self.assertEqual(in_file, column(1))
        self.click('#mpw-table button[data-sort="name"]')
        self.assertEqual(sorted(in_file), column(1))
        self.shot("28b-sorted-table")

        # A selection file of the user: one that is not one is told
        with tempfile.TemporaryDirectory() as tmp:
            wrong = Path(tmp) / "wrong.dat"
            wrong.write_text("1-propanol#ethane Train\n")
            chooser = self.browser.find_element(By.ID, "ev-selfile")
            chooser.send_keys(str(wrong))
            self.wait(lambda b: "wrong.dat" in self.errors())
            self.assertEqual("table", Select(self.browser.find_element(By.ID, "ev-sel"))
                             .first_selected_option.get_attribute("value"))
            mine = Path(tmp) / "mine.dat"
            mine.write_text("1-propanol#ethane|Train\nguanidinium#water|Test\nnobody|Train\n")
            chooser.send_keys(str(mine))
            self.wait(lambda b: "1 for training" in b.find_element(By.ID, "ev-selnote").text)
        note = self.text("#ev-selnote")
        self.assertIn("1 for testing, 1 of the molprop file not used", note)
        self.assertIn("1 name is not in the molprop file", note)
        self.assertEqual("mine.dat", Select(self.browser.find_element(By.ID, "ev-sel"))
                         .first_selected_option.text)

        # Dispersion first, then electrostatics; not the interaction energy
        self.click('#ev-observables input[data-name="inter"]')
        self.click('#ev-observables input[data-name="disp"]')
        self.click('#ev-observables input[data-name="elec"]')
        self.browser.find_element(By.ID, "ev-fffile").send_keys(str(data / "forcefields" / "ACS-pol.xml"))
        script("window.asked = []; const send = window.fetch;"
               "window.fetch = (url, init) => { if (init && init.body) window.asked.push(init.body);"
               " return send(url, init); };")
        self.click("#run-ev")
        self.wait(lambda b: b.find_element(By.ID, "ev-pill").text in ("Done", "Failed"))
        self.assertEqual("Done", self.text("#ev-pill"), self.errors())
        request = [json.loads(body) for body in script("return window.asked")
                   if '"evaluate"' in body][0]
        self.assertEqual(["disp", "elec"], request["observables"])
        self.assertEqual("1-propanol#ethane|Train\nguanidinium#water|Test\nnobody|Train\n",
                         request["molprop"]["selection"])
        self.assertNotIn("sets", request["molprop"])
        # Only what was chosen, the first one first
        plots = Select(self.browser.find_element(By.ID, "ev-plotsel"))
        self.assertEqual(["DISPERSION", "COULOMB"],
                         [o.get_attribute("value") for o in plots.options])
        self.assertEqual("DISPERSION", plots.first_selected_option.get_attribute("value"))
        rows = script("return [...document.querySelectorAll('#ev-stats tbody tr')]"
                      ".map(r => [r.cells[0].textContent, r.cells[1].textContent])")
        self.assertEqual({"Dispersion", "Electrostatics"}, {row[0] for row in rows})
        self.assertEqual("Dispersion", rows[0][0])
        self.assertEqual({"Train", "Test"}, {row[1] for row in rows})
        # Training and testing in the legend
        self.wait(lambda b: len(b.find_elements(By.CSS_SELECTOR, "#evplot g.legend")) == 2)
        self.assertEqual(["Train", "Test"], [g.text for g in self.browser.find_elements(
            By.CSS_SELECTOR, "#evplot g.legend")])

        # Zooming in shows other numbers on the axes, All shows all again
        def ticks():
            return script("return [...document.querySelectorAll('#evplot svg > text')]"
                          ".map(t => t.textContent).join(' ')")
        whole = ticks()
        self.click("#ev-zoomin")
        self.click("#ev-zoomin")
        self.assertNotEqual(whole, ticks())
        self.click("#ev-zoomall")
        self.assertEqual(whole, ticks())
        self.shot("28c-comparison-chosen")

        # A click on a point of the training: its structure and its energies
        script("document.getElementById('evplot').scrollIntoView({ block: 'center' })")
        place = script("""
            const svg = document.querySelector('#evplot svg'), box = svg.getBoundingClientRect();
            const scale = box.width / svg.viewBox.baseVal.width;
            const path = [...svg.querySelectorAll('g path')].filter(p => p.getAttribute('stroke-linecap') === 'round')[0];
            const [x, y] = path.getAttribute('d').slice(1).split('h')[0].split(' ').map(Number);
            return [x * scale - box.width / 2, y * scale - box.height / 2];""")
        svg = self.browser.find_element(By.CSS_SELECTOR, "#evplot svg")
        ActionChains(self.browser).move_to_element_with_offset(
            svg, int(round(place[0])), int(round(place[1]))).click().perform()
        self.wait(lambda b: b.find_element(By.ID, "ev-calc").is_displayed())
        self.assertEqual("", self.errors())
        self.assertEqual("1-propanol#ethane", self.text("#ev-calcname"))
        self.assertIn("Train, calculation", self.text("#ev-calcwhat"))
        energies = script("return [...document.querySelectorAll('#ev-calctab tbody tr')]"
                          ".map(r => [r.cells[0].textContent, r.className])")
        self.assertIn(["Dispersion", "plotted"], energies)
        self.assertIn("Interaction energy", [row[0] for row in energies])
        self.assertIn("atoms, the second compound with green carbons", self.text("#ev-calcnote"))
        self.assertEqual(1, len(self.browser.find_elements(By.CSS_SELECTOR, "#evplot circle.marked")))
        self.shot("28d-structure-of-a-point")
        self.click("#ev-calcclose")
        self.assertFalse(self.browser.find_element(By.ID, "ev-calc").is_displayed())
        self.assertEqual(0, len(self.browser.find_elements(By.CSS_SELECTOR, "#evplot circle.marked")))

    @unittest.skipUnless(os.path.isdir(TESTDATA), "ACT_TESTDATA is not set")
    def test_8b_calculations_twice(self):
        """!
        @brief A molprop file with a calculation that is there twice: the
        validation says so, and the file is saved without it, under the
        name of the file or another.
        """
        original = (Path(TESTDATA) / "molprop" / "water-forces.xml").read_text()
        begin, end = original.index("<experiment "), original.index("</experiment>") + 13
        with tempfile.TemporaryDirectory() as tmp:
            twice = Path(tmp) / "twice.xml"
            twice.write_text(original[:end] + "\n    " + original[begin:end] + original[end:])
            self.open_page()
            self.click('#nav button[data-view="molprop"]')
            self.browser.find_element(By.ID, "mpw-file").send_keys(str(twice))
            self.wait(lambda b: b.find_element(By.ID, "ev-card").is_displayed())
        self.wait(lambda b: "twice" in b.find_element(By.ID, "mpw-vlist").text)
        told = self.text("#mpw-vlist")
        self.assertIn("1 calculation is in the file twice", told)
        self.assertIn("in 1 compound: water", told)
        self.assertEqual("Valid, with duplicates", self.text("#mpw-vpill"))
        self.shot("29b-calculations-twice")
        # Saved without it, under the name of the file or another
        saved = self.saved_file("#mpw-clean", "twice.xml", "<?xml", rename="water-once.xml")
        self.assertEqual(original.count("<experiment "), saved.count("<experiment "))
        self.assertIn('molname="water"', saved)
        self.assertEqual("", self.errors())

    @unittest.skipUnless(os.path.isdir(TESTDATA), "ACT_TESTDATA is not set")
    def test_9c_compounds_and_weights(self):
        """!
        @brief The user says in the table what each compound is used for:
        training, testing or nothing, one by one or for all that the table
        shows. A comparison then has both data sets, and a training gets
        them with the weights of the properties.
        """
        data = Path(TESTDATA)
        self.open_page()
        self.click('#nav button[data-view="molprop"]')
        self.browser.find_element(By.ID, "mpw-file").send_keys(str(data / "molprop" / "mpdimer.xml"))
        self.wait(lambda b: b.find_element(By.ID, "ev-card").is_displayed())
        self.assertEqual("3 for training", self.text("#mpw-sets"))

        def uses():
            return self.browser.execute_script(
                "return [...document.querySelectorAll('#mpw-table button[data-use]')]"
                ".map(b => b.textContent)")

        def use(row, times=1):
            for _ in range(times):
                self.browser.execute_script(
                    "document.querySelectorAll('#mpw-table button[data-use]')[arguments[0]].click()",
                    row)
        self.assertEqual(["Training"] * 3, uses())
        use(0)
        use(1, 2)
        self.assertEqual(["Testing", "Not used", "Training"], uses())
        self.assertEqual("1 for training, 1 for testing, 1 not used", self.text("#mpw-sets"))
        # A click on the button does not open the details of the compound
        self.assertFalse(self.browser.find_element(By.ID, "mpw-detail").is_displayed())

        self.browser.find_element(By.ID, "ev-fffile").send_keys(
            str(data / "forcefields" / "ACS-pol.xml"))
        Select(self.browser.find_element(By.ID, "ev-ff")).select_by_value("upload")
        self.click("#run-ev")
        self.wait(lambda b: b.find_element(By.ID, "ev-pill").text == "Done")
        self.assertEqual("", self.errors())
        self.assertEqual("2 compounds", self.text("#ev-ncomp"))
        rows = self.browser.execute_script(
            "return [...document.querySelectorAll('#ev-stats tbody tr')]"
            ".map(r => [...r.cells].map(c => c.textContent))")
        self.assertEqual({"Train", "Test"}, {row[1] for row in rows})
        self.shot("25-compounds-chosen")

        # In a training the compounds for testing are those of the table,
        # and each property that is chosen has a weight
        self.browser.execute_script("document.getElementById('ev-card').scrollIntoView()")
        self.click('#ev-what button[data-v="train"]')
        percent = self.browser.find_element(By.ID, "tr-test")
        self.assertFalse(percent.is_enabled())
        self.assertTrue(self.browser.find_element(By.ID, "tr-testnote").is_displayed())
        weight = self.browser.find_element(By.CSS_SELECTOR, '#ev-observables input[data-weight="inter"]')
        other = self.browser.find_element(By.CSS_SELECTOR, '#ev-observables input[data-weight="elec"]')
        self.assertTrue(weight.is_displayed())
        self.assertEqual("1", weight.get_attribute("value"))
        self.assertFalse(other.is_displayed())
        self.click('#ev-observables input[data-name="elec"]')
        self.assertTrue(other.is_displayed())
        weight.clear()
        weight.send_keys("2.5")
        # What the page asks the server for
        self.browser.execute_script(
            "window.asked = []; const send = window.fetch;"
            "window.fetch = (url, init) => { if (init && init.body && typeof init.body === 'string')"
            " window.asked.push(init.body); return send(url, init); };")
        self.wait(lambda b: len(b.find_elements(By.CSS_SELECTOR, "#tr-kinds button")) > 0)
        self.browser.execute_script("document.querySelector('#tr-kinds button').click()")
        iterations = self.browser.find_element(By.ID, "tr-iter")
        iterations.clear()
        iterations.send_keys("2")
        self.click("#run-ev")
        self.wait(lambda b: b.find_element(By.ID, "ev-pill").text in ("Done", "Failed"))
        asked = [json.loads(body) for body in self.browser.execute_script("return window.asked")]
        request = [body for body in asked if body.get("task") == "train"][0]
        self.assertEqual({"inter": 2.5}, request["weights"])
        self.assertEqual(["inter", "elec"], request["observables"])
        self.assertEqual(0, request["training"]["test"])
        sets = request["molprop"]["sets"]
        self.assertEqual((1, 1), (len(sets["test"]), len(sets["ignore"])))
        self.assertEqual("Done", self.text("#ev-pill"), self.errors())

        # All that the table shows at once: those with a name that is found
        self.browser.execute_script("document.getElementById('mpw-content').scrollIntoView()")
        self.click('#mpw-setbar button[data-set="ignore"]')
        self.assertEqual(["Not used"] * 3, uses())
        self.assertEqual("0 for training, 3 not used", self.text("#mpw-sets"))
        self.browser.execute_script("document.getElementById('run-ev').scrollIntoView()")
        self.click("#run-ev")
        self.wait(lambda b: "No compound is left" in b.find_element(By.ID, "toast").text)
        self.click('#mpw-setbar button[data-set="train"]')
        self.assertEqual("3 for training", self.text("#mpw-sets"))
        self.assertTrue(percent.is_enabled())
        self.assertFalse(self.browser.find_element(By.ID, "tr-testnote").is_displayed())
        # Another file starts with all compounds for training
        use(0)
        self.browser.find_element(By.ID, "mpw-file").send_keys(
            str(data / "molprop" / "molprop.xml"))
        self.wait(lambda b: "molprop.xml" in b.find_element(By.ID, "mpw-chips").text)
        self.assertNotIn("testing", self.text("#mpw-sets"))

    @unittest.skipUnless(os.path.isdir(TESTDATA), "ACT_TESTDATA is not set")
    def test_9d_selection_file(self):
        """!
        @brief The user makes a selection file: compounds are ticked one by
        one or by the monomers they consist of, a part is drawn for testing,
        and the file is saved.
        """
        data = Path(TESTDATA)
        self.open_page()
        self.click('#nav button[data-view="molprop"]')
        self.browser.find_element(By.ID, "mpw-file").send_keys(
            str(data / "molprop" / "booze-inter.xml"))
        self.wait(lambda b: b.find_element(By.ID, "ev-card").is_displayed())
        self.assertEqual("", self.errors())
        script = self.browser.execute_script

        def ticked():
            return script("return [...document.querySelectorAll('#mpw-table tbody tr')]"
                          ".filter(r => r.querySelector('input[data-tick]').checked)"
                          ".map(r => r.cells[1].textContent)")

        def monomer(name):
            script("[...document.querySelectorAll('#sel-list input[data-mono]')]"
                   ".find(b => b.dataset.mono === arguments[0]).click()", name)
        everything = ["methanol#methanol", "methanol#water", "water#water"]
        self.assertEqual(everything, ticked())
        # The monomers of the dimers, with the number of compounds each is in
        self.assertTrue(self.browser.find_element(By.ID, "sel-mono").is_displayed())
        self.assertEqual(["methanol2", "water2"], script(
            "return [...document.querySelectorAll('#sel-list label')].map(l => l.textContent)"))
        # Choosing a monomer chooses the dimers it is in, and no others
        self.click("#sel-none")
        self.assertEqual([], ticked())
        self.assertEqual("0 for training, 3 not used", self.text("#mpw-sets"))
        monomer("water")
        self.assertEqual(["methanol#water", "water#water"], ticked())
        self.assertIn("1 of 2 monomers ticked", self.text("#sel-mononote"))
        self.assertIn("2 of 3 compounds are used", self.text("#sel-mononote"))
        # Or only the dimers of which all monomers are ticked
        self.click('#sel-mode button[data-v="every"]')
        self.assertEqual(["water#water"], ticked())
        self.click('#sel-mode button[data-v="any"]')
        self.click("#sel-all")
        self.assertEqual(everything, ticked())
        self.shot("26-selection-monomers")

        # One by one, and all that the table shows
        script("document.querySelector('#mpw-table input[data-tick]').click()")
        self.assertEqual(everything[1:], ticked())
        self.assertEqual("2 for training, 1 not used", self.text("#mpw-sets"))
        self.assertFalse(self.browser.find_element(By.ID, "mpw-detail").is_displayed())
        header = "document.querySelector('#mpw-table input[data-all]')"
        self.assertTrue(script("return %s.indeterminate" % header))
        script(header + ".click()")
        self.assertEqual(everything, ticked())
        # The compounds with data for a property
        options = [o.get_attribute("value") for o in
                   self.browser.find_elements(By.CSS_SELECTOR, "#mpw-prop option")]
        self.assertIn("InteractionEnergy", options)
        Select(self.browser.find_element(By.ID, "mpw-prop")).select_by_value("InteractionEnergy")
        self.assertEqual("3 compounds", self.text("#mpw-count"))

        # A part for testing is drawn from the compounds that are ticked
        script("document.querySelector('#mpw-table input[data-tick]').click()")
        percent = self.browser.find_element(By.ID, "sel-percent")
        percent.clear()
        percent.send_keys("50")
        self.click("#sel-draw")
        self.assertEqual("1 for training, 1 for testing, 1 not used", self.text("#mpw-sets"))
        self.click("#sel-clear")
        self.assertEqual("2 for training, 1 not used", self.text("#mpw-sets"))
        self.click("#sel-draw")
        self.shot("27-selection-file")

        # The file that is saved has what the table says
        script("window.saved = []; const send = window.fetch;"
               "window.fetch = (url, init) => send(url, init).then(answer => {"
               " if (String(url).includes('/api/selection')) answer.clone().text()"
               ".then(text => window.saved.push(text)); return answer; });")
        self.click("#sel-save")
        # Named after the molprop file
        self.confirm_save("booze-inter.dat")
        self.wait(lambda b: len(b.execute_script("return window.saved")) == 1)
        self.wait(lambda b: "Saved booze-inter.dat" in b.find_element(By.ID, "toast").text)
        self.assertEqual("", self.errors())
        lines = script("return window.saved")[0].splitlines()
        self.assertEqual(["methanol#water", "water#water"], [line.split("|")[0] for line in lines])
        self.assertEqual(["Test", "Train"], sorted(line.split("|")[1] for line in lines))
        uses = script("return [...document.querySelectorAll('#mpw-table tbody tr')]"
                      ".map(r => r.querySelector('button[data-use]').textContent)")
        self.assertEqual("Not used", uses[0])
        self.assertEqual([("Testing" if line.endswith("Test") else "Training") for line in lines],
                         uses[1:])
        # Nothing ticked: no file
        self.click("#sel-none")
        self.click("#sel-save")
        self.wait(lambda b: "No compound is for training" in b.find_element(By.ID, "toast").text)
        self.assertEqual(1, len(script("return window.saved")))

        # A selection file of the user is opened: the table says what the file says
        self.browser.find_element(By.ID, "sel-file").send_keys(
            str(data / "molprop" / "booze-inter.dat"))
        self.wait(lambda b: "booze-inter.dat" in b.find_element(By.ID, "sel-read").text)
        self.assertEqual("", self.errors())
        self.assertEqual("booze-inter.dat: 2 for training, 1 for testing.",
                         self.text("#sel-read"))
        self.assertEqual("2 for training, 1 for testing", self.text("#mpw-sets"))
        self.assertEqual(everything, ticked())
        self.assertEqual(["Training", "Training", "Testing"], script(
            "return [...document.querySelectorAll('#mpw-table button[data-use]')]"
            ".map(b => b.textContent)"))
        self.shot("28-selection-opened")
        # A comparison uses it, as a training does
        script("window.asked = []; const post = window.fetch;"
               "window.fetch = (url, init) => { if (init && typeof init.body === 'string')"
               " window.asked.push(init.body); return post(url, init); };")
        self.browser.find_element(By.ID, "ev-fffile").send_keys(
            str(data / "forcefields" / "ACS-pol.xml"))
        Select(self.browser.find_element(By.ID, "ev-ff")).select_by_value("upload")
        script("document.getElementById('run-ev').scrollIntoView()")
        self.click("#run-ev")
        self.wait(lambda b: b.find_element(By.ID, "ev-pill").text in ("Done", "Failed"))
        self.assertEqual("Done", self.text("#ev-pill"), self.errors())
        request = [body for body in map(json.loads, script("return window.asked"))
                   if body.get("task") == "evaluate"][0]
        self.assertEqual({"test": ["water#water"], "ignore": []}, request["molprop"]["sets"])
        rows = script("return [...document.querySelectorAll('#ev-stats tbody tr')]"
                      ".map(r => [...r.cells].map(c => c.textContent))")
        self.assertEqual({"Train", "Test"}, {row[1] for row in rows})
        # A file that is no selection of these compounds changes nothing
        script("document.getElementById('mpw-content').scrollIntoView()")
        self.browser.find_element(By.ID, "sel-file").send_keys(
            str(data / "molprop" / "booze-mono.dat"))
        self.wait(lambda b: "could not be read" in self.errors())
        self.assertIn("none of the compounds", self.errors())
        self.assertEqual("2 for training, 1 for testing", self.text("#mpw-sets"))

        # A file with single compounds has no monomers to choose from
        self.browser.find_element(By.ID, "mpw-file").send_keys(
            str(data / "molprop" / "molprop.xml"))
        self.wait(lambda b: "molprop.xml" in b.find_element(By.ID, "mpw-chips").text)
        self.assertFalse(self.browser.find_element(By.ID, "sel-mono").is_displayed())
        self.assertTrue(self.browser.find_element(By.ID, "sel-save").is_displayed())

    @unittest.skipUnless(os.path.isdir(TESTDATA), "ACT_TESTDATA is not set")
    def test_9e_geometry_of_chosen_compounds(self):
        """!
        @brief The force field workspace takes its geometries from the
        molprop file of the molprop workspace, and from the compounds that
        are ticked there.
        """
        data = Path(TESTDATA)
        self.open_force_fields()
        self.click('#fftabs button[data-tab="geom"]')
        shared = self.browser.find_element(By.ID, "mp-shared")
        run = self.browser.find_element(By.ID, "run-geo")
        self.assertFalse(shared.is_displayed())
        self.browser.find_element(By.ID, "ff-openfile").send_keys(
            str(data / "forcefields" / "ACS-g1.xml"))
        self.wait(lambda b: "ACS-g1.xml" in b.find_element(By.ID, "ffchips").text)
        # A molprop file is opened in its own workspace, and a compound is left out
        self.click('#nav button[data-view="molprop"]')
        self.browser.find_element(By.ID, "mpw-file").send_keys(str(data / "molprop" / "molprop.xml"))
        self.wait(lambda b: b.find_element(By.ID, "ev-card").is_displayed())
        self.click('#nav button[data-view="force-field"]')
        self.assertTrue(shared.is_displayed())
        self.assertFalse(run.is_enabled())
        shared.click()
        self.assertEqual("molprop.xml · all 3 compounds", self.text("#mp-name"))
        self.assertFalse(shared.is_displayed())
        self.assertTrue(run.is_enabled())
        self.browser.execute_script(
            "document.querySelector('#mpw-table input[data-tick]').click()")
        self.assertEqual("molprop.xml · the 2 of 3 compounds that are ticked in Molprop data",
                         self.text("#mp-name"))
        self.browser.execute_script(
            "window.asked = []; const send = window.fetch;"
            "window.fetch = (url, init) => { if (init && typeof init.body === 'string')"
            " window.asked.push(init.body); return send(url, init); };")
        run.click()
        self.wait(lambda b: b.find_element(By.ID, "geo-pill").text == "Done")
        self.assertEqual("", self.errors())
        self.assertEqual("2", self.text("#geo-ncomp"))
        request = [body for body in map(json.loads, self.browser.execute_script(
            "return window.asked")) if body.get("task") == "geometry_ff"][0]
        self.assertEqual(1, len(request["molprop"]["sets"]["ignore"]))
        self.shot("29-geometry-chosen")
        # The histograms of the geometries, and where the values of a bar come from
        self.wait(lambda b: b.find_element(By.ID, "geo-hist").is_displayed())
        kinds = Select(self.browser.find_element(By.ID, "geo-htype"))
        self.assertTrue(kinds.first_selected_option.text.startswith("Bond lengths"))
        self.assertIn("Angles", [o.text.split(" (")[0] for o in kinds.options])
        self.wait(lambda b: b.find_elements(By.CSS_SELECTOR, "#geoplot svg"))
        self.assertIn("values in bins of 1 pm", self.text("#geo-histnote"))
        self.browser.execute_script("document.getElementById('geoplot').scrollIntoView({ block: 'center' })")
        place = self.browser.execute_script("""
            const svg = document.querySelector('#geoplot svg'), box = svg.getBoundingClientRect();
            const scale = box.width / svg.viewBox.baseVal.width;
            const path = [...svg.querySelectorAll('g path')].filter(p => p.getAttribute('stroke-linecap') === 'round')[0];
            const [x, y] = path.getAttribute('d').slice(1).split('h')[0].split(' ').map(Number);
            return [x * scale - box.width / 2, y * scale - box.height / 2];""")
        ActionChains(self.browser).move_to_element_with_offset(
            self.browser.find_element(By.CSS_SELECTOR, "#geoplot svg"),
            int(round(place[0])), int(round(place[1]))).click().perform()
        self.wait(lambda b: b.find_elements(By.CSS_SELECTOR, "#geo-samples tbody tr"))
        self.assertRegex(self.text("#geo-bintitle"), r"(?i)^\d+ values? around ")
        rows = self.browser.execute_script(
            "return [...document.querySelectorAll('#geo-samples tbody tr')]"
            ".map(r => [...r.cells].map(c => c.textContent))")
        for compound, calculation, atoms, value in rows:
            self.assertTrue(compound)
            self.assertRegex(atoms, r"^[A-Z][a-z]?\d+-[A-Z][a-z]?\d+$")
            self.assertTrue(value.endswith("pm"))
        self.shot("29c-geometry-histogram")
        # Another kind of geometry
        kinds.select_by_value("ANGLES")
        self.wait(lambda b: "values in bins of 0.5 degrees" in b.find_element(By.ID, "geo-histnote").text)
        self.assertEqual("", self.errors())
        # Nothing ticked: nothing to take geometries from
        self.browser.execute_script(
            "document.querySelector('#mpw-table input[data-all]').click();"
            "if (document.querySelector('#mpw-table input[data-all]').checked)"
            " document.querySelector('#mpw-table input[data-all]').click()")
        run.click()
        self.wait(lambda b: "No compound is ticked" in b.find_element(By.ID, "toast").text)
        # A file of its own takes the place of the shared one
        self.browser.find_element(By.ID, "mp-file").send_keys(str(data / "molprop" / "molprop.xml"))
        self.wait(lambda b: b.find_element(By.ID, "mp-name").text == "molprop.xml")
        self.assertTrue(shared.is_displayed())

    # --- the cluster for long jobs ---

    def test_9f_screen_modes(self):
        """!
        @brief The button in the top bar goes from light to dark to hippy
        and back, the tab Settings offers the three; the choice is in the
        file with the settings and the page starts in it the next time.
        """
        import json  # pylint: disable=import-outside-toplevel
        # The tests of the cluster start without a file with settings
        self.addCleanup(lambda: self.config.unlink(missing_ok=True))
        self.open_page()
        root = lambda:self.browser.find_element(By.TAG_NAME, "html").get_attribute("data-theme")
        background = lambda: self.browser.execute_script(
            "return getComputedStyle(document.body).backgroundColor")
        self.assertEqual("light", root())
        self.assertEqual("Light", self.text("#theme"))
        light = background()
        self.click("#theme")
        self.assertEqual("dark", root())
        self.assertEqual("Dark", self.text("#theme"))
        self.assertEqual("rgb(0, 0, 0)", background())
        self.wait(lambda b: self.config.is_file()
                  and json.loads(self.config.read_text()).get("appearance") == {"theme": "dark"})
        self.click("#theme")
        self.assertEqual("hippy", root())
        self.assertNotIn(background(), (light, "rgb(0, 0, 0)"))
        frame = self.browser.execute_script(
            "return getComputedStyle(document.querySelector('.card')).borderTopWidth")
        self.assertEqual("4px", frame)
        self.shot("40-hippy")
        self.click("#theme")
        self.assertEqual("light", root())
        self.assertEqual(light, background())
        # The tab Settings, and the page that is opened again
        self.click('#nav button[data-view="settings"]')
        self.assertTrue(self.browser.find_element(By.ID, "view-settings").is_displayed())
        self.click('#set-theme button[data-v="dark"]')
        self.assertEqual("dark", root())
        self.assertEqual("Dark", self.text("#theme"))
        self.wait(lambda b: json.loads(self.config.read_text()).get("appearance") == {"theme": "dark"})
        self.open_page()
        self.assertEqual("dark", root())
        self.click('#nav button[data-view="settings"]')
        self.assertEqual("true", self.browser.find_element(
            By.CSS_SELECTOR, '#set-theme button[data-v="dark"]').get_attribute("aria-pressed"))
        self.click('#set-theme button[data-v="light"]')
        self.wait(lambda b: json.loads(self.config.read_text()).get("appearance") == {"theme": "light"})
        self.assertEqual("", self.errors())

    def test_a_cluster_settings(self):
        """!
        @brief The user gives the server, the user name and the working
        directory; they are kept in a file and are there when the page is
        opened again, and the login is tested.
        """
        import json  # pylint: disable=import-outside-toplevel
        self.open_page()
        self.click('#nav button[data-view="cluster"]')
        self.assertTrue(self.browser.find_element(By.ID, "view-cluster").is_displayed())
        self.assertIn("No cluster yet", self.text("#cl-chips"))
        self.assertIn("tab Settings", self.text("#cl-chips"))
        # The settings are in the tab Settings, to which the tab Cluster leads
        self.click("#cl-tosettings")
        self.assertTrue(self.browser.find_element(By.ID, "view-settings").is_displayed())
        self.assertFalse(self.browser.find_element(By.ID, "view-cluster").is_displayed())
        self.assertEqual("true", self.browser.find_element(
            By.CSS_SELECTOR, '#nav button[data-view="settings"]').get_attribute("aria-selected"))
        self.assertTrue(self.browser.find_element(By.ID, "set-cluster").is_displayed())
        self.assertFalse(self.browser.find_element(By.ID, "cl-check").is_enabled())
        self.assertFalse(self.browser.find_element(By.ID, "cl-forget").is_displayed())
        self.assertIn(str(self.config), self.text("#cl-file"))

        def fill(server, user, workdir):
            for field, value in (("cl-server", server), ("cl-user", user), ("cl-workdir", workdir)):
                box = self.browser.find_element(By.ID, field)
                box.clear()
                box.send_keys(value)
        def value(field):
            return self.browser.find_element(By.ID, field).get_attribute("value")
        # What is proposed: the module ACT and the partitions of csb
        self.assertEqual(("module", "ACT", "CLUSTER,CLUSTER-AMD", "GPU,CLUSTER-AMD", "gpu:1", "",
                          "srun", "alexandria", "120"),
                         tuple(value(f) for f in ("cl-load", "cl-loadname", "cl-partition",
                                                  "cl-gpupartition", "cl-gres", "cl-account",
                                                  "cl-launcher", "cl-program", "cl-poll")))
        self.assertEqual("Name of the module", self.text("#cl-loadlabel"))
        # Settings that cannot be are refused and not kept
        fill("-oProxyCommand=evil", "spoel", "act")
        self.click("#cl-save")
        self.wait(lambda b: "name of a server" in self.errors())
        self.assertFalse(self.config.exists())
        fill("good", "spoel", "act/jobs/")
        # ACT from a file that the shell reads, and other partitions
        Select(self.browser.find_element(By.ID, "cl-load")).select_by_value("source")
        self.assertEqual("The file, with its path", self.text("#cl-loadlabel"))
        for field, text in (("cl-loadname", "/opt/act/bin/ACTRC"), ("cl-partition", "cpu,cpu-long")):
            box = self.browser.find_element(By.ID, field)
            box.clear()
            box.send_keys(text)
        self.click("#cl-save")
        self.wait(lambda b: "spoel@good" in b.find_element(By.ID, "cl-chips").get_attribute("textContent"))
        self.assertEqual("", self.errors())
        self.assertIn("cpu,cpu-long", self.browser.find_element(By.ID, "cl-chips").get_attribute("textContent"))
        self.assertEqual({"cluster": {"server": "good", "user": "spoel", "workdir": "act/jobs",
                                      "load": "source", "load_name": "/opt/act/bin/ACTRC",
                                      "partition": "cpu,cpu-long",
                                      "gpu_partition": "GPU,CLUSTER-AMD", "gpu_gres": "gpu:1",
                                      "account": "", "launcher": "srun",
                                      "program": "alexandria", "poll_seconds": 120}},
                         json.loads(self.config.read_text()))
        self.assertEqual("act/jobs", value("cl-workdir"))
        # Nothing to load: the field for it goes away
        Select(self.browser.find_element(By.ID, "cl-load")).select_by_value("none")
        self.assertFalse(self.browser.find_element(By.ID, "cl-loadfield").is_displayed())
        Select(self.browser.find_element(By.ID, "cl-load")).select_by_value("source")
        self.click("#cl-save")
        self.wait(lambda b: b.find_element(By.ID, "cl-check").is_enabled())
        # The login
        self.click("#cl-check")
        self.wait(lambda b: b.find_element(By.ID, "cl-checkpill").text == "Ready")
        self.assertIn("The login works", self.text("#cl-result"))
        self.assertIn("slurm 23.02.7", self.text("#cl-result"))
        self.assertEqual(1, len(self.cluster.calls()))
        self.shot("30-cluster")
        # A change has to be saved before it is tested
        self.browser.find_element(By.ID, "cl-workdir").send_keys("/nothere")
        self.assertFalse(self.browser.find_element(By.ID, "cl-check").is_enabled())
        self.click("#cl-save")
        self.wait(lambda b: b.find_element(By.ID, "cl-check").is_enabled())
        self.click("#cl-check")
        self.wait(lambda b: b.find_element(By.ID, "cl-checkpill").text == "Not ready")
        self.assertIn("is not there", self.text("#cl-result"))
        # The page is opened again: the settings are those of the file
        self.open_page()
        self.click('#nav button[data-view="settings"]')
        self.wait(lambda b: b.find_element(By.ID, "cl-server").get_attribute("value") == "good")
        self.assertEqual("act/jobs/nothere", self.browser.find_element(
            By.ID, "cl-workdir").get_attribute("value"))
        self.click('#nav button[data-view="settings"]')
        self.click("#cl-forget")
        self.wait(lambda b: "No cluster yet" in b.find_element(By.ID, "cl-chips").get_attribute("textContent"))
        self.assertEqual({}, json.loads(self.config.read_text()))
        self.assertEqual("", self.browser.find_element(By.ID, "cl-server").get_attribute("value"))

    @unittest.skipUnless(os.path.isdir(TESTDATA), "ACT_TESTDATA is not set")
    def test_d_molecule_jobs_on_the_cluster(self):
        """!
        @brief The second virial coefficient and a simulation with OpenMM of
        a molecule go to the cluster; their results are shown in the
        molecule workspace. A directory stands in for the cluster.
        """
        data = Path(TESTDATA)
        self.open_page()
        self.click('#nav button[data-view="cluster"]')
        self.click('#nav button[data-view="settings"]')
        for field, value in (("cl-server", "good"), ("cl-user", "spoel"),
                             ("cl-workdir", "act/jobs")):
            box = self.browser.find_element(By.ID, field)
            box.clear()
            box.send_keys(value)
        Select(self.browser.find_element(By.ID, "cl-load")).select_by_value("none")
        self.click("#cl-save")
        self.wait(lambda b: "spoel@good" in b.find_element(By.ID, "cl-chips").get_attribute("textContent"))
        self.click('#nav button[data-view="molecule"]')
        self.browser.find_element(By.ID, "fffile").send_keys(str(data / "forcefields" / "SPCE.xml"))
        self.browser.find_element(By.ID, "molfile").send_keys(str(data / "molecules" / "water_dimer.pdb"))
        self.wait(lambda b: b.find_element(By.ID, "run-b2").is_enabled())

        # The second virial coefficient on four processes of the cluster
        self.click('#tasks button[data-tab="b2"]')
        self.assertTrue(self.browser.find_element(By.ID, "b2-where").is_displayed())
        self.click('#b2-place button[data-v="cluster"]')
        self.assertEqual("Show the job…", self.text("#run-b2"))
        self.assertFalse(self.browser.find_element(By.ID, "b2-procfield").is_displayed())
        for field, value in (("b2-jobname", "virial-1"), ("b2-tasks", "4"), ("b2-maxdimer", "16")):
            box = self.browser.find_element(By.ID, field)
            box.clear()
            box.send_keys(value)
        self.click("#run-b2")
        self.wait(lambda b: b.find_element(By.ID, "b2-job").is_displayed())
        script = self.text("#b2-jobscript")
        self.assertIn("#SBATCH -n 4", script)
        self.assertIn("srun alexandria b2 ", script)
        self.assertIn(" -maxdimer 16", script)
        self.click("#b2-send")
        self.wait(lambda b: b.find_element(By.ID, "view-cluster").is_displayed())
        self.assertEqual("", self.errors())

        # A simulation with OpenMM on a GPU of the cluster
        self.click('#nav button[data-view="molecule"]')
        self.click('#tasks button[data-tab="openmm"]')
        self.click('#omm-place button[data-v="cluster"]')
        self.assertEqual("Show the job…", self.text("#run-omm"))
        name = self.browser.find_element(By.ID, "omm-jobname")
        name.send_keys("md-1")
        self.click("#omm-gpu")
        self.click("#run-omm")
        self.wait(lambda b: b.find_element(By.ID, "omm-job").is_displayed())
        script = self.text("#omm-jobscript")
        self.assertIn("python3 run.py", script)
        self.assertIn("#SBATCH --gres=", script)
        files = self.browser.execute_script(
            "return [...document.querySelectorAll('#omm-jobfiles tbody tr')].map(r => r.cells[0].textContent)")
        self.assertEqual(["conf.pdb", "openmm.xml", "run.py", "simulation.dat"], files)
        self.shot("36-openmm-job-shown")
        self.click("#omm-send")
        self.wait(lambda b: b.find_element(By.ID, "view-cluster").is_displayed())
        self.assertEqual("", self.errors())

        # Both are done when the cluster is asked; their results are shown
        def row(job):
            return self.browser.execute_script(
                "const r = document.querySelector('#cj-table tbody tr[data-job=\"' + arguments[0] + '\"]');"
                "return r ? [...r.cells].map(c => c.textContent) : null", job)
        self.wait(lambda b: row("virial-1") is not None and row("md-1") is not None)

        def asked_until_done(_browser):
            # The cluster is not asked again within a short time, as after another test
            if row("virial-1")[2] == "Finished" and row("md-1")[2] == "Finished":
                return True
            if self.browser.find_element(By.ID, "cj-ask").is_enabled():
                self.click("#cj-ask")
            time.sleep(1)
            return False
        self.wait(asked_until_done, 180)
        self.click('#cj-table tr[data-job="md-1"] button[data-act="open"]')
        self.wait(lambda b: b.find_element(By.ID, "omm-result").is_displayed())
        self.assertTrue(self.browser.find_element(By.ID, "tab-openmm").is_displayed())
        self.assertIn("2 points", self.text("#omm-note"))
        self.assertIn("md-1", self.text("#omm-resulttitle").lower())
        self.shot("37-openmm-results")
        self.click('#nav button[data-view="cluster"]')
        self.click('#cj-table tr[data-job="virial-1"] button[data-act="open"]')
        self.wait(lambda b: b.find_element(By.ID, "tab-b2").is_displayed())
        self.wait(lambda b: b.find_element(By.ID, "b2-plotnote").text != "")
        self.assertEqual("", self.errors())

        # Take the jobs and the cluster away again
        self.click('#nav button[data-view="cluster"]')
        while self.browser.find_elements(By.CSS_SELECTOR, '#cj-table button[data-act="forget"]'):
            self.click('#cj-table button[data-act="forget"]')
            self.click('#cj-table button[data-act="forget"]')
            time.sleep(0.3)
        self.wait(lambda b: b.find_element(By.ID, "cj-empty").is_displayed())
        self.click('#nav button[data-view="settings"]')
        self.click("#cl-forget")
        self.wait(lambda b: "No cluster yet" in b.find_element(By.ID, "cl-chips").get_attribute("textContent"))

    def test_b_job_on_the_cluster(self):
        """!
        @brief The comparison of a force field with a molprop file goes to
        the cluster: the job is shown, sent, followed in the tab Cluster,
        and its results are shown as those of a comparison on this
        computer. A directory stands in for the cluster.
        """
        data = Path(TESTDATA)
        self.open_page()
        self.click('#nav button[data-view="cluster"]')
        self.assertTrue(self.browser.find_element(By.ID, "cj-empty").is_displayed())
        self.assertFalse(self.browser.find_element(By.ID, "cj-ask").is_enabled())
        self.click('#nav button[data-view="settings"]')
        for field, value in (("cl-server", "good"), ("cl-user", "spoel"),
                             ("cl-workdir", "act/jobs")):
            box = self.browser.find_element(By.ID, field)
            box.clear()
            box.send_keys(value)
        Select(self.browser.find_element(By.ID, "cl-load")).select_by_value("none")
        self.click("#cl-save")
        self.wait(lambda b: "spoel@good" in b.find_element(By.ID, "cl-chips").get_attribute("textContent"))
        logins = len(self.cluster.calls())

        # The comparison, with the choice of where it runs
        self.click('#nav button[data-view="molprop"]')
        self.browser.find_element(By.ID, "mpw-file").send_keys(
            str(data / "molprop" / "mpdimer.xml"))
        self.wait(lambda b: b.find_element(By.ID, "ev-card").is_displayed())
        self.assertTrue(self.browser.find_element(By.ID, "ev-where").is_displayed())
        self.assertFalse(self.browser.find_element(By.ID, "ev-clusteropts").is_displayed())
        self.assertEqual("Compare", self.text("#run-ev"))
        self.browser.find_element(By.ID, "ev-fffile").send_keys(
            str(data / "forcefields" / "ACS-pol.xml"))
        Select(self.browser.find_element(By.ID, "ev-ff")).select_by_value("upload")
        self.click('#ev-place button[data-v="cluster"]')
        self.assertTrue(self.browser.find_element(By.ID, "ev-clusteropts").is_displayed())
        self.assertEqual("Show the job…", self.text("#run-ev"))
        for field, value in (("ev-jobname", "dimers-1"), ("ev-tasks", "2"), ("ev-hours", "1.5")):
            box = self.browser.find_element(By.ID, field)
            box.clear()
            box.send_keys(value)
        self.click("#ev-gpu")
        self.browser.execute_script("document.getElementById('run-ev').scrollIntoView()")
        self.click("#run-ev")
        self.wait(lambda b: b.find_element(By.ID, "ev-job").is_displayed())
        self.assertEqual("", self.errors())
        self.assertEqual("dimers-1 · spoel@good:act/jobs/dimers-1", self.text("#ev-jobwhere"))
        script = self.text("#ev-jobscript")
        for line in ("#SBATCH --job-name=dimers-1", "#SBATCH --partition=GPU,CLUSTER-AMD",
                     "#SBATCH --gres=gpu:1", "#SBATCH -n 2", "#SBATCH --time=0-01:30:00"):
            self.assertIn(line, script)
        self.assertIn("srun alexandria train_ff ", script)
        self.assertIn(" -ff ff.xml ", script)
        self.assertIn(" -mp mp.xml ", script)
        self.assertIn(" -sel selection.dat", script)
        # No name of a file of this computer
        self.assertNotIn(self._tmp.name, script)
        files = self.browser.execute_script(
            "return [...document.querySelectorAll('#ev-jobfiles tbody tr')]"
            ".map(r => r.cells[0].textContent)")
        self.assertEqual(["ff.xml", "mp.xml", "selection.dat"], files)
        # Showing the job logs in nowhere
        self.assertEqual(logins, len(self.cluster.calls()))
        self.browser.execute_script("document.getElementById('ev-job').scrollIntoView()")
        self.shot("31-cluster-job-shown")

        # Sent: the tab Cluster follows it
        self.click("#ev-send")
        self.wait(lambda b: b.find_element(By.ID, "view-cluster").is_displayed())
        self.assertEqual("", self.errors())
        self.assertEqual(logins + 1, len(self.cluster.calls()))

        def row():
            return self.browser.execute_script(
                "const r = document.querySelector('#cj-table tbody tr[data-job=\"dimers-1\"]');"
                "return r ? [...r.cells].map(c => c.textContent) : null")
        self.wait(lambda b: row() is not None)
        self.assertEqual(["dimers-1", "Compare a force field with the data of a molprop file",
                          "Waiting"], row()[:3])
        self.assertEqual("4242", row()[3])
        self.assertEqual("2 processes, 1 h 30 min, GPU", row()[5])
        self.assertEqual("spoel@good:act/jobs/dimers-1", row()[6])
        self.assertIn("Stop", row()[7])
        self.assertIn("1 not done", self.text("#cj-note"))
        remote = self.cluster.dir / "home" / "act" / "jobs" / "dimers-1"
        self.assertEqual(3, len((remote / "selection.dat").read_text().splitlines()))
        self.assertIn("alexandria_chemistry", (remote / "ff.xml").read_text()[:400])

        # The user asks: one login for the state and one for the results
        self.assertTrue(self.browser.find_element(By.ID, "cj-ask").is_enabled())
        self.click("#cj-ask")
        self.wait(lambda b: row()[2] == "Finished")
        self.assertEqual(logins + 3, len(self.cluster.calls()))
        self.assertIn("Show results", row()[7])
        self.assertIn("Forget", row()[7])
        self.assertEqual("", self.text("#cj-note"))
        self.assertFalse(self.browser.find_element(By.ID, "cj-ask").is_enabled())
        self.assertIn("cluster", self.text("#cj-kept"))
        self.shot("32-cluster-jobs")

        # The results, where those of a comparison on this computer are
        self.click('#cj-table button[data-act="open"]')
        self.wait(lambda b: b.find_element(By.ID, "ev-result").is_displayed())
        self.assertTrue(self.browser.find_element(By.ID, "view-molprop").is_displayed())
        self.assertEqual("", self.errors())
        self.assertIn("job dimers-1 on the cluster", self.text("#ev-source"))
        rows = self.browser.execute_script(
            "return [...document.querySelectorAll('#ev-stats tbody tr')]"
            ".map(r => [...r.cells].map(c => c.textContent))")
        self.assertEqual(["Interaction energy", "Train", "2", "1"], rows[0][:4])
        self.wait(lambda b: len(b.find_elements(By.CSS_SELECTOR, "#evplot svg path")) >= 2)
        self.assertIn("2 Train", self.text("#ev-plotnote"))
        saved = self.saved_file("#ev-data", "evaluate-INTERACTION.xvg", "@")
        self.assertIn("-20 -19", saved)
        self.shot("33-cluster-results")
        self.assertEqual(logins + 3, len(self.cluster.calls()))

        # The page is opened again: the job is still there, and is forgotten
        self.open_page()
        self.click('#nav button[data-view="cluster"]')
        self.wait(lambda b: row() is not None)
        self.assertEqual("Finished", row()[2])
        self.click('#cj-table button[data-act="forget"]')
        self.assertIn("with its results here?", row()[7])
        self.click('#cj-table button[data-act="forget"]')
        self.wait(lambda b: b.find_element(By.ID, "cj-empty").is_displayed())
        self.assertTrue((remote / "job.sh").is_file())
        self.assertEqual(logins + 3, len(self.cluster.calls()))
        # Without a cluster the comparison runs on this computer only
        self.click('#nav button[data-view="settings"]')
        self.click("#cl-forget")
        self.wait(lambda b: "No cluster yet" in b.find_element(By.ID, "cl-chips").get_attribute("textContent"))
        self.click('#nav button[data-view="molprop"]')
        self.assertFalse(self.browser.find_element(By.ID, "ev-where").is_displayed())

    def test_e_failed_job_on_the_cluster(self):
        """!
        @brief A job that fails tells why in the tab Cluster, with the lines
        of its output that tell it; the output is shown at such a line, and
        the job is sent again with other processes, its files copied on the
        cluster. A directory stands in for the cluster.
        """
        data = Path(TESTDATA)
        fail = self.cluster.dir / "fail"
        self.addCleanup(lambda: fail.unlink(missing_ok=True))
        fail.write_text("Error: Cannot make topology for water\n"
                        + "Fatal: Training set is empty on one or more of the nodes\n" * 4)
        self.open_page()
        self.click('#nav button[data-view="cluster"]')
        self.click('#nav button[data-view="settings"]')
        for field, value in (("cl-server", "good"), ("cl-user", "spoel"),
                             ("cl-workdir", "act/jobs")):
            box = self.browser.find_element(By.ID, field)
            box.clear()
            box.send_keys(value)
        Select(self.browser.find_element(By.ID, "cl-load")).select_by_value("none")
        self.click("#cl-save")
        self.wait(lambda b: "spoel@good" in b.find_element(By.ID, "cl-chips").get_attribute("textContent"))
        self.click('#nav button[data-view="molprop"]')
        self.browser.find_element(By.ID, "mpw-file").send_keys(
            str(data / "molprop" / "mpdimer.xml"))
        self.wait(lambda b: b.find_element(By.ID, "ev-card").is_displayed())
        self.browser.find_element(By.ID, "ev-fffile").send_keys(
            str(data / "forcefields" / "ACS-pol.xml"))
        Select(self.browser.find_element(By.ID, "ev-ff")).select_by_value("upload")
        self.click('#ev-place button[data-v="cluster"]')
        for field, value in (("ev-jobname", "failing-1"), ("ev-tasks", "4")):
            box = self.browser.find_element(By.ID, field)
            box.clear()
            box.send_keys(value)
        self.browser.execute_script("document.getElementById('run-ev').scrollIntoView()")
        self.click("#run-ev")
        self.wait(lambda b: b.find_element(By.ID, "ev-job").is_displayed())
        self.click("#ev-send")
        self.wait(lambda b: b.find_element(By.ID, "view-cluster").is_displayed())

        def state(name):
            return self.browser.execute_script(
                "const r = document.querySelector('#cj-table tbody tr[data-job=\"%s\"]');"
                "return r ? r.cells[2].textContent : null" % name)
        self.wait(lambda b: state("failing-1") == "Waiting")
        # The user may ask once in half a minute: an earlier test may just have asked
        clicked = [0.0]

        def failed(browser):
            if state("failing-1") == "Failed":
                return True
            ask = browser.find_element(By.ID, "cj-ask")
            if ask.is_enabled() and time.time() - clicked[0] > 5:
                clicked[0] = time.time()
                ask.click()
            return False
        self.wait(failed)
        self.assertEqual("", self.errors())
        reason = self.text("#cj-table .diag-reason")
        self.assertEqual("alexandria stopped: Training set is empty on one or more of the nodes",
                         reason)
        self.assertIn("Each of the 4 processes", self.text("#cj-table .diag"))
        self.assertIn("1 compound got no topology with this force field: water.",
                      self.text("#cj-table .diag"))
        lines = self.browser.execute_script(
            "return [...document.querySelectorAll('#cj-table .diag-lines li')]"
            ".map(li => li.textContent)")
        self.assertEqual("slurm.out:2Fatal: Training set is empty on one or more of the nodes×4",
                         lines[0])
        self.shot("35-cluster-failed")

        # The output, at the line that tells
        self.click('#cj-table .diag-lines button[data-line="2"]')
        self.wait(lambda b: b.find_element(By.ID, "cj-viewer").is_displayed())
        self.assertEqual("failing-1 · slurm.out", self.text("#cj-paneltitle"))
        marked = self.browser.execute_script(
            "return [...document.querySelectorAll('#cj-view mark')].map(m => [m.dataset.line, "
            "m.className])")
        self.assertIn(["2", "here"], marked)
        self.assertIn(["1", ""], marked)
        self.shot("35b-cluster-output")
        files = self.browser.execute_script(
            "return [...document.querySelectorAll('#cj-viewfiles button')].map(b => b.textContent)")
        self.assertIn("job.sh", files)
        self.click('#cj-viewfiles button[data-v="job.sh"]')
        self.wait(lambda b: "#SBATCH -n 4" in b.find_element(By.ID, "cj-view").get_attribute("textContent"))
        self.assertEqual("failing-1 · job.sh", self.text("#cj-paneltitle"))

        # Sent again with one process: the files are copied on the cluster
        fail.unlink()
        logins = len(self.cluster.calls())
        self.click('#cj-table .diag button[data-act="again"]')
        self.wait(lambda b: b.find_element(By.ID, "cj-again").is_displayed())
        self.assertFalse(self.browser.find_element(By.ID, "cj-viewer").is_displayed())
        self.assertEqual("4", self.browser.find_element(By.ID, "cj-again-tasks").get_attribute("value"))
        self.assertIn("fewer processes", self.text("#cj-againnote"))
        box = self.browser.find_element(By.ID, "cj-again-tasks")
        box.clear()
        box.send_keys("1")
        self.click("#cj-again-show")
        self.wait(lambda b: b.find_element(By.ID, "cj-again-job").is_displayed())
        self.assertEqual("failing-1-r2", self.browser.find_element(
            By.ID, "cj-again-name").get_attribute("value"))
        self.assertIn("#SBATCH -n 1", self.text("#cj-again-script"))
        rows = self.browser.execute_script(
            "return [...document.querySelectorAll('#cj-again-files tbody tr')]"
            ".map(r => [...r.cells].map(c => c.textContent))")
        self.assertEqual([["ff.xml", "copied from failing-1"], ["mp.xml", "copied from failing-1"],
                          ["selection.dat", "copied from failing-1"]], rows)
        self.assertEqual(logins, len(self.cluster.calls()))
        self.shot("36-cluster-again")
        self.click("#cj-again-send")
        self.wait(lambda b: state("failing-1-r2") == "Waiting")
        self.assertEqual("", self.errors())
        self.assertFalse(self.browser.find_element(By.ID, "cj-panel").is_displayed())
        self.assertEqual(logins + 1, len(self.cluster.calls()))
        remote = self.cluster.dir / "home" / "act" / "jobs" / "failing-1-r2"
        self.assertEqual("0\n", (remote / "act-exit-status").read_text())
        self.assertEqual((remote.parent / "failing-1" / "mp.xml").read_bytes(),
                         (remote / "mp.xml").read_bytes())
        self.click('#nav button[data-view="settings"]')
        self.click("#cl-forget")

    @unittest.skipUnless(os.path.isdir(TESTDATA), "ACT_TESTDATA is not set")
    def test_c_training_on_the_cluster(self):
        """!
        @brief A training goes to the cluster with an individual for each
        process of the job. The tab Cluster tells how far it is, and its
        results are the trained parameters, how the training went and the
        comparison of the trained force field. A directory stands in for
        the cluster, and a script for the program there.
        """
        data = Path(TESTDATA)
        self.open_page()
        self.click('#nav button[data-view="cluster"]')
        self.click('#nav button[data-view="settings"]')
        for field, value in (("cl-server", "good"), ("cl-user", "spoel"),
                             ("cl-workdir", "act/jobs")):
            box = self.browser.find_element(By.ID, field)
            box.clear()
            box.send_keys(value)
        Select(self.browser.find_element(By.ID, "cl-load")).select_by_value("none")
        self.click("#cl-save")
        self.wait(lambda b: "spoel@good" in b.find_element(By.ID, "cl-chips").get_attribute("textContent"))
        logins = len(self.cluster.calls())

        self.click('#nav button[data-view="molprop"]')
        self.browser.find_element(By.ID, "mpw-file").send_keys(
            str(data / "molprop" / "booze-mono.xml"))
        self.wait(lambda b: b.find_element(By.ID, "ev-card").is_displayed())
        self.browser.find_element(By.ID, "ev-fffile").send_keys(
            str(data / "forcefields" / "halgren-PG.xml"))
        Select(self.browser.find_element(By.ID, "ev-ff")).select_by_value("upload")
        self.click('#ev-what button[data-v="train"]')
        self.assertTrue(self.browser.find_element(By.ID, "ev-where").is_displayed())
        self.assertTrue(self.browser.find_element(By.ID, "tr-procfield").is_displayed())
        self.browser.execute_script("document.getElementById('ev-where').scrollIntoView()")
        self.click('#ev-place button[data-v="cluster"]')
        # The processes are those of the job, two at least for this optimizer
        self.assertFalse(self.browser.find_element(By.ID, "tr-procfield").is_displayed())
        self.assertEqual("1", self.browser.find_element(By.ID, "ev-tasks").get_attribute("value"))
        Select(self.browser.find_element(By.ID, "tr-optimizer")).select_by_value("HYBRID")
        self.assertEqual("4", self.browser.find_element(By.ID, "ev-tasks").get_attribute("value"))
        self.assertEqual("Show the job…", self.text("#run-ev"))
        for field, value in (("tr-fit", "De b"), ("tr-iter", "20"), ("tr-gen", "3"),
                             ("ev-jobname", "train-1")):
            box = self.browser.find_element(By.ID, field)
            box.clear()
            box.send_keys(value)
        self.browser.execute_script("document.getElementById('run-ev').scrollIntoView()")
        self.click("#run-ev")
        self.wait(lambda b: b.find_element(By.ID, "ev-job").is_displayed() or self.errors())
        self.assertEqual("", self.errors())
        script = self.text("#ev-jobscript")
        for part in ("#SBATCH -n 4", "srun alexandria train_ff ", " -optimizer HYBRID ",
                     " -fit 'De b' ", " -max_generations 3 ", " -max_iter 20 ", " -pop_size 4 ",
                     " -o trained.xml", " -progress progress.jsonl"):
            self.assertIn(part, script)
        self.assertNotIn(self._tmp.name, script)
        self.assertEqual(logins, len(self.cluster.calls()))
        self.click("#ev-send")
        self.wait(lambda b: b.find_element(By.ID, "view-cluster").is_displayed())
        self.assertEqual("", self.errors())

        def row():
            return self.browser.execute_script(
                "const r = document.querySelector('#cj-table tbody tr[data-job=\"train-1\"]');"
                "return r ? [...r.cells].map(c => c.textContent) : null")
        self.wait(lambda b: row() is not None)
        self.assertEqual(["train-1", "Train a force field on the data of a molprop file",
                          "Waiting"], row()[:3])

        def asked(browser):
            """Ask until the cluster is asked: not within half a minute of the test before"""
            if row()[2].startswith("Finished"):
                return True
            button = browser.find_element(By.ID, "cj-ask")
            if button.is_enabled():
                button.click()
            return False
        self.wait(asked, 90)
        # How far it was when the cluster was asked
        self.assertEqual("generation 2 of 3 · χ² 46.5", self.text("#cj-table .jobprogress"))
        self.assertEqual(logins + 3, len(self.cluster.calls()))
        self.shot("34-cluster-training")

        self.click('#cj-table button[data-act="open"]')
        self.wait(lambda b: b.find_element(By.ID, "tr-result").is_displayed())
        self.assertTrue(self.browser.find_element(By.ID, "view-molprop").is_displayed())
        self.assertEqual("", self.errors())
        rows = self.browser.execute_script(
            "return [...document.querySelectorAll('#tr-params tbody tr')]"
            ".map(r => [...r.cells].map(c => c.textContent))")
        self.assertEqual([["sigma", "ow", "0.3"]], rows)
        self.assertTrue(self.browser.find_element(By.ID, "tr-run").is_displayed())
        self.wait(lambda b: "generation 2 of 3" in b.find_element(By.ID, "tr-status").text)
        self.assertIn("HYBRID", self.text("#tr-status"))
        self.assertIn("χ² 46.5", self.text("#tr-status"))
        self.assertFalse(self.browser.find_element(By.ID, "tr-stop").is_displayed())
        self.assertGreaterEqual(len(self.browser.find_elements(
            By.CSS_SELECTOR, "#trplot svg path, #trplot svg polyline")), 1)
        self.assertIn("job train-1 on the cluster, the trained force field", self.text("#ev-source"))
        # Named after the molprop file it was trained on
        saved = self.saved_file("#tr-save", "booze-mono-trained.xml", "<alexandria_chemistry")
        self.assertIn("alexandria_chemistry", saved)
        self.shot("35-cluster-training-results")
        self.assertEqual(logins + 3, len(self.cluster.calls()))

        # The output files of the training came back and can be saved, also
        # the log that train_ff writes without being asked
        self.click('#nav button[data-view="cluster"]')
        self.click('#cj-table button[data-act="files"]')
        self.wait(lambda b: b.find_elements(By.CSS_SELECTOR, '#cj-table button[data-save="train_ff.log"]'))
        listed = self.text("#cj-table")
        for name in ("trained.xml", "train_ff.json", "sensitivity.json", "slurm.out"):
            self.assertIn(name, listed)
        self.assertNotIn("selection.dat", listed)
        log = self.saved_file('#cj-table button[data-save="train_ff.log"]', "train_ff.log", "The log")
        self.assertIn("train_ff", log)
        self.assertEqual(logins + 3, len(self.cluster.calls()))

        # Take the job and the cluster away again
        self.click('#nav button[data-view="cluster"]')
        self.click('#cj-table button[data-act="forget"]')
        self.click('#cj-table button[data-act="forget"]')
        self.wait(lambda b: b.find_element(By.ID, "cj-empty").is_displayed())
        self.click('#nav button[data-view="settings"]')
        self.click("#cl-forget")
        self.wait(lambda b: "No cluster yet" in b.find_element(By.ID, "cl-chips").get_attribute("textContent"))


if __name__ == "__main__":
    unittest.main()
