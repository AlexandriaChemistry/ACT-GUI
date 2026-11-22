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
@brief The second virial coefficient as alexandria b2 computes it: what
of its result goes to the browser.

b2 writes, for each temperature, the classical second virial coefficient
of the dimer and its quantum corrections for the force and the torque, and
their total. The page gets them as columns by temperature.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping

## The key of the coefficients in the result of b2
KEY = "Second virial coefficients"
## A temperature as b2 names it
_TEMPERATURE = re.compile(r"T = ([0-9.eE+-]+) K")
## The parts of the coefficient, in the order of the page, by the name in b2
PARTS = {"B2(Total)": "Total", "B2(Classical)": "Classical", "B2(Force)": "Force",
         "B2(Torque1)": "Torque1", "B2(Torque2)": "Torque2"}


def digest(data: Any) -> Any:
    """!
    @brief What of the result of b2 goes to the browser.
    @param data The result of b2.
    @return The result with "virial": the unit, the temperatures from low
    to high and for each part of the coefficient its values at these
    temperatures; the result itself without virial if it has no
    coefficients.
    """
    if not isinstance(data, Mapping):
        return data
    table = data.get(KEY)
    if not isinstance(table, Mapping):
        return data
    rows = []
    for key, values in table.items():
        found = _TEMPERATURE.fullmatch(str(key))
        if found is None or not isinstance(values, Mapping):
            continue
        rows.append((float(found.group(1)), values))
    rows.sort(key=lambda row: row[0])
    parts: Dict[str, List[Any]] = {label: [] for label in PARTS.values()}
    for _, values in rows:
        for name, label in PARTS.items():
            value = values.get(name)
            parts[label].append(value if isinstance(value, (int, float))
                                and not isinstance(value, bool) else None)
    return dict(data, virial={"unit": table.get("Unit", ""),
                              "temperatures": [row[0] for row in rows], "parts": parts})
