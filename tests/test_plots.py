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
@brief Tests of the plots that are saved as PDF files with plotXVG.

Most tests use a small program in the place of plotXVG, so that they run
without matplotlib. The last one makes a real PDF file when plotXVG is
installed.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from act_gui import plots

## An xvg file as simulate writes it, with three series
XVG = """# This file was created by a test
@    title "ACT Energies"
@    xaxis  label "Time (ps)"
@    yaxis  label "Energy (kJ/mol)"
@TYPE xy
@ legend on
@ s0 legend "EPOT"
@ s1 legend "EKIN"
@ s2 legend "TEMPERATURE"
0.000 -10.0 1.0 290.0
0.001 -10.5 1.5 300.0
0.002 -10.2 1.2 295.0
0.003 -10.4 1.4 299.0
"""

## A stand-in for plotXVG: writes its argument in a file that looks like a PDF
## and after it the header of the file it was given
FAKE_PLOTTER = """import json, sys
request = json.loads(sys.argv[1])
header = [line for line in open(request["files"][0]) if line.startswith("@")]
with open(request["options"]["save"], "w") as out:
    out.write("%PDF-1.4\\n" + sys.argv[1] + "\\n" + "".join(header))
"""

## A stand-in that fails, one that writes nothing and one that writes rubbish
FAILING_PLOTTER = "import sys\nsys.stderr.write('no display\\n')\nsys.exit(3)\n"
SILENT_PLOTTER = "print('No datasets could be read, sorry!')\n"
RUBBISH_PLOTTER = """import json, sys
request = json.loads(sys.argv[1])
open(request["options"]["save"], "w").write("<html>not a plot</html>")
"""


