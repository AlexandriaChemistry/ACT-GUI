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
@brief Plots as PDF files, made with plotXVG.

The plots on the page are drawn by the browser. A plot for a paper or a
report is made here from the xvg file that alexandria wrote, with plotXVG,
which is what users of ACT get on the command line as well.

plotXVG runs in a process of its own, without a window: matplotlib is not
made for use from several threads of a server, and a plot that goes wrong
cannot take the server with it. The process calls the function plot() of
plotXVG, not its command: the command asks for a window, which a server
does not have. What to plot is given as one argument in JSON format, never
as a command line for a shell, and the only words in it that come from a
request are names of series, which must be names that are in the file.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import List, Optional, Sequence, Tuple


class PlotError(RuntimeError):
    """!
    @brief A plot that could not be made. The message is for the user.
    """


## What the name of a series looks like, such as EPOT or TEMPERATURE
_SERIES_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
## A line of an xvg file that names a series
_LEGEND_LINE = re.compile(r'^@\s*s\d+\s+legend\s+"([^"]*)"')
## The title and the label of the y-axis in the header of an xvg file
_TITLE_LINE = re.compile(r'^@\s*title\s+"')
_YLABEL_LINE = re.compile(r'^@\s*yaxis\s+label\s+"')
## What a title or a label that replaces them may look like
_LABEL_TEXT = re.compile(r"^[A-Za-z0-9 ()/.,^+-]{1,80}$")
## The largest number of series in one plot
MAX_SERIES = 8
## The program that makes a plot: it takes one argument, an object in JSON
## format with "files" and the "options" of the function plot() of plotXVG.
## The backend that draws without a display is chosen before plotXVG is loaded.
_RUNNER = """
import json
import sys
import matplotlib
matplotlib.use("Agg")
import plotxvg
request = json.loads(sys.argv[1])
plotxvg.plot(request["files"], **request["options"])
"""
## One plot is made at a time: a plot takes a second or two of one processor
_one_at_a_time = threading.Lock()


def available() -> bool:
    """!
    @brief Whether plotXVG is installed.
    @return True if PDF files can be made.
    """
    try:
        return importlib.util.find_spec("plotxvg") is not None
    except (ImportError, ValueError):
        return False


