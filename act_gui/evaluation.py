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

import os
import random
import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import molfile


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
    ## The names under which train_ff reports its statistics
    statistics: Tuple[str, ...] = ()
    ## The files with the comparison that can be plotted, by the name under
    ## which they are fetched; for dimers those that train_ff writes always
    terms: Tuple[str, ...] = ()

    @property
    def plots(self) -> Tuple[str, ...]:
        """!
        @brief The files with the comparison that can be plotted.
        @return Their names, as they are fetched.
        """
        return self.files + self.terms


## The properties that can be compared, by name
OBSERVABLES: Dict[str, Observable] = {o.name: o for o in (
    Observable("epot", "Potential energy", "fc_epot", ("epotcorr",),
               statistics=("Potential energy",)),
    Observable("force", "Forces on the atoms", "fc_force", ("forcecorr",),
               statistics=("RMS Force",)),
    Observable("freq", "Vibrational frequencies", "fc_freq", ("freqcorr",),
               statistics=("Frequencies",)),
    Observable("esp", "Electrostatic potential", "fc_esp", ("espcorr",), statistics=("ESP",)),
    Observable("mu", "Dipole", "fc_mu", ("dipolecorr",), statistics=("dipole",)),
    Observable("quad", "Quadrupole", "fc_quad", ("quadrupolecorr",),
               statistics=("quadrupole",)),
    Observable("polar", "Polarizability", "fc_polar", ("alphacorr", "isopol", "anisopol"),
               statistics=("Polariz. components", "Isotropic Polariz.",
                           "Anisotropic Polariz.")),
    Observable("inter", "Interaction energy", "fc_inter", dimer=True,
               statistics=("INTERACTION",), terms=("INTERACTION",)),
    Observable("elec", "Electrostatics", "fc_elec", dimer=True,
               statistics=("COULOMB",), terms=("COULOMB",)),
    Observable("allelec", "Electrostatics and induction", "fc_allelec", dimer=True,
               statistics=("ALLELEC",), terms=("ALLELEC",)),
    Observable("induc", "Induction", "fc_induc", dimer=True,
               statistics=("INDUCTION",), terms=("INDUCTION",)),
    Observable("exch", "Exchange", "fc_exch", dimer=True,
               statistics=("EXCHANGE",), terms=("EXCHANGE",)),
    Observable("disp", "Dispersion", "fc_disp", dimer=True,
               statistics=("DISPERSION",), terms=("DISPERSION",)),
    Observable("exchind", "Exchange and induction", "fc_exchind", dimer=True,
               statistics=("EXCHIND",), terms=("EXCHIND",)),
    Observable("vdw", "Van der Waals: exchange and dispersion", "fc_vdw", dimer=True,
               statistics=("EXCHDISP",), terms=("EXCHDISP",)),
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

## The most data points of a term of a compound that are sent to the browser
MAX_POINTS = 20000

## The most points of an energy term of all compounds that are sent to the browser
MAX_PLOT_POINTS = 500000

## What the key of a calculation in the result of train_ff looks like: the
## word and the data file of the calculation in the molprop file
_CALCULATION = re.compile(r"calculation-([A-Za-z0-9_.+-]{1,100})")
CALCULATION_PREFIX = "calculation-"

## The length units of the atoms in a molprop file, in Angstrom
LENGTH_UNITS = {"Angstrom": 1.0, "A": 1.0, "nm": 10.0, "pm": 0.01, "Bohr": 0.529177210903,
                "bohr": 0.529177210903}

## The element of an atom from its name in a molprop file
_ELEMENT = re.compile(r"[A-Z][a-z]?")

## The largest weight of a property in a training
MAX_WEIGHT = 1e6

## The data sets that the user can put compounds in by hand: for testing,
## or not used at all. The other compounds are for training.
SETS = ("test", "ignore")

## The seed with which the compounds for testing are drawn, such that the
## same file and the same part give the same compounds
SPLIT_SEED = 20261120
## The sign between the names of the compounds in the name of a complex
MONOMER_SEPARATOR = "#"
## The largest seed for drawing the compounds for testing
MAX_SEED = 2147483647
## The largest selection file that is read, in characters
MAX_SELECTION_SIZE = 16 * 1024 * 1024
## What a compound can be used for according to a selection file, as
## alexandria reads it, and what that is in a request
SELECTION_STATUS = {"Train": "train", "Test": "test", "Ignore": "ignore"}
## The most names that are listed of those that are not in the molprop file
MAX_UNKNOWN = 20


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


def monomers(molecule: Any) -> List[str]:
    """!
    @brief The compounds that a dimer or a larger complex consists of.

    The name of a complex has the names of its compounds with a # between
    them, as in methanol#water. These names are used when the name or the
    IUPAC name of the complex has one for each compound that edit_mp found
    in it; otherwise the IUPAC name or the formula of each compound. A
    single compound is its own monomer.
    @param molecule A compound of a molprop file as edit_mp describes it.
    @return The names, each once, in the order of the complex. Empty if the
    compound has no name.
    """
    if not isinstance(molecule, Mapping):
        return []
    known = [name.strip() for name in (molecule.get("name"), molecule.get("iupac"))
             if isinstance(name, str) and name.strip()]
    if not known:
        return []
    fragments = molecule.get("fragments")
    count = len(fragments) if isinstance(fragments, (list, tuple)) else 0
    names: List[str] = []
    for name in known:
        parts = [part.strip() for part in name.split(MONOMER_SEPARATOR)]
        if len(parts) > 1 and all(parts) and count in (0, len(parts)):
            names = parts
            break
    if not names and count < 2:
        names = [known[0]]
    elif not names:
        for fragment in fragments:
            found = None
            if isinstance(fragment, Mapping):
                found = fragment.get("iupac") or fragment.get("formula") or fragment.get("inchi")
            names.append(found.strip() if isinstance(found, str) and found.strip() else "?")
    unique: List[str] = []
    for part in names:
        if part not in unique:
            unique.append(part)
    return unique


def described(data: Any) -> Any:
    """!
    @brief What of the description of a molprop file by edit_mp goes to the
    browser: all of it, and for each compound its monomers, such that the
    page and the server agree on what they are.
    @param data The result of edit_mp -json.
    @return The same with "monomers" in each compound.
    """
    if not isinstance(data, Mapping) or not isinstance(data.get("molecules"), list):
        return data
    molecules = [dict(molecule, monomers=monomers(molecule)) if isinstance(molecule, Mapping)
                 else molecule for molecule in data["molecules"]]
    return dict(data, molecules=molecules)


def without_monomers(molecules: Any, chosen: Any, every: bool = False) -> List[str]:
    """!
    @brief The compounds that are not used when the user chooses compounds
    by their monomers.

    With the complexes A#B, A#C and B#C, choosing A uses A#B and A#C but not
    B#C. With every, a complex is used only when all its monomers are
    chosen: A and B then use A#B, and A#A or B#B if the file has them.
    @param molecules The compounds of a molprop file as edit_mp describes them.
    @param chosen    The names of the monomers that are chosen.
    @param every     Whether all monomers of a complex have to be chosen,
    instead of one of them.
    @return The names in the selection of the compounds that are left out.
    @throws EvaluationError if the monomers are not a list of names, or if
    one of them is in no compound of the file.
    """
    if not isinstance(chosen, (list, tuple)) or len(chosen) > MAX_COMPOUNDS \
            or not all(isinstance(name, str) for name in chosen):
        raise EvaluationError("The monomers that are chosen must be a list of names")
    if not isinstance(molecules, Sequence) or isinstance(molecules, (str, bytes)):
        raise EvaluationError("The molprop file has no compounds")
    wanted = set(chosen)
    known = set()
    left_out: List[str] = []
    for molecule in molecules:
        parts = monomers(molecule)
        known.update(parts)
        used = bool(parts) and (all(part in wanted for part in parts) if every
                                else any(part in wanted for part in parts))
        if not used and isinstance(molecule, Mapping):
            name = molecule.get("iupac") or molecule.get("name")
            if isinstance(name, str) and name not in left_out:
                left_out.append(name)
    unknown = sorted(wanted - known)
    if unknown:
        raise EvaluationError("The molprop file has no monomer %r" % (unknown[0][:60],))
    return left_out


def selection(molecules: Any, test: float = 0.0, sets: Any = None,
              seed: Optional[int] = None) -> str:
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
    @param seed      The seed with which the compounds for testing are
    drawn, None for the one that is always used, such that the same file
    gives the same compounds.
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
                   random.Random(SPLIT_SEED if seed is None else seed)
                   .sample(range(len(used)), number)}
    if len(testing) >= len(used):
        raise EvaluationError("No compound is left to train on")
    return "".join("%s|%s\n" % (name, "Test" if name in testing else "Train")
                   for name in used)


def selection_file(molecules: Any, spec: Any, test: float = 0.0) -> str:
    """!
    @brief A selection file as the user asks for it: the compounds chosen
    one by one, by their monomers, or both, and those for testing by hand
    or as a part that is drawn at random.
    @param molecules The compounds of a molprop file as edit_mp describes them.
    @param spec      The choices, an object that may have "sets", see
    selection(), "monomers" with "names", the monomers that are chosen, and
    "every", whether all monomers of a complex have to be chosen instead of
    one, and "seed", a whole number with which the compounds for testing
    are drawn.
    @param test      The part of the compounds that is kept for testing.
    @return The text of the file.
    @throws EvaluationError if the choices are not understood or leave
    nothing to train on.
    """
    if not isinstance(spec, Mapping):
        raise EvaluationError("The choices for the selection must be an object")
    sets = spec.get("sets")
    by_monomer = spec.get("monomers")
    if by_monomer is not None:
        if not isinstance(by_monomer, Mapping) or not isinstance(by_monomer.get("every", False),
                                                                 bool):
            raise EvaluationError("The monomers are given as their names (names) and whether "
                                  "all of a complex have to be chosen (every)")
        left_out = without_monomers(molecules, by_monomer.get("names"),
                                    by_monomer.get("every", False))
        if sets is not None and not isinstance(sets, Mapping):
            raise EvaluationError("The compounds that are chosen are given as those for "
                                  "testing (test) and those that are not used (ignore)")
        merged = {key: list(value) if isinstance(value, (list, tuple)) else value
                  for key, value in (sets or {}).items()}
        ignored = merged.get("ignore", [])
        tested = merged.get("test", [])
        if isinstance(ignored, list) and isinstance(tested, list):
            for name in left_out:
                if name in tested:
                    raise EvaluationError("The compound %s is for testing, but its monomers "
                                          "are not chosen" % name)
                if name not in ignored:
                    ignored.append(name)
            merged["ignore"] = ignored
        sets = merged
    seed = spec.get("seed")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)
                             or not 0 <= seed <= MAX_SEED):
        raise EvaluationError("The seed is a whole number between 0 and %d" % MAX_SEED)
    return selection(molecules, test, sets, seed)


