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

import random
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

## The largest weight of a property in a training
MAX_WEIGHT = 1e6

## The data sets that the user can put compounds in by hand: for testing,
## or not used at all. The other compounds are for training.
SETS = ("test", "ignore")

## The seed with which the compounds for testing are drawn, such that the
## same file and the same part give the same compounds
SPLIT_SEED = 20261120


def options(chosen: Any, weights: Any = None) -> Dict[str, Any]:
    """!
    @brief The options of train_ff for the properties that were chosen.
    @param chosen  The names of the properties.
    @param weights How much a property counts in a training, by its name,
    for those that do not count once: numbers above zero. None for none.
    @return The weights and the options for the files.
    @throws EvaluationError if there is no property, one that is not in the
    list, or a weight that is no such number or for another property.
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
    if weights is None:
        return made
    if not isinstance(weights, Mapping):
        raise EvaluationError("The weights of the properties must be an object")
    for name, weight in weights.items():
        if name not in chosen:
            raise EvaluationError("A weight is given for '%s', which is not a property that "
                                  "was chosen" % (name,))
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) \
                or not 0 < weight <= MAX_WEIGHT:
            raise EvaluationError("The weight of a property is a number above 0, at most %g"
                                  % MAX_WEIGHT)
        made[OBSERVABLES[name].weight] = weight
    return made


def selection(molecules: Any, test: float = 0.0, sets: Any = None) -> str:
    """!
    @brief The selection of compounds for train_ff: all of them, unless
    the user chose otherwise.
    @param molecules The compounds of a molprop file, as edit_mp describes
    them: objects with "iupac" and "name".
    @param test      The part of the compounds that is kept for testing,
    drawn at random with a fixed seed; the others are for training. With a
    part above zero and two compounds or more, one compound at least is
    kept for testing and one for training.
    @param sets      The compounds that the user chose by hand: an object
    with "test", the names of those for testing, and "ignore", the names
    of those that are not used. The names are those of the selection, the
    IUPAC name of a compound or else its name. None for none.
    @return The text of the selection file, one line for each compound
    that is used.
    @throws EvaluationError if there are no compounds, if a name cannot be
    written in the file, if a name that was chosen is not in the file or
    is in both sets, if compounds for testing are given by hand and as a
    part, or if no compound is left for training.
    """
    if not isinstance(molecules, Sequence) or isinstance(molecules, (str, bytes)) \
            or not molecules:
        raise EvaluationError("The molprop file has no compounds")
    if len(molecules) > MAX_COMPOUNDS:
        raise EvaluationError("The molprop file has more than %d compounds" % MAX_COMPOUNDS)
    names: List[str] = []
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
            names.append(name)
    by_hand: Dict[str, set] = {key: set() for key in SETS}
    if sets is not None:
        if not isinstance(sets, Mapping) or not set(sets) <= set(SETS):
            raise EvaluationError("The compounds that are chosen are given as those for "
                                  "testing (test) and those that are not used (ignore)")
        for key, chosen in sets.items():
            if not isinstance(chosen, (list, tuple)) or len(chosen) > MAX_COMPOUNDS:
                raise EvaluationError("The compounds that are chosen must be a list of names")
            for name in chosen:
                if not isinstance(name, str) or name not in seen:
                    raise EvaluationError("The molprop file has no compound %r"
                                          % (str(name)[:60],))
                if any(name in other for other in by_hand.values()):
                    raise EvaluationError("The compound %s is chosen twice" % name)
                by_hand[key].add(name)
    if by_hand["test"] and test > 0:
        raise EvaluationError("Give the compounds for testing by hand or as a part of all, "
                              "not both")
    used = [name for name in names if name not in by_hand["ignore"]]
    testing = set(by_hand["test"])
    if test > 0 and len(used) > 1:
        number = min(len(used) - 1, max(1, round(test * len(used))))
        testing = {used[index] for index in
                   random.Random(SPLIT_SEED).sample(range(len(used)), number)}
    if len(testing) >= len(used):
        raise EvaluationError("No compound is left to train on")
    return "".join("%s|%s\n" % (name, "Test" if name in testing else "Train")
                   for name in used)


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