class PlotTest(unittest.TestCase):
    """!
    @brief An xvg file becomes a PDF file.
    """

    def setUp(self):
        """!
        @brief Make a directory with an xvg file and the stand-in programs.
        """
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.xvg = self.dir / "energy.xvg"
        self.xvg.write_text(XVG)

    def tearDown(self):
        """!
        @brief Remove the files.
        """
        self._tmp.cleanup()

    def program(self, source: str):
        """!
        @brief The command of a stand-in for plotXVG.
        @param source The Python code of the stand-in.
        @return The words of the command.
        """
        path = self.dir / ("plotter%d.txt" % len(list(self.dir.glob("plotter*"))))
        path.write_text(source)
        return [sys.executable, str(path)]

    def request(self, pdf: Path) -> dict:
        """!
        @brief What the stand-in for plotXVG was asked to plot.
        @param pdf The file that the stand-in wrote.
        @return The object with the files and the options.
        """
        return json.loads(pdf.read_text().splitlines()[1])

    def test_legends(self):
        """!
        @brief The names of the series are read from the file.
        """
        self.assertEqual(["EPOT", "EKIN", "TEMPERATURE"], plots.legends(self.xvg))
        empty = self.dir / "ir.xvg"
        empty.write_text('@    title "Infrared spectrum"\n0 1\n')
        self.assertEqual([], plots.legends(empty))

    def test_pdf_is_made(self):
        """!
        @brief The plotter gets the file, the name of the PDF, lines
        instead of symbols and no window, and the PDF lands where it was
        asked for.
        """
        pdf = self.dir / "out" / "energy.pdf"
        made = plots.make_pdf(self.xvg, pdf, program=self.program(FAKE_PLOTTER))
        self.assertEqual(pdf, made)
        asked = self.request(pdf)
        self.assertEqual([str(self.xvg)], asked["files"])
        self.assertIs(True, asked["options"]["noshow"])
        self.assertEqual(["solid"], asked["options"]["linestyle"])
        self.assertNotIn("coly", asked["options"])
        self.assertNotIn("xmin", asked["options"])
        # Nothing else is left behind
        self.assertEqual(["energy.pdf"], [p.name for p in pdf.parent.iterdir()])

    def test_series(self):
        """!
        @brief Only the series that are asked for are plotted.
        """
        pdf = self.dir / "t.pdf"
        plots.make_pdf(self.xvg, pdf, series=["TEMPERATURE"], program=self.program(FAKE_PLOTTER))
        self.assertEqual(["TEMPERATURE"], self.request(pdf)["options"]["coly"])

    def test_x_range(self):
        """!
        @brief The smallest and largest x of the data are found.
        """
        self.assertEqual((0.0, 0.003), plots.x_range(self.xvg))
        empty = self.dir / "empty.xvg"
        empty.write_text('@    title "Nothing"\n')
        with self.assertRaises(plots.PlotError):
            plots.x_range(empty)

    def test_reversed_axis(self):
        """!
        @brief A reversed x-axis is asked for with a minimum that is larger
        than the maximum, both from the data, and only then.
        """
        pdf = self.dir / "rev.pdf"
        plots.make_pdf(self.xvg, pdf, reverse_x=True, program=self.program(FAKE_PLOTTER))
        options = self.request(pdf)["options"]
        low, high = options["xmin"], options["xmax"]
        self.assertEqual(0.003, low)
        self.assertLess(high, low)
        # plotXVG takes zero for "not given"
        self.assertNotEqual(0.0, high)
        self.assertAlmostEqual(0.0, high, places=6)
        spectrum = self.dir / "ir.xvg"
        spectrum.write_text("@ title \"IR\"\n-62 0.1\n100 0.5\n3300 0.2\n&\n")
        plots.make_pdf(spectrum, pdf, reverse_x=True, program=self.program(FAKE_PLOTTER))
        options = self.request(pdf)["options"]
        self.assertEqual([3300, -62], [options["xmin"], options["xmax"]])
        plots.make_pdf(self.xvg, pdf, program=self.program(FAKE_PLOTTER))
        self.assertNotIn("xmin", self.request(pdf)["options"])

    def test_labels(self):
        """!
        @brief A plot of the temperature does not say "Energy": the title
        and the label of the y-axis can be replaced, in a copy of the file
        that is gone afterwards.
        """
        pdf = self.dir / "labels.pdf"
        plots.make_pdf(self.xvg, pdf, series=["TEMPERATURE"], title="Temperature",
                       ylabel="Temperature (K)", program=self.program(FAKE_PLOTTER))
        text = pdf.read_text()
        self.assertIn('title "Temperature"', text)
        self.assertIn('yaxis  label "Temperature (K)"', text)
        self.assertNotIn("Energy", text)
        # The other lines of the header are kept
        self.assertIn('xaxis  label "Time (ps)"', text)
        self.assertIn('legend "TEMPERATURE"', text)
        # The file of the program is as it was, and the copy is gone
        self.assertEqual(XVG, self.xvg.read_text())
        self.assertNotEqual(str(self.xvg), self.request(pdf)["files"][0])
        self.assertEqual(["energy.xvg"], [p.name for p in self.dir.glob("*.xvg")])
        # Text that could end the quotes of the label is refused
        with self.assertRaises(plots.PlotError):
            plots.make_pdf(self.xvg, pdf, ylabel='K" \n@ title "x', program=self.program(FAKE_PLOTTER))

    def test_series_that_are_refused(self):
        """!
        @brief A series must be in the file: nothing else reaches the
        command line of the plotter.
        """
        pdf = self.dir / "bad.pdf"
        for series in (["ETOT"], ["-save"], ["EPOT", "../x"], [""], ["EPOT"] * 9, [3]):
            with self.assertRaises(plots.PlotError):
                plots.make_pdf(self.xvg, pdf, series=series, program=self.program(FAKE_PLOTTER))
            self.assertFalse(pdf.exists())

    def test_missing_file(self):
        """!
        @brief A file that is not there is reported.
        """
        with self.assertRaises(plots.PlotError):
            plots.make_pdf(self.dir / "nothere.xvg", self.dir / "x.pdf",
                           program=self.program(FAKE_PLOTTER))

    def test_plotter_that_fails(self):
        """!
        @brief A failing plotter, one that writes nothing and one that
        writes something else than a PDF give an error, and no file.
        """
        pdf = self.dir / "fail.pdf"
        with self.assertRaises(plots.PlotError) as caught:
            plots.make_pdf(self.xvg, pdf, program=self.program(FAILING_PLOTTER))
        self.assertIn("no display", str(caught.exception))
        with self.assertRaises(plots.PlotError) as caught:
            plots.make_pdf(self.xvg, pdf, program=self.program(SILENT_PLOTTER))
        self.assertIn("No datasets", str(caught.exception))
        with self.assertRaises(plots.PlotError):
            plots.make_pdf(self.xvg, pdf, program=self.program(RUBBISH_PLOTTER))
        self.assertEqual([], list(self.dir.glob("*.pdf")) + list(self.dir.glob("*.part*")))

    def test_plotter_that_hangs(self):
        """!
        @brief A plotter that does not finish is stopped.
        """
        with self.assertRaises(plots.PlotError) as caught:
            plots.make_pdf(self.xvg, self.dir / "slow.pdf", timeout=0.5,
                           program=self.program("import time\ntime.sleep(30)\n"))
        self.assertIn("too long", str(caught.exception))

    @unittest.skipUnless(plots.available(), "plotXVG is not installed")
    def test_real_plotter(self):
        """!
        @brief plotXVG makes a PDF file of the energies and of one series,
        also on a machine without a display and whatever the environment
        says about the backend of matplotlib.
        """
        saved = os.environ.get("MPLBACKEND")
        os.environ["MPLBACKEND"] = "TkAgg"
        self.addCleanup(lambda: os.environ.pop("MPLBACKEND", None) if saved is None
                        else os.environ.__setitem__("MPLBACKEND", saved))
        for series, reverse, ylabel in (([], False, None), (["TEMPERATURE"], False, "Temperature (K)"),
                                        (["EPOT"], True, None)):
            pdf = self.dir / ("real%d%d.pdf" % (len(series), reverse))
            plots.make_pdf(self.xvg, pdf, series=series, reverse_x=reverse, ylabel=ylabel)
            self.assertTrue(pdf.read_bytes().startswith(b"%PDF"))
            self.assertGreater(pdf.stat().st_size, 2000)


if __name__ == "__main__":
    unittest.main()
