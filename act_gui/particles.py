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
@brief The charge distributions of the particle types of a force field,
and how far its shells can go from their atoms: what of the description of
a force field goes to the browser to draw them.

A charge of ACT is a point, a Gaussian or a Slater distribution, with the
width 1/zeta. The viewers of the page draw a distributed charge as a
transparent sphere of that radius. The description of a force field by
edit_ff has all its parameters, megabytes for a large one; for the spheres
the kind of distribution and the zeta of each particle type are enough.

A shell cannot go further from its atom than the largest distance of the
force field, its wall: the option shellmaxdistance of the polarization,
0.02 nm if the force field does not give it, 0 for none. A shell at the
wall is held there instead of being where the forces on it are zero, which
the viewers show.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

## The parameters of a particle type that are the width of a charge: that
## of the particle and that of its second charge, by the name in the result
WIDTHS = {"zeta": "zeta", "zetaVS": "zeta_vs"}

## The option of the polarization with the largest distance between a
## shell and its atom in nm, and what ACT takes without it, see
## shellMaxDistance in forcecomputer.cpp
WALL_OPTION = "shellmaxdistance"
DEFAULT_WALL = 0.02


def wall(forcefield: Mapping[str, Any]) -> float:
    """!
    @brief The largest distance between a shell and its atom.
    @param forcefield The object "forcefield" of the result of edit_ff -json.
    @return The distance in nm; 0 if there is none, or if the force field
    has no polarization.
    """
    interactions = forcefield.get("interactions")
    for entry in interactions if isinstance(interactions, list) else []:
        if not isinstance(entry, Mapping) or entry.get("type") != "POLARIZATION":
            continue
        options = entry.get("options")
        given = options.get(WALL_OPTION) if isinstance(options, Mapping) else None
        if given is None:
            return DEFAULT_WALL
        try:
            value = float(given)
        except (TypeError, ValueError):
            return DEFAULT_WALL
        return value if value > 0 else 0.0
    return 0.0


def digest(data: Any) -> Any:
    """!
    @brief What of the description of a force field goes to the browser.
    @param data The result of edit_ff -json.
    @return The kind of charge distribution of the force field
    ("distribution", such as Gaussian, empty if it is not told) and for
    each particle type that has one the zeta of its charge and that of its
    second charge in 1/nm ("types", by the name of the type). A zeta of
    zero is a point charge. "shell_max_distance" is the largest distance
    between a shell and its atom in nm, see wall(). The result itself if it
    describes no force field.
    """
    if not isinstance(data, Mapping) or not isinstance(data.get("forcefield"), Mapping):
        return data
    described = data["forcefield"].get("particletypes")
    limit = wall(data["forcefield"])
    if not isinstance(described, Mapping):
        return {"distribution": "", "types": {}, "shell_max_distance": limit}
    options = described.get("options")
    distribution = options.get("chargedistribution", "") if isinstance(options, Mapping) else ""
    types: Dict[str, Dict[str, float]] = {}
    listed = described.get("types")
    for entry in listed if isinstance(listed, list) else []:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("identifier"), str):
            continue
        widths: Dict[str, float] = {}
        parameters = entry.get("parameters")
        for param in parameters if isinstance(parameters, list) else []:
            if not isinstance(param, Mapping) or param.get("type") not in WIDTHS:
                continue
            value = param.get("value")
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                widths[WIDTHS[param["type"]]] = value
        if widths:
            types[entry["identifier"]] = widths
    return {"distribution": distribution if isinstance(distribution, str) else "",
            "types": types, "shell_max_distance": limit}