def legends(xvg: Path) -> List[str]:
    """!
    @brief The names of the series in an xvg file.
    @param xvg The file.
    @return The names in the order of the columns, empty for a file with
    one series without a name.
    """
    names: List[str] = []
    with open(xvg, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.startswith(("@", "#")):
                # The header is over
                break
            match = _LEGEND_LINE.match(line)
            if match:
                names.append(match.group(1))
    return names


def x_range(xvg: Path) -> Tuple[float, float]:
    """!
    @brief The smallest and the largest x in an xvg file.
    @param xvg The file.
    @return The two numbers.
    @throws PlotError if the file has no data.
    """
    low, high = float("inf"), float("-inf")
    with open(xvg, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith(("@", "#", "&")):
                continue
            words = line.split()
            try:
                value = float(words[0])
            except (IndexError, ValueError):
                continue
            low, high = min(low, value), max(high, value)
    if low > high:
        raise PlotError("There is no data to plot")
    return low, high


def check_series(xvg: Path, series: Sequence[str]) -> List[str]:
    """!
    @brief Check the series that a request asks for.
    @param xvg    The file.
    @param series The names.
    @return The names as a list.
    @throws PlotError if there are too many, or if one is not a series of
    the file.
    """
    chosen = list(series)
    if len(chosen) > MAX_SERIES:
        raise PlotError("At most %d series fit in one plot" % MAX_SERIES)
    if chosen:
        known = legends(xvg)
        for name in chosen:
            if not isinstance(name, str) or not _SERIES_NAME.match(name) or name not in known:
                raise PlotError("The file has no series '%s'" % (name,))
    return chosen


def _relabelled(xvg: Path, copy: Path, title: Optional[str], ylabel: Optional[str]) -> None:
    """!
    @brief Copy an xvg file with another title or label of the y-axis.
    @param xvg    The file.
    @param copy   Where the copy goes.
    @param title  The new title, None to keep the one of the file.
    @param ylabel The new label of the y-axis, None to keep it.
    @throws PlotError for a text that does not fit in the header.
    """
    for text in (title, ylabel):
        if text is not None and not _LABEL_TEXT.match(text):
            raise PlotError("The label '%s' cannot be used in a plot" % text)
    with open(xvg, "r", encoding="utf-8", errors="replace") as source, \
            open(copy, "w", encoding="utf-8") as target:
        for line in source:
            if title is not None and _TITLE_LINE.match(line):
                line = '@    title "%s"\n' % title
            elif ylabel is not None and _YLABEL_LINE.match(line):
                line = '@    yaxis  label "%s"\n' % ylabel
            target.write(line)


def make_pdf(xvg: Path, pdf: Path, series: Sequence[str] = (), reverse_x: bool = False,
             timeout: float = 60.0, program: Optional[Sequence[str]] = None,
             title: Optional[str] = None, ylabel: Optional[str] = None) -> Path:
    """!
    @brief Make a PDF file of an xvg file.
    @param xvg     The xvg file.
    @param pdf     Where the PDF file goes. Its directory is made if needed.
    @param series  The names of the series to plot, all if empty.
    @param reverse_x Whether the x-axis runs from large to small values, as
    spectroscopists draw wavenumbers.
    @param timeout Seconds after which the plotter is stopped.
    @param title   A title for the plot instead of that of the file.
    @param ylabel  A label for the y-axis instead of that of the file, for
    a series with another unit than the file says, such as the temperature
    in a file with energies.
    @param program The words that start the plotter, which gets one more
    argument: an object in JSON format with the files and the options of
    plotxvg.plot(). plotXVG with the Python that runs this server if None.
    @return The path of the PDF file.
    @throws PlotError if the plot cannot be made. No file is left then.
    """
    xvg = Path(xvg)
    pdf = Path(pdf)
    if not xvg.is_file():
        raise PlotError("There is no data to plot")
    chosen = check_series(xvg, series)
    if program is None:
        if not available():
            raise PlotError("plotXVG is not installed on the server, so plots cannot be "
                            "saved as PDF. Install it with 'conda install -c "
                            "alexandriachemistry plotxvg' or 'pip install plotxvg'.")
        program = [sys.executable, "-c", _RUNNER]
    pdf.parent.mkdir(parents=True, exist_ok=True)
    # Written under another name first, so that a file with the final name
    # is always a complete plot. The extension tells plotXVG the format.
    partial = pdf.with_name("%s.part%s.pdf" % (pdf.stem, uuid.uuid4().hex[:8]))
    # Lines instead of symbols, and no window
    options = {"save": str(partial), "noshow": True, "linestyle": ["solid"]}
    if chosen:
        options["coly"] = chosen
    if reverse_x:
        # plotXVG draws the axis from xmin to xmax, also when the first is
        # the larger one. It takes zero for "not given", so a limit at zero
        # is moved by less than can be seen.
        low, high = x_range(xvg)
        tiny = 1e-9 * max(abs(high), abs(low), 1.0)
        options["xmin"] = high or tiny
        options["xmax"] = low or -tiny
    source = xvg
    relabelled = None
    if title is not None or ylabel is not None:
        # plotXVG takes the labels from the file, so it gets a copy with
        # the other labels
        relabelled = pdf.with_name("%s.part%s.xvg" % (pdf.stem, uuid.uuid4().hex[:8]))
        _relabelled(xvg, relabelled, title, ylabel)
        source = relabelled
    command = list(program) + [json.dumps({"files": [str(source)], "options": options})]
    # Without a display, also for a plotter that looks at the environment
    environment = dict(os.environ, MPLBACKEND="Agg")
    try:
        with _one_at_a_time:
            try:
                done = subprocess.run(command, cwd=str(xvg.parent), env=environment,
                                      stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, timeout=timeout, check=False)
            except subprocess.TimeoutExpired as exc:
                raise PlotError("Making the plot took too long") from exc
            except OSError as exc:
                raise PlotError("The plotter could not be started: %s" % exc) from exc
        output = done.stdout.decode("utf-8", errors="replace").strip()
        if done.returncode != 0 or not partial.is_file():
            raise PlotError("The plot could not be made. %s" % output[-500:])
        with open(partial, "rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise PlotError("The plotter did not write a PDF file")
        os.replace(partial, pdf)
    finally:
        for leftover in (partial, relabelled):
            if leftover is not None and leftover.exists():
                leftover.unlink()
    return pdf
