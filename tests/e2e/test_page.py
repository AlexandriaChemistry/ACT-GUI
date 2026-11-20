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
        cls._executable = act.config.executable
        act.config.executable = ALEXANDRIA
        cls._tmp = tempfile.TemporaryDirectory()
        # The settings of the user go to a file of the test, and a small
        # program stands in for ssh: no test logs in anywhere
        from tests.test_cluster import FakeServer  # pylint: disable=import-outside-toplevel
        cls.cluster = FakeServer(Path(cls._tmp.name) / "cluster")
        cls.config = Path(cls._tmp.name) / "config" / "settings.json"
        cls.settings = Settings(data_dir=Path(cls._tmp.name), token="e2e-token",
                                config_file=cls.config,
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
        element.click()
        target = self.downloads / name

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
        # The spectrum on paper
        self.saved_pdf("ir-pdf", "nma-ir.pdf")
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
        self.click("#run-omm")
        target = self.downloads / "act-openmm.zip"
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

    def saved_file(self, selector, name, start):
        """!
        @brief Press a button that saves a file and check the file.
        @param selector The CSS selector of the button.
        @param name     The name the file should get.
        @param start    What the file starts with.
        @return The text of the file.
        """
        target = self.downloads / name
        if target.exists():
            target.unlink()
        self.click(selector)
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
        self.saved_file("#g-save-vs", "vsites.json", "{")

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
        # The particle types can be looked at, not changed
        self.show_parameters("@particles", "c3_s")
        rows = self.parameter_rows()
        self.assertEqual("c3_s", rows[0][0])
        self.assertFalse(self.browser.find_elements(By.CSS_SELECTOR, "#p-table tr.pick"))
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
        log = self.downloads / "geometry_ff-g.log"
        self.click("#geo-log")
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
        rows = self.browser.find_elements(By.CSS_SELECTOR, "#mpw-table tbody tr")
        self.assertEqual(3, len(rows))
        self.assertIn("1-propanol#ethane", rows[0].text)
        # The details of a compound
        self.assertFalse(self.browser.find_element(By.ID, "mpw-detail").is_displayed())
        self.click("#mpw-table tbody tr.pick td")
        self.assertEqual("1-propanol#ethane", self.text("#mpw-dname"))
        self.assertEqual(2, len(self.browser.find_elements(By.CSS_SELECTOR, "#mpw-dfrag tbody tr")))
        self.assertIn("InChI=1S/C2H6", self.text("#mpw-dfrag"))
        self.shot("20-molprop")
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
            "return [...document.querySelectorAll('#ev-observables input')]"
            ".map(b => b.dataset.name + (b.checked ? '*' : ''))")
        self.assertEqual("inter*", offered[0])
        self.assertIn("elec", offered)
        self.assertNotIn("epot", offered)
        self.click('#ev-observables input[data-name="elec"]')
        # The force field of the test data, since the default is for other compounds
        self.browser.find_element(By.ID, "ev-fffile").send_keys(
            str(data / "forcefields" / "ACS-pol.xml"))
        Select(self.browser.find_element(By.ID, "ev-ff")).select_by_value("upload")
        self.assertEqual("ACS-pol.xml", self.browser.find_element(
            By.CSS_SELECTOR, '#ev-ff option[value="upload"]').text)
        self.assertFalse(self.browser.find_element(By.ID, "ev-result").is_displayed())
        self.click("#run-ev")
        self.wait(lambda b: b.find_element(By.ID, "ev-pill").text == "Done")
        self.assertEqual("", self.errors())
        self.assertTrue(self.browser.find_element(By.ID, "ev-result").is_displayed())
        self.assertEqual("3 compounds", self.text("#ev-ncomp"))
        rows = self.browser.execute_script(
            "return [...document.querySelectorAll('#ev-stats tbody tr')]"
            ".map(r => [...r.cells].map(c => c.textContent))")
        interaction = [row for row in rows if row[0] == "INTERACTION"][0]
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
        # Another property
        Select(self.browser.find_element(By.ID, "ev-plotsel")).select_by_value("COULOMB")
        self.wait(lambda b: "Electrostatics" in b.find_element(
            By.CSS_SELECTOR, "#evplot svg").get_attribute("aria-label"))
        # The files
        saved = self.saved_file("#ev-data", "evaluate-COULOMB.xvg", "#")
        self.assertIn('legend "Train"', saved)
        log = self.saved_file("#ev-log", "evaluate-g.log", "")
        self.assertIn("train_ff", log)
        from act_gui import plots  # pylint: disable=import-outside-toplevel
        if plots.available():
            target = self.downloads / "evaluate-COULOMB.pdf"
            self.click("#ev-pdf")
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
        self.shot("22-molprop-default")

    # --- the cluster for long jobs ---

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
        self.assertFalse(self.browser.find_element(By.ID, "cl-check").is_enabled())
        self.assertFalse(self.browser.find_element(By.ID, "cl-forget").is_displayed())
        self.assertIn(str(self.config), self.text("#cl-file"))

        def fill(server, user, workdir):
            for field, value in (("cl-server", server), ("cl-user", user), ("cl-workdir", workdir)):
                box = self.browser.find_element(By.ID, field)
                box.clear()
                box.send_keys(value)
        # Settings that cannot be are refused and not kept
        fill("-oProxyCommand=evil", "spoel", "act")
        self.click("#cl-save")
        self.wait(lambda b: "name of a server" in self.errors())
        self.assertFalse(self.config.exists())
        fill("good", "spoel", "act/jobs/")
        self.click("#cl-save")
        self.wait(lambda b: "spoel@good" in b.find_element(By.ID, "cl-chips").text)
        self.assertEqual("", self.errors())
        self.assertEqual({"cluster": {"server": "good", "user": "spoel", "workdir": "act/jobs"}},
                         json.loads(self.config.read_text()))
        self.assertEqual("act/jobs", self.browser.find_element(
            By.ID, "cl-workdir").get_attribute("value"))
        # The login
        self.click("#cl-check")
        self.wait(lambda b: b.find_element(By.ID, "cl-pill").text == "Ready")
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
        self.wait(lambda b: b.find_element(By.ID, "cl-pill").text == "Not ready")
        self.assertIn("is not there", self.text("#cl-result"))
        # The page is opened again: the settings are those of the file
        self.open_page()
        self.click('#nav button[data-view="cluster"]')
        self.wait(lambda b: b.find_element(By.ID, "cl-server").get_attribute("value") == "good")
        self.assertEqual("act/jobs/nothere", self.browser.find_element(
            By.ID, "cl-workdir").get_attribute("value"))
        self.click("#cl-forget")
        self.wait(lambda b: "No cluster yet" in b.find_element(By.ID, "cl-chips").text)
        self.assertEqual({}, json.loads(self.config.read_text()))
        self.assertEqual("", self.browser.find_element(By.ID, "cl-server").get_attribute("value"))


if __name__ == "__main__":
    unittest.main()
