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
@brief The evaluation of a force field against a molprop file: what
alexandria train_ff compares when it does not optimize, the selection of
compounds it needs, and what of its result goes to the browser.

train_ff compares the properties for which a weight is given, and writes
for each of them a file with the reference value and the value of the
force field for every data point. The user chooses the properties from the
list here; the names of the compounds come from the molprop file as edit_mp
described it, and are checked before they are written in the selection.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Sequence, Tuple


class EvaluationError(ValueError):
    """!
    @brief An evaluation that cannot be started. The message is for the user.
    """


@dataclass(frozen=True)
class Observable:
    """!
    @brief A property that a force field can be compared on.
    """
    ## The name in requests
    name: str
    ## What the page calls it
    label: str
    ## The option of train_ff with the weight of the property
    weight: str
    ## The options of train_ff that write the files with the comparison
    files: Tuple[str, ...] = ()
    ## Whether it is a property of dimers, otherwise of single compounds
    dimer: bool = False


## The properties that can be compared, by name
OBSERVABLES: Dict[str, Observable] = {o.name: o for o in (
    Observable("epot", "Potential energy", "fc_epot", ("epotcorr",)),
    Observable("force", "Forces on the atoms", "fc_force", ("forcecorr",)),
    Observable("freq", "Vibrational frequencies", "fc_freq", ("freqcorr",)),
    Observable("esp", "Electrostatic potential", "fc_esp", ("espcorr",)),
    Observable("mu", "Dipole", "fc_mu", ("dipolecorr",)),
    Observable("quad", "Quadrupole", "fc_quad", ("quadrupolecorr",)),
    Observable("polar", "Polarizability", "fc_polar", ("alphacorr", "isopol", "anisopol")),
    Observable("inter", "Interaction energy", "fc_inter", dimer=True),
    Observable("elec", "Electrostatics", "fc_elec", dimer=True),
    Observable("allelec", "Electrostatics and induction", "fc_allelec", dimer=True),
    Observable("induc", "Induction", "fc_induc", dimer=True),
    Observable("exch", "Exchange", "fc_exch", dimer=True),
    Observable("disp", "Dispersion", "fc_disp", dimer=True),
    Observable("exchind", "Exchange and induction", "fc_exchind", dimer=True),
    Observable("vdw", "Van der Waals: exchange and dispersion", "fc_vdw", dimer=True),
)}

## The files that train_ff writes for dimers without being asked, by the
## name under which they can be fetched
DIMER_FILES: Dict[str, str] = {name: name + ".xvg" for name in (
    "INTERACTION", "COULOMB", "ALLELEC", "INDUCTION", "EXCHANGE", "DISPERSION", "EXCHIND",
    "EXCHDISP")}

## All files with a comparison that an evaluation can have
PLOTS: Tuple[str, ...] = tuple(option for o in OBSERVABLES.values() for option in o.files) \
    + tuple(DIMER_FILES)

## The largest number of compounds in a selection and the longest name
MAX_COMPOUNDS = 200000
MAX_NAME_LENGTH = 300


def options(chosen: Any) -> Dict[str, Any]:
    """!
    @brief The options of train_ff for the properties that were chosen.
    @param chosen The names of the properties.
    @return The weights and the options for the files.
    @throws EvaluationError if there is none, or one that is not in the list.
    """
    if not isinstance(chosen, (list, tuple)) or not chosen:
        raise EvaluationError("Choose at least one property to compare")
    if len(set(map(str, chosen))) != len(chosen):
        raise EvaluationError("A property is given twice")
    made: Dict[str, Any] = {}
    for name in chosen:
        if not isinstance(name, str) or name not in OBSERVABLES:
            raise EvaluationError("Unknown property '%s'" % (name,))
        observable = OBSERVABLES[name]
        made[observable.weight] = 1
        for option in observable.files:
            made[option] = True
    return made


def selection(molecules: Any) -> str:
    """!
    @brief The selection of compounds for train_ff: all of them.
    @param molecules The compounds of a molprop file, as edit_mp describes
    them: objects with "iupac" and "name".
    @return The text of the selection file, one line for each compound.
    @throws EvaluationError if there are no compounds, or if a name cannot
    be written in the file.
    """
    if not isinstance(molecules, Sequence) or isinstance(molecules, (str, bytes)) \
            or not molecules:
        raise EvaluationError("The molprop file has no compounds")
    if len(molecules) > MAX_COMPOUNDS:
        raise EvaluationError("The molprop file has more than %d compounds" % MAX_COMPOUNDS)
    lines: List[str] = []
    seen = set()
    for molecule in molecules:
        name = None
        if isinstance(molecule, Mapping):
            # train_ff looks compounds up by this name
            name = molecule.get("iupac") or molecule.get("name")
        if not isinstance(name, str) or not name.strip():
            raise EvaluationError("A compound in the molprop file has no name")
        # The bar separates the name from the data set, and a line is a compound
        if len(name) > MAX_NAME_LENGTH or "|" in name or not name.isprintable():
            raise EvaluationError("The name of a compound cannot be used in a selection: "
                                  "%r" % name[:60])
        if name not in seen:
            seen.add(name)
            lines.append("%s|Train\n" % name)
    return "".join(lines)


def digest(data: Any) -> Any:
    """!
    @brief What of the result of train_ff goes to the browser. The atoms of
    every compound with their forces, and every data point, are left out:
    the points are in the files with the comparison.
    @param data The result of train_ff.
    @return The statistics, the outliers, the timings and for each compound
    its name, its data set and its number of data points.
    """
    if not isinstance(data, Mapping):
        return data
    compounds = []
    molecules = data.get("molecules") or {}
    listed = molecules.values() if isinstance(molecules, Mapping) else molecules
    for molecule in listed:
        if not isinstance(molecule, Mapping):
            continue
        points = molecule.get("interaction_energies")
        compounds.append({"name": molecule.get("name", ""),
                          "dataset": molecule.get("dataset", ""),
                          "points": len(points) if isinstance(points, Mapping) else 0})
    statistics = {}
    for dataset, values in (data.get("statistics") or {}).items():
        if isinstance(values, Mapping) and values:
            statistics[dataset] = values
    outliers = {}
    for name, entries in (data.get("outliers") or {}).items():
        if isinstance(entries, Mapping):
            outliers[name] = [dict(entry, calculation=key) for key, entry in entries.items()
                              if isinstance(entry, Mapping)][:20]
    return {"compounds": compounds, "statistics": statistics, "outliers": outliers}


def describe() -> List[Dict[str, Any]]:
    """!
    @brief The properties that can be compared, for the browser.
    @return For each its name, what the page calls it and whether it is a
    property of dimers.
    """
    return [{"name": o.name, "label": o.label, "dimer": o.dimer} for o in OBSERVABLES.values()]
