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

plotXVG runs as a program of its own, without a window: matplotlib is not
made for use from several threads of a server, and a plot that goes wrong
cannot take the server with it. It gets a list of arguments, never a
command line for a shell, and the only words in that list that come from a
request are names of series, which must be names that are in the file.
"""
from __future__ import annotations

import importlib.util
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
## The largest number of series in one plot
MAX_SERIES = 8
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


def make_pdf(xvg: Path, pdf: Path, series: Sequence[str] = (), reverse_x: bool = False,
             timeout: float = 60.0, program: Optional[Sequence[str]] = None) -> Path:
    """!
    @brief Make a PDF file of an xvg file.
    @param xvg     The xvg file.
    @param pdf     Where the PDF file goes. Its directory is made if needed.
    @param series  The names of the series to plot, all if empty.
    @param reverse_x Whether the x-axis runs from large to small values, as
    spectroscopists draw wavenumbers.
    @param timeout Seconds after which the plotter is stopped.
    @param program The words that start the plotter, plotXVG with the
    Python that runs this server if None.
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
        program = [sys.executable, "-m", "plotxvg.cli"]
    pdf.parent.mkdir(parents=True, exist_ok=True)
    # Written under another name first, so that a file with the final name
    # is always a complete plot. The extension tells plotXVG the format.
    partial = pdf.with_name("%s.part%s.pdf" % (pdf.stem, uuid.uuid4().hex[:8]))
    command = list(program) + ["-f", str(xvg), "-save", str(partial), "-noshow", "-quiet",
                               "-ls", "solid"]
    if chosen:
        command += ["-coly"] + chosen
    if reverse_x:
        # plotXVG draws the axis from -xmin to -xmax, also when the first is
        # the larger one. It takes zero for "not given", so a limit at zero
        # is moved by less than can be seen.
        low, high = x_range(xvg)
        tiny = 1e-9 * max(abs(high), abs(low), 1.0)
        command += ["-xmin", "%.10g" % (high or tiny), "-xmax", "%.10g" % (low or -tiny)]
    # Without a display
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
        if partial.exists():
            partial.unlink()
    return pdf
