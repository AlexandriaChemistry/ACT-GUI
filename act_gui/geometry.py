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
@brief The histograms of the bond lengths, angles and dihedrals that
alexandria geometry_ff writes with -json: what of them goes to the
browser with the result, and one histogram when the page asks for it.

With -samples each bin of a histogram lists some of its values with the
compound, the calculation and the atoms they come from, so that odd values
can be traced. That makes the result large, so the result of the job keeps
only which histograms there are; the page fetches one at a time.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping

## The most values that a bin lists with where they come from
MAX_SAMPLES = 50


class GeometryError(ValueError):
    """!
    @brief A histogram that is not there. The message is for the user.
    """


def digest(data: Any) -> Any:
    """!
    @brief What of the result of geometry_ff goes to the browser: all of it,
    with of the histograms only for each type the identifiers with their
    number of values and unit, the most common first.
    @param data The result of geometry_ff.
    @return The result to keep.
    """
    if not isinstance(data, Mapping) or not isinstance(data.get("histograms"), Mapping):
        return data
    summary: Dict[str, List[Dict[str, Any]]] = {}
    for itype, listed in data["histograms"].items():
        if not isinstance(listed, list):
            continue
        entries = [{"identifier": h.get("identifier", ""), "count": h.get("count", 0),
                    "unit": h.get("unit", "")} for h in listed if isinstance(h, Mapping)]
        summary[str(itype)] = sorted(entries, key=lambda e: (-e["count"], e["identifier"]))
    return dict(data, histograms=summary)


def histogram(data: Any, itype: Any, identifier: Any) -> Mapping[str, Any]:
    """!
    @brief One histogram of the result of geometry_ff.
    @param data       The result of geometry_ff.
    @param itype      The interaction type, such as BONDS or ANGLES.
    @param identifier The identifier, such as c3_b~h_b.
    @return The histogram as geometry_ff wrote it: unit, width of a bin,
    number of values and the bins with, if they were asked for, where their
    values come from.
    @throws GeometryError if there is no such histogram.
    """
    histograms = data.get("histograms") if isinstance(data, Mapping) else None
    listed = histograms.get(itype) if isinstance(histograms, Mapping) and isinstance(itype, str) else None
    if isinstance(listed, list):
        for entry in listed:
            if isinstance(entry, Mapping) and entry.get("identifier") == identifier:
                return entry
    raise GeometryError("There is no histogram of %s %s" % (itype, identifier))
