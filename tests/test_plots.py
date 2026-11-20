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

## A stand-in for plotXVG: writes its arguments in a file that looks like a PDF
FAKE_PLOTTER = """import sys
args = sys.argv[1:]
with open(args[args.index("-save") + 1], "w") as out:
    out.write("%PDF-1.4\\n" + " ".join(args) + "\\n")
"""

## A stand-in that fails, one that writes nothing and one that writes rubbish
FAILING_PLOTTER = "import sys\nsys.stderr.write('no display\\n')\nsys.exit(3)\n"
SILENT_PLOTTER = "print('No datasets could be read, sorry!')\n"
RUBBISH_PLOTTER = """import sys
args = sys.argv[1:]
open(args[args.index("-save") + 1], "w").write("<html>not a plot</html>")
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
        @brief The plotter gets the file, the name of the PDF and lines
        instead of symbols, and the PDF lands where it was asked for.
        """
        pdf = self.dir / "out" / "energy.pdf"
        made = plots.make_pdf(self.xvg, pdf, program=self.program(FAKE_PLOTTER))
        self.assertEqual(pdf, made)
        words = pdf.read_text().splitlines()[1].split()
        self.assertEqual(str(self.xvg), words[words.index("-f") + 1])
        self.assertIn("-noshow", words)
        self.assertIn("-quiet", words)
        self.assertEqual("solid", words[words.index("-ls") + 1])
        self.assertNotIn("-coly", words)
        # Nothing else is left behind
        self.assertEqual(["energy.pdf"], [p.name for p in pdf.parent.iterdir()])

    def test_series(self):
        """!
        @brief Only the series that are asked for are plotted.
        """
        pdf = self.dir / "t.pdf"
        plots.make_pdf(self.xvg, pdf, series=["TEMPERATURE"], program=self.program(FAKE_PLOTTER))
        words = pdf.read_text().splitlines()[1].split()
        self.assertEqual("TEMPERATURE", words[words.index("-coly") + 1])

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
        @brief plotXVG makes a PDF file of the energies and of one series.
        """
        for series in ([], ["TEMPERATURE"]):
            pdf = self.dir / ("real%d.pdf" % len(series))
            plots.make_pdf(self.xvg, pdf, series=series)
            self.assertTrue(pdf.read_bytes().startswith(b"%PDF"))
            self.assertGreater(pdf.stat().st_size, 2000)


if __name__ == "__main__":
    unittest.main()
