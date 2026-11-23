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

With -analyse geometry_ff also tells for each bond and angle type whether
its values are normally distributed and whether their standard deviation
is above the tolerance. That goes to the browser with the result: the
numbers of each kind, and with each type what was found for it.

With -samples each bin of a histogram lists some of its values with the
compound, the calculation and the atoms they come from, so that odd values
can be traced. That makes the result large, so the result of the job keeps
only which histograms there are; the page fetches one at a time.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

## The most values that a bin lists with where they come from
MAX_SAMPLES = 50
## What the analysis says about a kind of geometry: its numbers
_KIND_NUMBERS = ("tolerance", "distributions", "tested", "not_normal", "wide")
## What the analysis says about one type: numbers, and yes or no
_ENTRY_NUMBERS = ("average", "sigma", "statistic", "pvalue", "skewness", "kurtosis")
_ENTRY_FLAGS = ("wide", "tested", "normal")


class GeometryError(ValueError):
    """!
    @brief A histogram that is not there. The message is for the user.
    """


def _number(value: Any) -> bool:
    """!
    @brief Whether a value of the result is a number.
    @param value The value.
    @return True for an int or a float, not for true or false.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _analysed(analysis: Any) -> Optional[Dict[str, Any]]:
    """!
    @brief What geometry_ff -analyse found, as the page gets it.
    @param analysis The analysis in the result of geometry_ff.
    @return The test, the significance level (alpha) and the fewest values
    that are tested (minimum); "kinds", for each kind of geometry its unit,
    the tolerance for sigma and the number of types, of types that were
    tested, that are not normal and that are wide; and "types", for each
    kind what was found for each identifier. None without an analysis.
    """
    if not isinstance(analysis, Mapping):
        return None
    kept: Dict[str, Any] = {"test": str(analysis.get("test", "")), "kinds": {}, "types": {}}
    for key in ("alpha", "minimum"):
        if _number(analysis.get(key)):
            kept[key] = analysis[key]
    for itype, kind in analysis.items():
        if not isinstance(kind, Mapping) or not isinstance(kind.get("entries"), list):
            continue
        numbers = {key: kind[key] for key in _KIND_NUMBERS if _number(kind.get(key))}
        kept["kinds"][str(itype)] = dict(numbers, unit=str(kind.get("unit", "")))
        types: Dict[str, Dict[str, Any]] = {}
        for entry in kind["entries"]:
            if not isinstance(entry, Mapping) or not isinstance(entry.get("identifier"), str):
                continue
            found = {key: entry[key] for key in _ENTRY_NUMBERS if _number(entry.get(key))}
            found.update({key: entry[key] for key in _ENTRY_FLAGS
                          if isinstance(entry.get(key), bool)})
            types[entry["identifier"]] = found
        kept["types"][str(itype)] = types
    return kept


def digest(data: Any) -> Any:
    """!
    @brief What of the result of geometry_ff goes to the browser: all of it,
    with of the histograms only for each type the identifiers with their
    number of values and unit, the most common first.
    @param data The result of geometry_ff.
    @return The result to keep. With an analysis of the distributions
    (geometry_ff -analyse), "analysis" has the numbers of each kind of
    geometry, see _analysed(), and each type in the list of histograms has
    what was found for it under "analysis": wide, tested and, if it was
    tested, normal, statistic, pvalue, skewness and kurtosis.
    """
    if not isinstance(data, Mapping) or not isinstance(data.get("histograms"), Mapping):
        return data
    analysed = _analysed(data.get("analysis"))
    found = analysed.pop("types") if analysed is not None else {}
    summary: Dict[str, List[Dict[str, Any]]] = {}
    for itype, listed in data["histograms"].items():
        if not isinstance(listed, list):
            continue
        entries = [{"identifier": h.get("identifier", ""), "count": h.get("count", 0),
                    "unit": h.get("unit", "")} for h in listed if isinstance(h, Mapping)]
        for entry in entries:
            analysis = found.get(str(itype), {}).get(entry["identifier"])
            if analysis is not None:
                entry["analysis"] = analysis
        summary[str(itype)] = sorted(entries, key=lambda e: (-e["count"], e["identifier"]))
    kept = dict(data, histograms=summary)
    if analysed is not None:
        kept["analysis"] = analysed
    else:
        kept.pop("analysis", None)
    return kept


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