def read_selection(molecules: Any, text: Any) -> Dict[str, Any]:
    """!
    @brief Read a selection file of the user for the compounds of a molprop
    file, as alexandria reads it: a line has the name of a compound, a bar
    and Train, Test or Ignore; the name is the IUPAC name of the compound
    or its name in the molprop file; of a dimer the two compounds may be in
    the other order than in the molprop file; the first line of a compound
    counts. A compound of the molprop file that is not in the selection is
    not used.
    @param molecules The compounds of the molprop file as edit_mp describes
    them.
    @param text      The content of the selection file.
    @return An object with "sets", the compounds for testing and those that
    are not used as selection() takes them, the numbers "train", "test"
    and "ignore" of compounds of the molprop file, "unknown", the first
    names of the selection that the molprop file does not have, with their
    number "unknown_count", and "invalid", the number of lines that were
    not understood, with the first of them in "first_invalid".
    @throws EvaluationError if the text is too long or is no text, or if
    the selection has none of the compounds of the molprop file.
    """
    if not isinstance(text, str):
        raise EvaluationError("The selection file must be text")
    if len(text) > MAX_SELECTION_SIZE:
        raise EvaluationError("The selection file is larger than %d MB"
                              % (MAX_SELECTION_SIZE // (1024 * 1024)))
    if not isinstance(molecules, Sequence) or isinstance(molecules, (str, bytes)) \
            or not molecules:
        raise EvaluationError("The molprop file has no compounds")
    names: List[str] = []
    # A compound is found by its IUPAC name or its name, and a dimer also
    # with its compounds in the other order. The name in the selection that
    # the server writes comes first where two compounds share a name.
    exact: Dict[str, str] = {}
    other: Dict[str, str] = {}
    for molecule in molecules:
        if not isinstance(molecule, Mapping):
            continue
        known = [name for name in (molecule.get("iupac"), molecule.get("name"))
                 if isinstance(name, str) and name]
        if not known:
            continue
        if known[0] not in names:
            names.append(known[0])
        exact.setdefault(known[0], known[0])
        for name in known:
            other.setdefault(name, known[0])
            parts = name.split(MONOMER_SEPARATOR)
            if len(parts) == 2:
                other.setdefault(parts[1] + MONOMER_SEPARATOR + parts[0], known[0])
    lookup = dict(other, **exact)
    status: Dict[str, str] = {}
    unknown: List[str] = []
    invalid = 0
    first_invalid = ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        fields = [field.strip() for field in line.split("|")]
        if len(fields) != 2 or not fields[0] or fields[1] not in SELECTION_STATUS:
            invalid += 1
            first_invalid = first_invalid or line[:80]
            continue
        found = lookup.get(fields[0])
        if found is None:
            if fields[0] not in unknown:
                unknown.append(fields[0])
        elif found not in status:
            status[found] = SELECTION_STATUS[fields[1]]
    if not status:
        raise EvaluationError(
            "The selection file has none of the compounds of the molprop file"
            + (": %d lines were not understood, the first is %r" % (invalid, first_invalid)
               if invalid else ""))
    sets: Dict[str, List[str]] = {"test": [], "ignore": []}
    for name in names:
        use = status.get(name, "ignore")
        if use != "train":
            sets[use].append(name)
    return {"sets": sets, "train": len(names) - len(sets["test"]) - len(sets["ignore"]),
            "test": len(sets["test"]), "ignore": len(sets["ignore"]),
            "unknown": [name[:MAX_NAME_LENGTH] for name in unknown[:MAX_UNKNOWN]],
            "unknown_count": len(unknown), "invalid": invalid, "first_invalid": first_invalid}


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
    for molecule in _molecules(data):
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


def _molecules(data: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    """!
    @brief The compounds in the result of train_ff.
    @param data The result of train_ff.
    @return The compounds, in the order of the result.
    """
    molecules = data.get("molecules") or {}
    listed = molecules.values() if isinstance(molecules, Mapping) else molecules
    return [molecule for molecule in listed if isinstance(molecule, Mapping)]


def compound(data: Any, index: Any) -> Dict[str, Any]:
    """!
    @brief The energies of one compound in the result of train_ff: for
    each term, such as the interaction energy or the electrostatics, the
    reference values and the values of the force field for the structures
    of the compound.
    @param data  The result of train_ff.
    @param index The place of the compound in the list that digest() made.
    @return The name, the data set and "energies": by the name of the term
    an object with "reference" and "forcefield", two lists of the same
    length. "truncated" tells whether there were more than MAX_POINTS
    structures, of which the first are given.
    @throws EvaluationError if there is no such compound.
    """
    molecules = _molecules(data) if isinstance(data, Mapping) else []
    if isinstance(index, bool) or not isinstance(index, int) \
            or not 0 <= index < len(molecules):
        raise EvaluationError("There is no such compound")
    molecule = molecules[index]
    energies: Dict[str, Dict[str, List[float]]] = {}
    points = molecule.get("interaction_energies")
    truncated = False
    if isinstance(points, Mapping):
        for number, terms in enumerate(points.values()):
            if number >= MAX_POINTS:
                truncated = True
                break
            if not isinstance(terms, Mapping):
                continue
            for term, pair in terms.items():
                if not isinstance(pair, Mapping):
                    continue
                reference, forcefield = pair.get("QM"), pair.get("ACT")
                if any(isinstance(value, bool) or not isinstance(value, (int, float))
                       for value in (reference, forcefield)):
                    continue
                kept = energies.setdefault(str(term), {"reference": [], "forcefield": []})
                kept["reference"].append(reference)
                kept["forcefield"].append(forcefield)
    return {"name": molecule.get("name", ""), "dataset": molecule.get("dataset", ""),
            "energies": energies, "truncated": truncated}


def describe() -> List[Dict[str, Any]]:
    """!
    @brief The properties that can be compared, for the browser.
    @return For each its name, what the page calls it, whether it is a
    property of dimers, the names of its statistics and the files with
    the comparison that can be plotted.
    """
    return [{"name": o.name, "label": o.label, "dimer": o.dimer,
             "statistics": list(o.statistics), "plots": list(o.plots)}
            for o in OBSERVABLES.values()]


def _number(value: Any) -> Optional[float]:
    """!
    @brief A number of the result of train_ff.
    @param value What is there.
    @return The number, None if it is no number.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def points(data: Any, term: Any) -> Dict[str, Any]:
    """!
    @brief The points of a term of the interaction energy of all compounds,
    the reference against the force field, each with the compound, the
    calculation and the data set it belongs to, such that the page can
    tell which structure a point is.
    @param data The result of train_ff.
    @param term The name of the term, one of DIMER_FILES.
    @return "term"; the lists "reference", "forcefield", "dataset",
    "compound" (the place in the list of digest()) and "calculation" of the
    same length; "truncated" if there were more than MAX_PLOT_POINTS.
    @throws EvaluationError if there is no such term.
    """
    if not isinstance(term, str) or term not in DIMER_FILES:
        raise EvaluationError("There is no such energy term")
    made: Dict[str, Any] = {"term": term, "truncated": False, "reference": [],
                            "forcefield": [], "dataset": [], "compound": [], "calculation": []}
    molecules = _molecules(data) if isinstance(data, Mapping) else []
    for index, molecule in enumerate(molecules):
        energies = molecule.get("interaction_energies")
        if not isinstance(energies, Mapping):
            continue
        dataset = molecule.get("dataset", "")
        for key, terms in energies.items():
            pair = terms.get(term) if isinstance(terms, Mapping) else None
            if not isinstance(pair, Mapping):
                continue
            reference, forcefield = _number(pair.get("QM")), _number(pair.get("ACT"))
            if reference is None or forcefield is None:
                continue
            if len(made["reference"]) >= MAX_PLOT_POINTS:
                made["truncated"] = True
                return made
            made["reference"].append(reference)
            made["forcefield"].append(forcefield)
            made["dataset"].append(dataset if isinstance(dataset, str) else "")
            made["compound"].append(index)
            made["calculation"].append(str(key))
    return made


def calculation(data: Any, index: Any, key: Any) -> Dict[str, Any]:
    """!
    @brief The energies of one calculation of a compound in the result of
    train_ff: for each term the reference, the force field and the
    difference of the two.
    @param data  The result of train_ff.
    @param index The place of the compound in the list that digest() made.
    @param key   The key of the calculation, calculation- and its data file.
    @return The name and data set of the compound, the key and the data
    file of the calculation, and "energies": a list with for each term its
    name, "reference", "forcefield" and "difference" (force field minus
    reference), None where a value is missing.
    @throws EvaluationError if there is no such compound or calculation.
    """
    molecules = _molecules(data) if isinstance(data, Mapping) else []
    if isinstance(index, bool) or not isinstance(index, int) \
            or not 0 <= index < len(molecules):
        raise EvaluationError("There is no such compound")
    matched = _CALCULATION.fullmatch(key) if isinstance(key, str) else None
    molecule = molecules[index]
    energies = molecule.get("interaction_energies")
    terms = energies.get(key) if matched and isinstance(energies, Mapping) else None
    if not isinstance(terms, Mapping):
        raise EvaluationError("The compound has no such calculation")
    listed = []
    for term, pair in terms.items():
        if not isinstance(pair, Mapping):
            continue
        reference, forcefield = _number(pair.get("QM")), _number(pair.get("ACT"))
        listed.append({"term": str(term), "reference": reference, "forcefield": forcefield,
                       "difference": None if reference is None or forcefield is None
                       else forcefield - reference})
    return {"name": molecule.get("name", ""), "dataset": molecule.get("dataset", ""),
            "calculation": key, "datafile": matched.group(1), "energies": listed}


def _atoms(experiment: Any, fragments: Any) -> List[Dict[str, Any]]:
    """!
    @brief The atoms of a calculation in a molprop file.
    @param experiment The element of the calculation.
    @param fragments  The element with the compounds of the molecule, None
    if it has none.
    @return For each atom its element, type, coordinates in Angstrom and
    the place of the compound it belongs to.
    """
    part: Dict[int, int] = {}
    if fragments is not None:
        for number, fragment in enumerate(fragments.findall("fragment")):
            for word in (fragment.text or "").split():
                if word.isdigit():
                    part[int(word) - 1] = number
    atoms = []
    for place, atom in enumerate(experiment.findall("atom")):
        name = atom.get("name", "")
        found = _ELEMENT.match(name)
        scale = LENGTH_UNITS.get(atom.get("coord_unit", "Angstrom"), 1.0)
        position = []
        for axis in ("x", "y", "z"):
            try:
                position.append(float((atom.findtext(axis) or "").strip()) * scale)
            except ValueError as exc:
                raise EvaluationError("An atom of the calculation has no position") from exc
        atoms.append({"element": found.group(0) if found else "X",
                      "type": atom.get("obtype", ""), "x": position[0], "y": position[1],
                      "z": position[2], "fragment": part.get(place, 0)})
    return atoms


def _calculation(paths: Sequence["os.PathLike[str]"], name: str,
                 datafile: str) -> Tuple[Any, Any]:
    """!
    @brief A calculation in molprop files, found by the name or the IUPAC
    name of the compound and the data file of the calculation, as train_ff
    names them. The files are read piece by piece, such that a large file
    needs little memory.
    @param paths    The molprop files.
    @param name     The name of the compound.
    @param datafile The data file of the calculation.
    @return The element of the compound and that of the calculation.
    @throws EvaluationError if the files do not have the calculation, or
    cannot be read.
    """
    for path in paths:
        try:
            context = ElementTree.iterparse(os.fspath(path), events=("start", "end"))
            root = None
            for event, element in context:
                if root is None and event == "start":
                    root = element
                if event != "end" or element.tag != "molecule":
                    continue
                info = element.find("molinfo")
                names = {element.get("molname"), info.get("iupac") if info is not None else None}
                if name in names:
                    for experiment in element.findall("experiment"):
                        if experiment.get("datafile") == datafile:
                            return element, experiment
                element.clear()
                if root is not None:
                    root.clear()
        except (OSError, ElementTree.ParseError) as exc:
            raise EvaluationError("The molprop file cannot be read") from exc
    raise EvaluationError("The molprop file that is open has no calculation %s of %s"
                          % (datafile, name))


def structure(paths: Sequence["os.PathLike[str]"], name: str,
              datafile: str) -> List[Dict[str, Any]]:
    """!
    @brief The atoms of a calculation in molprop files, see _calculation().
    @param paths    The molprop files.
    @param name     The name of the compound.
    @param datafile The data file of the calculation.
    @return The atoms, see _atoms().
    @throws EvaluationError if the files do not have the calculation, or
    cannot be read.
    """
    molecule, experiment = _calculation(paths, name, datafile)
    return _atoms(experiment, molecule.find("fragments"))


def structure_file(paths: Sequence["os.PathLike[str]"], name: str, datafile: str) -> str:
    """!
    @brief A calculation in molprop files as an SDF file, from which
    gentop makes the particles of a force field, see molfile.
    @param paths    The molprop files.
    @param name     The name of the compound.
    @param datafile The data file of the calculation.
    @return The file: the atoms of the calculation, the bonds of the
    compound and the formal charges that make up the charge of each of its
    compounds.
    @throws EvaluationError if the files do not have the calculation, if
    they cannot be read or if the structure cannot be written.
    """
    molecule, experiment = _calculation(paths, name, datafile)
    atoms = _atoms(experiment, molecule.find("fragments"))
    bonds = []
    for bond in molecule.findall("bond"):
        try:
            bonds.append((int(bond.get("ai", "")) - 1, int(bond.get("aj", "")) - 1,
                          float(bond.get("bondorder", "1"))))
        except ValueError as exc:
            raise EvaluationError("A bond of the compound cannot be read") from exc
    fragments = []
    listed = molecule.find("fragments")
    for fragment in listed.findall("fragment") if listed is not None else []:
        members = [int(word) - 1 for word in (fragment.text or "").split() if word.isdigit()]
        try:
            charge = int(round(float(fragment.get("charge", "0"))))
        except ValueError as exc:
            raise EvaluationError("The charge of a compound cannot be read") from exc
        fragments.append((members, charge))
    try:
        return molfile.sdf(name, atoms, bonds, fragments)
    except molfile.MolfileError as exc:
        raise EvaluationError(str(exc)) from exc


def user_selection(molecules: Any, text: Any) -> str:
    """!
    @brief A selection file of the user, for train_ff as it is: each line
    the name of a compound, a bar and Train, Test or Ignore. Names that the
    molprop file does not have are kept, train_ff leaves them out.
    @param molecules The compounds of the molprop file as edit_mp describes them.
    @param text      The content of the file.
    @return The lines, without the empty ones and the spaces around the
    fields.
    @throws EvaluationError if a line is no such line, or if no compound of
    the molprop file is for training.
    """
    if not isinstance(text, str):
        raise EvaluationError("The selection file must be text")
    if len(text) > MAX_SELECTION_SIZE:
        raise EvaluationError("The selection file is larger than %d MB"
                              % (MAX_SELECTION_SIZE // (1024 * 1024)))
    lines = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        fields = [field.strip() for field in line.split("|")]
        if len(fields) != 2 or not fields[0] or fields[1] not in SELECTION_STATUS \
                or len(fields[0]) > MAX_NAME_LENGTH:
            raise EvaluationError("Line %d of the selection file is not the name of a compound, "
                                  "a bar and Train, Test or Ignore: %r" % (number, line[:80]))
        lines.append("%s|%s\n" % (fields[0], fields[1]))
    try:
        read = read_selection(molecules, "".join(lines))
    except EvaluationError:
        read = {"train": 0}
    if read["train"] < 1:
        raise EvaluationError("The selection file has no compound of the molprop file for "
                              "training")
    return "".join(lines)
