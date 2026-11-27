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
@brief What a new force field is made from: the choices of the user become
the input files and the options of alexandria gen_ff.

gen_ff reads a table with one line for each particle type, with the bounds,
the mutability and the unit of each of its parameters, and optionally files
with virtual sites. This module makes that table from a few choices: the
potentials, the charge model, the atom types and which of them get a shell
or a second charge. It does what the program actgui of ACT-utilities did,
without the windows.

Everything the user can choose is one of the values that are listed here.
A request that asks for anything else is refused, so that no text of the
user reaches the files or the command line.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .settings import PACKAGE_DIR

## Where the atom types and the predefined virtual sites are
DATA_DIR = PACKAGE_DIR / "data" / "generator"

## The mutabilities that are written
BOUNDED = "Bounded"
FIXED = "Fixed"

## The kinds of particles
ATOM = "atom"
SHELL = "shell"
VSITE = "vsite"

## The names of the interactions in the table
COULOMB = "COULOMB"
VDW = "VANDERWAALS"
VDW_CORRECTION = "VDWCORRECTION"
INDUCTION = "INDUCTIONCORRECTION"
POLARIZATION = "POLARIZATION"
EEM = "ELECTRONEGATIVITYEQUALIZATION"

## The charge model with a point charge and a Gaussian charge on each atom
POINT_GAUSSIAN = "P+G"


class GeneratorError(ValueError):
    """!
    @brief Choices that are not accepted. The message is for the user.
    """


@dataclass(frozen=True)
class Potential:
    """!
    @brief A potential that can be chosen, with the bounds of its parameters.
    """
    ## The name as gen_ff knows it
    name: str
    ## What it is and where it is described
    comment: str
    ## For each parameter the minimum, the maximum and the unit
    bounds: Mapping[str, Tuple[float, float, str]] = field(default_factory=dict)
    ## For each parameter the combination rule that is proposed
    rules: Mapping[str, str] = field(default_factory=dict)


## The combination rules, as gen_ff knows them
COMBINATION_RULES = (
    "Geometric", "Arithmetic", "GeneralizedMean", "Volumetric", "InverseSquare",
    "Kronecker", "HogervorstEpsilon", "HogervorstSigma", "WaldmanSigma",
    "WaldmanEpsilon", "HalgrenEpsilon", "QiSigma", "QiEpsilon", "Yang", "MasonGamma")

## The Van der Waals potentials. The combination rules are those of Kriz et
## al., J. Chem. Theory Comput. 20 (2024) 2362.
VDW_POTENTIALS: Dict[str, Potential] = {p.name: p for p in (
    Potential("SLATER_ISA_TT",
              "Slater-ISA repulsion with Tang-Toennies dispersion. Van Vleet et al., "
              "J. Chem. Theory Comput. 12 (2016) 3851 and Sheng et al., Phys. Rev. Lett. "
              "125 (2020) 2534",
              {"A": (0, 100000, "kJ/mol"), "bDisp": (5, 50, "1/nm"), "bExch": (5, 50, "1/nm"),
               "c6": (0, 0.005, "kJ/mol nm6"), "c8": (0, 0.002, "kJ/mol nm8"),
               "c10": (0, 0.001, "kJ/mol nm10")},
              {"A": "Geometric", "bExch": "Arithmetic", "bDisp": "Arithmetic",
               "c6": "Geometric", "c8": "Geometric", "c10": "Geometric"}),
    Potential("TT2b",
              "Tang-Toennies with separate b. Sheng et al., Phys. Rev. Lett. 125 (2020) 2534",
              {"Att2b": (0, 100000, "kJ/mol"), "bDisptt2b": (5, 50, "1/nm"),
               "bExchtt2b": (5, 50, "1/nm"), "c6tt2b": (0, 0.005, "kJ/mol nm6"),
               "c8tt2b": (0, 0.002, "kJ/mol nm8"), "c10tt2b": (0, 0.001, "kJ/mol nm10")},
              {"Att2b": "Geometric", "bExchtt2b": "Arithmetic", "bDisptt2b": "Arithmetic",
               "c6tt2b": "Geometric", "c8tt2b": "Geometric", "c10tt2b": "Geometric"}),
    Potential("WANG_BUCKINGHAM",
              "Buckingham variant without singularity at r=0. Wang et al., "
              "J. Chem. Theory Comput. 9 (2013) 452",
              {"sigma": (0, 1, "nm"), "epsilon": (0, 5, "kJ/mol"), "gamma": (1, 25, "")},
              {"sigma": "InverseSquare", "epsilon": "HogervorstEpsilon", "gamma": "MasonGamma"}),
    Potential("LJ14_7",
              "Buffered 14-7 potential. Halgren, J. Amer. Chem. Soc. 114 (1992) 7827",
              {"sigma": (0, 1, "nm"), "epsilon": (0, 5, "kJ/mol"), "gamma": (0, 0.8, ""),
               "delta": (0, 2, "")},
              {"sigma": "Volumetric", "epsilon": "Geometric", "gamma": "Arithmetic",
               "delta": "Arithmetic"}),
    Potential("LJ12_6",
              "Lennard-Jones potential. Jones, Proc. Royal Soc. A 106 (1924) 463",
              {"sigma": (0, 1, "nm"), "epsilon": (0, 5, "kJ/mol")},
              {"sigma": "Geometric", "epsilon": "Geometric"}),
    Potential("GENERALIZED_BUCKINGHAM",
              "Generalized Buckingham. Werhahn et al., Chem. Phys. Lett. 619 (2015) 133",
              {"rmin": (0, 1, "nm"), "epsilon": (0, 5, "kJ/mol"), "gamma": (7, 25, ""),
               "delta": (3, 12, "")},
              {"rmin": "WaldmanSigma", "epsilon": "HalgrenEpsilon", "gamma": "HogervorstSigma",
               "delta": "Arithmetic"}),
)}
## The Van der Waals potential that is proposed
DEFAULT_VDW = "WANG_BUCKINGHAM"

## The corrections of the induction energy, NONE for no correction
INDUCTION_POTENTIALS: Dict[str, Potential] = {p.name: p for p in (
    Potential("NONE", "No corrections"),
    Potential("MACDANIEL_SCHMIDT",
              "Negative exponential correction. McDaniel & Schmidt, J. Phys. Chem. 117 "
              "(2013) 2053",
              {"a1dexp": (0, 100000, "kJ/mol"), "a2dexp": (0, 100000, "kJ/mol"),
               "bdexp": (5, 50, "1/nm")},
              {"a1dexp": "Geometric", "a2dexp": "Geometric", "bdexp": "Arithmetic"}),
    Potential("MORSE_BONDS",
              "Correction with repulsive and attractive part. Hosseini & Van der Spoel, "
              "in preparation",
              {"D0": (0, 0, "kJ/mol"), "De": (0, 800, "kJ/mol"), "bondlength": (0, 0.5, "nm"),
               "beta": (5, 50, "1/nm")},
              {"bondlength": "Arithmetic", "De": "Kronecker", "beta": "Arithmetic",
               "D0": "Geometric"}),
)}

_DISTRIBUTED = ("Distributed charges. Ghahremanpour et al., J. Chem. Theory Comput. 14 "
                "(2018) 5553")
## The charge models
CHARGE_MODELS: Dict[str, str] = {
    "POINT": "Point charges",
    "GAUSSIAN": _DISTRIBUTED,
    "SLATER": _DISTRIBUTED,
    POINT_GAUSSIAN: "A point charge and a Gaussian charge on each atom. Van der Spoel & "
                    "Hosseini, Phys. Chem. Chem. Phys. 27 (2025) 13817",
}
## The charge model that is proposed
DEFAULT_CHARGE_MODEL = "GAUSSIAN"

## The functions for bonds, angles and dihedrals, the proposed one first
BOND_FUNCTIONS = ("HUA_BONDS", "HARMONIC_BONDS", "CUBIC_BONDS", "MORSE_BONDS")
ANGLE_FUNCTIONS = ("HARMONIC_ANGLES", "UREY_BRADLEY_ANGLES", "CROSS_BOND_ANGLES")
DIHEDRAL_FUNCTIONS = ("FOURIER_DIHEDRALS", "PROPER_DIHEDRALS")
## The options of gen_ff that take these functions
FUNCTION_OPTIONS = {"bonds": "bondfn", "angles": "anglefn", "dihedrals": "dihfn"}

## The files with predefined virtual sites: name, what the page calls them
## and whether they are sigma-holes, which can get an exchange correction
VSITE_FILES: Dict[str, Tuple[str, bool]] = {
    "vsite2_halogen_sigma_holes": ("σ-holes on halogen atoms", True),
    "vsite2_water_sigma_holes": ("σ-holes on water", True),
    "vsite3_water_bisector": ("Site on water bisector", False),
    "vsite3_alcohol_bisector": ("Site on alcohol COH bisector", False),
    "vsite3_water_lone_pairs": ("Lone pairs on water", False),
    "vsite3_alcohol_lone_pairs": ("Lone pairs on alcohol oxygen", False),
    "vsite4_amine": ("Lone pair on amine nitrogen", False),
}

## The kinds of virtual sites that the user can define: for each the number
## of atoms it is made from and the names of its parameters
SITE_KINDS: Dict[str, Tuple[int, Tuple[str, ...]]] = {
    "VSITE2": (2, ("vs2a",)),
    "VSITE2FD": (2, ("vs2fd_a",)),
    "VSITE3S": (3, ("vs3sa",)),
    "VSITE3": (3, ("vs3a", "vs3b")),
    "VSITE3FD": (3, ("vs3fd_a", "vs3fd_b")),
    "VSITE3OUTS": (3, ("vs3outs_a", "vs3outs_c")),
    "VSITE3OUT": (3, ("vs3out_a", "vs3out_b", "vs3out_c")),
    "VSITE4S3": (4, ("vs4s3a",)),
    "VSITE4S": (4, ("vs4sa", "vs4sb")),
    "VSITE4": (4, ("vs4a", "vs4b", "vs4c")),
}
## What the name of the particle type of a virtual site looks like. It
## starts with a v, as the predefined ones, so that it is no atom type.
_SITE_TYPE = re.compile(r"^v[A-Za-z0-9]{1,14}$")
## The largest number of virtual sites of the user, and the largest
## absolute value of a bound of their parameters
MAX_SITES = 50
## The largest number of parameters of the user that are fixed
MAX_FIXED = 2000
## The largest value of a fixed parameter
MAX_FIXED_VALUE = 1e12
## What the names in a fixed parameter may look like
_FIXED_NAMES = {"interaction": re.compile(r"^[A-Z_]{0,40}$"),
                "type": re.compile(r"^[A-Za-z0-9+_-]{1,20}$"),
                "parameter": re.compile(r"^[A-Za-z0-9_]{1,30}$"),
                "unit": re.compile(r"^[A-Za-z0-9/^ ]{0,30}$")}
MAX_SITE_PARAMETER = 1000.0
## The smallest and largest charge of a virtual site that are proposed, in e.
## A site starts in the middle, without a charge.
VSITE_CHARGE_RANGE = (-4.0, 4.0)
## The smallest and largest charge of a virtual site that can be asked for
VSITE_CHARGE_LIMITS = (-10.0, 10.0)
## The most virtual sites that can be named to get Van der Waals parameters
MAX_VDW_SITES = 500
## What the name of the particle type of a virtual site in such a list looks
## like: that of a site, or v1 and the atom type of a second charge
_VDW_SITE = re.compile(r"^v[A-Za-z0-9+_-]{1,19}$")

## The combination rules of the exchange correction on virtual sites
_VDW_CORRECTION_RULES = (("aexp", "Kronecker"), ("bexp", "Arithmetic"), ("vsite", "Geometric"))

## The smallest and largest number of excluded bonds, relative dielectric
## constant and row of the periodic table for Slater charges
NEXCL_RANGE = (0, 3)
EPSILONR_RANGE = (0.1, 100.0)
ROW_RANGE = (1, 6)
## The force constants of shells that can be given, in kJ/mol nm2
KSHELL_RANGE = (1.0, 1e9)
## Which parameter of the shells follows from a fixed force constant, see
## the option -fixkshell of gen_ff: none, alpha or the charge of the shell
SHELL_FOLLOWS = {
    "no": "The force constant follows from the polarizability and the charge of the shell",
    "alpha": "The force constant is fixed, the polarizability follows from it and the charge",
    "charge": "The force constant is fixed, the charge of the shell follows from it and the polarizability",
}


@dataclass(frozen=True)
class AtomType:
    """!
    @brief An atom type that can be put in a force field.
    """
    ## The name, as in GAFF
    name: str
    ## The element
    element: str
    ## The atomic number
    atomnr: int
    ## Whether it is selected when the user has not chosen
    default: bool
    ## Whether it makes bonds: zero for ions and noble gases
    bonds: int
    ## The covalent radius in nm
    radius: float
    ## The row of the periodic table, for Slater charges
    row: int
    ## The formal charge
    charge: int
    ## Whether it gets a second charge when the user has not chosen
    vsite: bool
    ## Whether it gets a shell when the user has not chosen
    shell: bool
    ## The smallest charge
    qmin: float
    ## The largest charge
    qmax: float
    ## What it is
    comment: str


def read_atom_types(path: Optional[Path] = None) -> Dict[str, AtomType]:
    """!
    @brief Read the atom types that can be chosen.
    @param path The file, the one of the package if None.
    @return The atom types by name, in the order of the file.
    @throws GeneratorError if a line of the file cannot be read.
    """
    path = Path(path) if path is not None else DATA_DIR / "atomtypes_gui.csv"
    types: Dict[str, AtomType] = {}
    with open(path, "r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            if line.startswith("#") or not line.strip():
                continue
            words = line.rstrip("\n").split("|")
            if len(words) != 13:
                continue
            try:
                types[words[0]] = AtomType(
                    name=words[0], element=words[1], atomnr=int(words[2]),
                    default=int(words[3]) > 0, bonds=int(words[4]), radius=float(words[5]),
                    row=int(words[6]), charge=int(words[7]), vsite=int(words[8]) > 0,
                    shell=int(words[9]) > 0, qmin=float(words[10]), qmax=float(words[11]),
                    comment=words[12])
            except ValueError as exc:
                raise GeneratorError("Error in the file %s on line %d"
                                     % (path.name, lineno)) from exc
    return types


def read_vsites(name: str) -> List[Dict[str, Any]]:
    """!
    @brief Read a file with predefined virtual sites.
    @param name The name of the file, one of VSITE_FILES.
    @return The virtual sites, in the form that gen_ff -vs reads.
    @throws GeneratorError for an unknown name.
    """
    if name not in VSITE_FILES:
        raise GeneratorError("There are no virtual sites '%s'" % (name,))
    with open(DATA_DIR / (name + ".json"), "r", encoding="utf-8") as handle:
        return list(json.load(handle).get("virtual_sites", []))


def describe_vsites(name: str) -> str:
    """!
    @brief What a file with predefined virtual sites says about them.
    @param name The name of the file, one of VSITE_FILES.
    @return The description in the file, with its reference if it has one;
    empty if the file has none.
    @throws GeneratorError for an unknown name.
    """
    if name not in VSITE_FILES:
        raise GeneratorError("There are no virtual sites '%s'" % (name,))
    with open(DATA_DIR / (name + ".json"), "r", encoding="utf-8") as handle:
        description = json.load(handle).get("description", "")
    return description if isinstance(description, str) else ""


@dataclass(frozen=True)
class TypeChoice:
    """!
    @brief An atom type that the user selected.
    """
    ## The name of the atom type
    name: str
    ## Whether it gets a distributed charge on top of the atom
    vsite: bool = False
    ## Whether it is polarizable
    shell: bool = False


@dataclass(frozen=True)
class Choices:
    """!
    @brief Everything the user chose, checked.
    """
    ## The atom types
    types: Tuple[TypeChoice, ...]
    ## The Van der Waals potential
    vdw: str = DEFAULT_VDW
    ## Whether hydrogen atoms have Van der Waals parameters that are trained
    vdw_hydrogen: bool = True
    ## The same for the other atoms
    vdw_heavy: bool = True
    ## The same for all virtual sites
    vdw_vsite: bool = False
    ## The particle types of the virtual sites that have such parameters when
    ## not all have them: those of the sites, and v1 plus the atom type for
    ## the second charge of an atom type
    vdw_sites: Tuple[str, ...] = ()
    ## The same for shells
    vdw_shell: bool = False
    ## The number of bonds within which there are no nonbonded interactions
    nexcl: int = 2
    ## The charge model
    charge_model: str = DEFAULT_CHARGE_MODEL
    ## The relative dielectric constant
    epsilonr: float = 1.0
    ## The highest row of the periodic table for Slater charges
    max_row: int = 1
    ## Whether charges of equivalent atoms are made the same
    symmetric: bool = True
    ## The function for bonds
    bonds: str = BOND_FUNCTIONS[0]
    ## The function for angles
    angles: str = ANGLE_FUNCTIONS[0]
    ## The function for dihedrals
    dihedrals: str = DIHEDRAL_FUNCTIONS[0]
    ## The correction of the induction energy
    induction: str = "NONE"
    ## Whether atoms have parameters of that correction that are trained
    induction_atom: bool = True
    ## The same for shells
    induction_shell: bool = False
    ## The files with predefined virtual sites
    vsite_files: Tuple[str, ...] = ()
    ## Whether sigma-holes get an exchange correction
    exchange: bool = False
    ## The smallest and largest charge of a virtual site, in e
    vsite_charge: Tuple[float, float] = VSITE_CHARGE_RANGE
    ## The combination rules, by interaction and parameter
    rules: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    ## The virtual sites that the user defined, in the form of gen_ff -vs
    sites: Tuple[Mapping[str, Any], ...] = ()
    ## The parameters that the user fixed at a value: interaction, type,
    ## parameter, value and unit, see _fixed
    fixed: Tuple[Mapping[str, Any], ...] = ()
    ## Which parameter of the shells follows from a fixed force constant,
    ## one of SHELL_FOLLOWS
    shell_follows: str = "no"
    ## That force constant in kJ/mol nm2, None for that of the charge and
    ## the middle of the range of the polarizability
    kshell: Optional[float] = None


def default_rules(vdw: str, induction: str) -> Dict[str, Dict[str, str]]:
    """!
    @brief The combination rules that are proposed for two potentials.
    @param vdw       The Van der Waals potential.
    @param induction The correction of the induction energy.
    @return The rules by interaction and parameter.
    """
    rules = {VDW: dict(VDW_POTENTIALS[vdw].rules)}
    if induction != "NONE":
        rules[INDUCTION] = dict(INDUCTION_POTENTIALS[induction].rules)
    return rules


def _object(value: Any, what: str) -> Mapping[str, Any]:
    """!
    @brief Check that a part of the request is an object.
    @param value The part, None for an empty one.
    @param what  What it is, for the message.
    @return The object.
    @throws GeneratorError if it is something else.
    """
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise GeneratorError("%s must be an object" % what)
    return value


def _one_of(value: Any, allowed: Any, default: str, what: str) -> str:
    """!
    @brief Check a choice from a list.
    @param value   What the request gave, None for the default.
    @param allowed The values that can be chosen.
    @param default The value if none is given.
    @param what    What is chosen, for the message.
    @return The choice.
    @throws GeneratorError if it is not in the list.
    """
    if value is None:
        return default
    if not isinstance(value, str) or value not in allowed:
        raise GeneratorError("Unknown %s '%s'" % (what, value))
    return value


def _flag(value: Any, default: bool, what: str) -> bool:
    """!
    @brief Check a choice that is on or off.
    @param value   What the request gave, None for the default.
    @param default The value if none is given.
    @param what    What is chosen, for the message.
    @return The choice.
    @throws GeneratorError if it is not true or false.
    """
    if value is None:
        return default
    if not isinstance(value, bool):
        raise GeneratorError("%s must be true or false" % what)
    return value


def _number(value: Any, default: float, limits: Tuple[float, float], what: str,
            whole: bool = False) -> float:
    """!
    @brief Check a number.
    @param value   What the request gave, None for the default.
    @param default The value if none is given.
    @param limits  The smallest and the largest value.
    @param what    What it is, for the message.
    @param whole   Whether it must be a whole number.
    @return The number.
    @throws GeneratorError if it is no number or outside the limits.
    """
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        raise GeneratorError("%s must be a number" % what)
    if not limits[0] <= value <= limits[1]:
        raise GeneratorError("%s must be between %g and %g" % (what, limits[0], limits[1]))
    if whole and value != int(value):
        raise GeneratorError("%s must be a whole number" % what)
    return value


def _sites(given: Any, chosen: List["TypeChoice"],
           atom_types: Mapping[str, AtomType]) -> Tuple[Mapping[str, Any], ...]:
    """!
    @brief Check the virtual sites that the user defined.
    @param given      What the request gave: a list of objects with type,
    interaction, atoms and parameters, as gen_ff -vs reads them.
    @param chosen     The atom types that are selected.
    @param atom_types The atom types that can be chosen.
    @return The sites, with nothing in them but what was checked.
    @throws GeneratorError if a site is not complete or not possible.
    """
    if given is None:
        return ()
    if not isinstance(given, list):
        raise GeneratorError("The virtual sites must be a list")
    if len(given) > MAX_SITES:
        raise GeneratorError("At most %d virtual sites can be defined" % MAX_SITES)
    # A site is made from atoms that make bonds and are in the force field
    bondtypes = {choice.name + "_b" for choice in chosen if atom_types[choice.name].bonds > 0}
    particles = set(atom_types) | {choice.name + "_s" for choice in chosen} \
        | {"v1" + choice.name for choice in chosen}
    sites = []
    for entry in given:
        entry = _object(entry, "A virtual site")
        for key in entry:
            if key not in ("type", "interaction", "atoms", "parameters"):
                raise GeneratorError("A virtual site has no '%s'" % (key,))
        name = entry.get("type")
        if not isinstance(name, str) or not _SITE_TYPE.fullmatch(name) or name in particles:
            raise GeneratorError("'%s' cannot be the name of a virtual site: use a v and up "
                                 "to 14 letters and digits, and no name of an atom type"
                                 % (name,))
        kind = _one_of(entry.get("interaction"), SITE_KINDS, "", "kind of virtual site")
        if not kind:
            raise GeneratorError("The virtual site %s has no kind" % name)
        natom, names = SITE_KINDS[kind]
        atoms = entry.get("atoms")
        if not isinstance(atoms, list) or len(atoms) != natom:
            raise GeneratorError("A virtual site of kind %s is made from %d atoms"
                                 % (kind, natom))
        for atom in atoms:
            if not isinstance(atom, str) or atom not in bondtypes:
                raise GeneratorError("The virtual site %s is on '%s', which is not the bond "
                                     "type of an atom type that is selected" % (name, atom))
        parameters = _object(entry.get("parameters"), "The parameters of %s" % name)
        if set(parameters) != set(names):
            raise GeneratorError("A virtual site of kind %s has the parameters %s"
                                 % (kind, ", ".join(names)))
        bounds = {}
        for param in names:
            limits = _object(parameters[param], "The bounds of %s" % param)
            if set(limits) != {"min", "max"}:
                raise GeneratorError("The parameter %s needs a min and a max" % param)
            low, high = limits["min"], limits["max"]
            for value in (low, high):
                if isinstance(value, bool) or not isinstance(value, (int, float)) \
                        or not math.isfinite(value) or abs(value) > MAX_SITE_PARAMETER:
                    raise GeneratorError("The bounds of %s must be numbers between %g and %g"
                                         % (param, -MAX_SITE_PARAMETER, MAX_SITE_PARAMETER))
            if low > high:
                raise GeneratorError("The minimum of %s is larger than its maximum" % param)
            bounds[param] = {"min": low, "max": high}
        sites.append({"type": name, "interaction": kind, "atoms": list(atoms),
                      "parameters": bounds})
    return tuple(sites)


def _vdw_sites(given: Any) -> Tuple[str, ...]:
    """!
    @brief Check the virtual sites that get Van der Waals parameters.
    @param given What the request gave: a list of names of particle types.
    @return The names. Whether the force field has these virtual sites is
    checked by build.
    @throws GeneratorError if it is no list of names of virtual sites.
    """
    if given is None:
        return ()
    if not isinstance(given, list):
        raise GeneratorError("The virtual sites with Van der Waals parameters must be a list")
    if len(given) > MAX_VDW_SITES:
        raise GeneratorError("At most %d virtual sites can be named" % MAX_VDW_SITES)
    for name in given:
        if not isinstance(name, str) or not _VDW_SITE.fullmatch(name):
            raise GeneratorError("'%s' is not the name of a virtual site" % (name,))
    if len(set(given)) != len(given):
        raise GeneratorError("A virtual site with Van der Waals parameters is given twice")
    return tuple(given)


def _vsite_charge(given: Any) -> Tuple[float, float]:
    """!
    @brief Check the bounds of the charge of the virtual sites.
    @param given What the request gave: an object with min and max, None
    for the bounds that are proposed.
    @return The smallest and the largest charge.
    @throws GeneratorError if they are no numbers within the limits, or the
    smallest is larger than the largest.
    """
    if given is None:
        return VSITE_CHARGE_RANGE
    given = _object(given, "The charge of the virtual sites")
    for key in given:
        if key not in ("min", "max"):
            raise GeneratorError("The charge of the virtual sites has no '%s'" % (key,))
    low = float(_number(given.get("min"), VSITE_CHARGE_RANGE[0], VSITE_CHARGE_LIMITS,
                        "The smallest charge of a virtual site"))
    high = float(_number(given.get("max"), VSITE_CHARGE_RANGE[1], VSITE_CHARGE_LIMITS,
                         "The largest charge of a virtual site"))
    if low > high:
        raise GeneratorError("The smallest charge of a virtual site is larger than the largest")
    return (low, high)


def _fixed(given: Any) -> Tuple[Mapping[str, Any], ...]:
    """!
    @brief Check the parameters that the user fixed, as the file of actgui
    gave them.
    @param given What the request gave: a list of objects with interaction
    (empty for a parameter of the particle type itself, such as q), type,
    parameter, value and unit (empty for the unit of the table).
    @return The parameters, with nothing in them but what was checked.
    Whether the types have these parameters is checked by build.
    @throws GeneratorError if one is not complete, not a number or given
    twice.
    """
    if given is None:
        return ()
    if not isinstance(given, list):
        raise GeneratorError("The fixed parameters must be a list")
    if len(given) > MAX_FIXED:
        raise GeneratorError("At most %d parameters can be fixed" % MAX_FIXED)
    fixed = []
    seen = set()
    for entry in given:
        entry = _object(entry, "A fixed parameter")
        for key in entry:
            if key not in ("interaction", "type", "parameter", "value", "unit"):
                raise GeneratorError("A fixed parameter has no '%s'" % (key,))
        names = {}
        for key, pattern in _FIXED_NAMES.items():
            value = entry.get(key, "")
            if not isinstance(value, str) or not pattern.fullmatch(value):
                raise GeneratorError("The %s of a fixed parameter cannot be '%s'" % (key, value))
            names[key] = value
        value = entry.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(value) or abs(value) > MAX_FIXED_VALUE:
            raise GeneratorError("The value of %s of %s must be a number"
                                 % (names["parameter"], names["type"]))
        key = (names["interaction"], names["type"], names["parameter"])
        if key in seen:
            raise GeneratorError("The parameter %s %s of %s is fixed twice"
                                 % (names["interaction"], names["parameter"], names["type"]))
        seen.add(key)
        fixed.append(dict(names, value=value))
    return tuple(fixed)


def parse_choices(request: Any, atom_types: Mapping[str, AtomType]) -> Choices:
    """!
    @brief Check what the browser sent and fill in what it left out.
    @param request    The object "generator" of a request: atomtypes (a
    list of objects with name, vsite and shell), vdw (potential, particles,
    sites, the virtual sites with parameters that are trained, and nexcl),
    charge (model, epsilonr, maxrow, symmetric), bonds, angles, dihedrals,
    induction (potential, particles), vsites (files, exchange, charge with
    the min and max of the charge of a site, and sites, the virtual sites of
    the user), rules (by interaction and parameter), fixed (the parameters
    that the user fixed, see _fixed) and shells (follows, one of
    SHELL_FOLLOWS, and kshell, the fixed force constant, or none).
    @param atom_types The atom types that can be chosen.
    @return The choices.
    @throws GeneratorError if something is not one of the possibilities.
    """
    request = _object(request, "The choices for the force field")
    known = ("atomtypes", "vdw", "charge", "bonds", "angles", "dihedrals", "induction",
             "vsites", "rules", "fixed", "shells")
    for key in request:
        if key not in known:
            raise GeneratorError("Unknown choice '%s'" % (key,))

    # The atom types
    given = request.get("atomtypes")
    if not isinstance(given, list) or not given:
        raise GeneratorError("Select at least one atom type")
    types: List[TypeChoice] = []
    seen = set()
    for entry in given:
        entry = _object(entry, "An atom type")
        name = entry.get("name")
        if not isinstance(name, str) or name not in atom_types:
            raise GeneratorError("Unknown atom type '%s'" % (name,))
        if name in seen:
            raise GeneratorError("The atom type %s is given twice" % name)
        seen.add(name)
        types.append(TypeChoice(name=name,
                                vsite=_flag(entry.get("vsite"), False, "vsite"),
                                shell=_flag(entry.get("shell"), False, "shell")))

    vdw = _object(request.get("vdw"), "vdw")
    vdw_on = _object(vdw.get("particles"), "The particles with Van der Waals interactions")
    charge = _object(request.get("charge"), "charge")
    induction = _object(request.get("induction"), "induction")
    induction_on = _object(induction.get("particles"),
                           "The particles with the induction correction")
    vsites = _object(request.get("vsites"), "vsites")
    files = vsites.get("files", [])
    if not isinstance(files, list):
        raise GeneratorError("The virtual sites must be a list")
    for name in files:
        if not isinstance(name, str) or name not in VSITE_FILES:
            raise GeneratorError("There are no virtual sites '%s'" % (name,))
    if len(set(files)) != len(files):
        raise GeneratorError("Virtual sites are given twice")

    vdw_name = _one_of(vdw.get("potential"), VDW_POTENTIALS, DEFAULT_VDW,
                       "Van der Waals potential")
    induction_name = _one_of(induction.get("potential"), INDUCTION_POTENTIALS, "NONE",
                             "induction correction")
    # The combination rules: those that are proposed, unless another is given
    rules = default_rules(vdw_name, induction_name)
    for itype, params in _object(request.get("rules"), "The combination rules").items():
        if itype not in rules:
            raise GeneratorError("There are no combination rules for '%s'" % (itype,))
        for param, rule in _object(params, "The combination rules of %s" % itype).items():
            if param not in rules[itype]:
                raise GeneratorError("%s has no parameter '%s'" % (itype, param))
            rules[itype][param] = _one_of(rule, COMBINATION_RULES, "", "combination rule")

    # The force constants of the shells
    shells = _object(request.get("shells"), "shells")
    for key in shells:
        if key not in ("follows", "kshell"):
            raise GeneratorError("Unknown choice for the shells '%s'" % (key,))
    shell_follows = _one_of(shells.get("follows"), SHELL_FOLLOWS, "no",
                            "parameter of the shells that follows")
    kshell = shells.get("kshell")
    if kshell is not None:
        kshell = float(_number(kshell, 0, KSHELL_RANGE, "The force constant of the shells"))
        if shell_follows == "no":
            raise GeneratorError("A force constant of the shells is only used if it is fixed")
    if shell_follows != "no" and not any(t.shell for t in types):
        raise GeneratorError("The force constant of the shells can only be fixed if an atom type "
                             "has a shell")

    return Choices(
        types=tuple(types),
        vdw=vdw_name,
        vdw_hydrogen=_flag(vdw_on.get("hydrogen"), True, "hydrogen"),
        vdw_heavy=_flag(vdw_on.get("heavy"), True, "heavy"),
        vdw_vsite=_flag(vdw_on.get("vsite"), False, "vsite"),
        vdw_sites=_vdw_sites(vdw.get("sites")),
        vdw_shell=_flag(vdw_on.get("shell"), False, "shell"),
        nexcl=int(_number(vdw.get("nexcl"), 2, NEXCL_RANGE, "The number of excluded bonds",
                          whole=True)),
        charge_model=_one_of(charge.get("model"), CHARGE_MODELS, DEFAULT_CHARGE_MODEL,
                             "charge model"),
        epsilonr=float(_number(charge.get("epsilonr"), 1.0, EPSILONR_RANGE,
                               "The relative dielectric constant")),
        max_row=int(_number(charge.get("maxrow"), 1, ROW_RANGE, "The highest Slater row",
                            whole=True)),
        symmetric=_flag(charge.get("symmetric"), True, "symmetric"),
        bonds=_one_of(request.get("bonds"), BOND_FUNCTIONS, BOND_FUNCTIONS[0],
                      "function for bonds"),
        angles=_one_of(request.get("angles"), ANGLE_FUNCTIONS, ANGLE_FUNCTIONS[0],
                       "function for angles"),
        dihedrals=_one_of(request.get("dihedrals"), DIHEDRAL_FUNCTIONS, DIHEDRAL_FUNCTIONS[0],
                          "function for dihedrals"),
        induction=induction_name,
        induction_atom=_flag(induction_on.get("atom"), True, "atom"),
        induction_shell=_flag(induction_on.get("shell"), False, "shell"),
        vsite_files=tuple(files),
        exchange=_flag(vsites.get("exchange"), False, "exchange"),
        vsite_charge=_vsite_charge(vsites.get("charge")),
        rules=rules,
        sites=_sites(vsites.get("sites"), types, atom_types),
        fixed=_fixed(request.get("fixed")),
        shell_follows=shell_follows,
        kshell=kshell)


class _Table:
    """!
    @brief The table of particle types that gen_ff reads, while it is made.
    """

    def __init__(self) -> None:
        """!
        @brief Make an empty table.
        """
        ## For each particle type its columns, in the order they were set
        self.rows: Dict[str, Dict[str, Any]] = {}

    def add(self, name: str, columns: Dict[str, Any]) -> None:
        """!
        @brief Add a particle type.
        @param name    The name of the particle type.
        @param columns Its first columns.
        """
        self.rows[name] = dict(columns)

    def parameter(self, name: str, interaction: str, parameter: str, minimum: float,
                  maximum: float, mutability: str, unit: str, nonnegative: bool = True) -> None:
        """!
        @brief Set the columns of a parameter of a particle type.
        @param name        The name of the particle type.
        @param interaction The interaction, empty for a parameter of the
        particle type itself.
        @param parameter   The name of the parameter.
        @param minimum     The smallest value.
        @param maximum     The largest value.
        @param mutability  Whether training may change it.
        @param unit        The unit.
        @param nonnegative Whether it may not be negative.
        """
        prefix = "%s:%s:" % (interaction, parameter)
        row = self.rows[name]
        row[prefix + "min"] = minimum
        row[prefix + "max"] = maximum
        row[prefix + "mut"] = mutability
        row[prefix + "unit"] = unit
        row[prefix + "nonneg"] = str(nonnegative)

    def fix(self, name: str, interaction: str, parameter: str, value: float, unit: str) -> None:
        """!
        @brief Fix a parameter of a particle type at a value, as actgui did
        with the parameters of the user.
        @param name        The name of the particle type.
        @param interaction The interaction, empty for a parameter of the
        particle type itself.
        @param parameter   The name of the parameter.
        @param value       The value.
        @param unit        Its unit, which must be that of the table; empty
        for that unit.
        @throws GeneratorError if the type has no such parameter, or it has
        another unit.
        """
        prefix = "%s:%s:" % (interaction, parameter)
        row = self.rows[name]
        if prefix + "min" not in row:
            raise GeneratorError("The particle type %s has no parameter %s to fix"
                                 % (name, (interaction + " " + parameter).strip()))
        if unit and unit != row[prefix + "unit"]:
            raise GeneratorError("The unit of %s of %s is %s, not %s"
                                 % (parameter, name, row[prefix + "unit"], unit))
        self.parameter(name, interaction, parameter, value, value, FIXED, row[prefix + "unit"],
                       value >= 0)

    def text(self) -> str:
        """!
        @brief The table as gen_ff reads it.
        @return Lines with fields that are separated by bars. The first
        line names the columns.
        """
        columns: List[str] = []
        for row in self.rows.values():
            for key in row:
                if key not in columns:
                    columns.append(key)
        lines = ["|".join(["#atomtype"] + columns)]
        for name, row in self.rows.items():
            lines.append("|".join([name] + [_cell(row.get(key, "")) for key in columns]))
        return "\n".join(lines) + "\n"


def _cell(value: Any) -> str:
    """!
    @brief A value as it is written in the table.
    @param value A number or a text.
    @return The text, whole numbers without a decimal point.
    """
    if isinstance(value, float) and value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return str(value)


@dataclass(frozen=True)
class GeneratorInput:
    """!
    @brief What gen_ff gets for a set of choices.
    """
    ## The table with the particle types, the file of the option -f
    atomtypes: str
    ## The virtual sites in JSON format, the file of the option -vs, None
    ## if there are none
    vsites: Optional[str]
    ## The other options of gen_ff
    options: Dict[str, Any]


def _vdw(table: _Table, choices: Choices, name: str, element: str, kind: str) -> None:
    """!
    @brief Set the Van der Waals parameters of a particle type.
    @param table   The table.
    @param choices The choices of the user.
    @param name    The name of the particle type.
    @param element The element of the atom it belongs to.
    @param kind    atom, shell or vsite.
    """
    if kind == ATOM:
        trained = choices.vdw_hydrogen if element == "H" else choices.vdw_heavy
    elif kind == SHELL:
        trained = choices.vdw_shell
    else:
        trained = choices.vdw_vsite or name in choices.vdw_sites
    for param, (low, high, unit) in VDW_POTENTIALS[choices.vdw].bounds.items():
        table.parameter(name, VDW, param, low, high, BOUNDED if trained else FIXED, unit)


def _vdw_correction(table: _Table, name: str, needed: bool, on: bool) -> None:
    """!
    @brief Set the parameters of the exchange correction of a particle type.
    @param table  The table.
    @param name   The name of the particle type.
    @param needed Whether the force field has this correction at all.
    @param on     Whether this particle type takes part in it.
    """
    if not needed:
        return
    if on:
        table.parameter(name, VDW_CORRECTION, "aexp", 0, 50000, BOUNDED, "kJ/mol")
        table.parameter(name, VDW_CORRECTION, "bexp", 10, 50, BOUNDED, "1/nm")
    else:
        table.parameter(name, VDW_CORRECTION, "aexp", 0, 0, FIXED, "kJ/mol")
        table.parameter(name, VDW_CORRECTION, "bexp", 10, 50, FIXED, "1/nm")
    # Whether it is a virtual site with the correction
    flag = 1 if on else 0
    table.parameter(name, VDW_CORRECTION, "vsite", flag, flag, FIXED, "")


def build(choices: Choices, atom_types: Mapping[str, AtomType],
          extra_vsites: Optional[List[Dict[str, Any]]] = None) -> GeneratorInput:
    """!
    @brief Make the input of gen_ff.
    @param choices      The choices of the user.
    @param atom_types   The atom types that can be chosen.
    @param extra_vsites More virtual sites in the form that gen_ff -vs
    reads, next to the predefined ones and those of the choices.
    @return The table, the virtual sites and the options.
    @throws GeneratorError if a virtual site is to get Van der Waals
    parameters that the force field does not have.
    """
    table = _Table()
    point = choices.charge_model == "POINT"

    # The virtual sites, and the particle types they need
    sites: List[Dict[str, Any]] = []
    site_types: Dict[str, bool] = {}
    for name in choices.vsite_files:
        for site in read_vsites(name):
            sites.append(site)
            site_types[site["type"]] = choices.exchange and VSITE_FILES[name][1]
    for site in list(choices.sites) + list(extra_vsites or []):
        sites.append(dict(site))
        site_types.setdefault(site["type"], False)
    correction = any(site_types.values())
    for name, exchange in site_types.items():
        table.add(name, {"element": "VS", "mass": 0, "bondtype": name, "poltype": "",
                         "acmtype": "", "vdwtype": name, "atomicnumber": 0,
                         "zetatype": name + "_z", "radius": 0, "row": 1})
        table.parameter(name, "", "q", choices.vsite_charge[0], choices.vsite_charge[1],
                        BOUNDED, "e", False)
        if point:
            table.parameter(name, COULOMB, "zeta", 0, 0, FIXED, "1/nm")
        else:
            table.parameter(name, COULOMB, "zeta", 4, 20, BOUNDED, "1/nm")
        _vdw(table, choices, name, "VS", VSITE)
        _vdw_correction(table, name, correction, exchange)

    # The atom types, each with its shell and its virtual site if it has them
    second_charges = set()
    for choice in choices.types:
        atom = atom_types[choice.name]
        if choice.vsite:
            second_charges.add("v1" + atom.name)
        # A distributed charge on top of an atom is stored as a second charge
        # of the atom type. Only if it has Van der Waals interactions it must
        # be a particle of its own.
        second_charge = not (choices.vdw_vsite or "v1" + atom.name in choices.vdw_sites)
        for kind in (ATOM, SHELL, VSITE):
            if kind == VSITE and (not choice.vsite or second_charge):
                continue
            if kind == SHELL and not choice.shell:
                continue
            name = atom.name
            element = atom.element
            # An atom that makes bonds has a bond type
            bondtype = atom.name + "_b" if kind == ATOM and atom.bonds > 0 else atom.name
            row = min(atom.row, choices.max_row)
            if kind == SHELL:
                name = atom.name + "_s"
                element = "X"
            elif kind == VSITE:
                name = "v1" + atom.name
                element = "VS"
                bondtype = name
                sites.append({"type": name, "interaction": "VSITE1", "atoms": [atom.name]})
            elif choice.vsite:
                # A point charge on the atom next to the distributed one
                row = 0
            zetatype = name + "_z"
            table.add(name, {
                "element": element, "atomicnumber": atom.atomnr, "bondtype": bondtype,
                "zetatype": zetatype, "acmtype": zetatype if kind == ATOM else "",
                "poltype": atom.name + "_s" if kind == ATOM and choice.shell else "",
                "radius": atom.radius if kind == ATOM else "", "row": row})
            # The charge. That of an atom follows from the charge model, and
            # the sum of atom, shell and virtual site is kept within bounds
            # in the training, see issue 1079 of ACT.
            if kind == ATOM:
                table.parameter(name, "", "q", atom.qmin, atom.qmax, "ACM", "e", False)
            elif kind == VSITE:
                table.parameter(name, "", "q", -atom.atomnr, 0, BOUNDED, "e", False)
            else:
                # A shell with a small charge moves too far from its core
                table.parameter(name, "", "q", -atom.atomnr, -1, BOUNDED, "e", False)
            # An atom with a shell or a second charge is a point charge
            if point or (kind == ATOM and (choice.vsite or choice.shell)):
                table.parameter(name, COULOMB, "zeta", 0, 0, FIXED, "1/nm")
            else:
                table.parameter(name, COULOMB, "zeta", 4, 20, BOUNDED, "1/nm")
            if kind == ATOM and choice.vsite and second_charge:
                table.parameter(name, "", "chargeVS", -atom.atomnr, 0, BOUNDED, "e", False)
                if point:
                    table.parameter(name, "", "zetaVS", 0, 0, FIXED, "1/nm")
                else:
                    table.parameter(name, "", "zetaVS", 4, 20, BOUNDED, "1/nm")
                table.rows[name]["rowVS"] = min(atom.row, choices.max_row)
            if kind == SHELL:
                table.parameter(name, POLARIZATION, "alpha", 1e-6, 12, BOUNDED, "Angstrom3")
                table.parameter(name, POLARIZATION, "rhyper", 0.0, 0.04, FIXED, "nm")
                table.parameter(name, POLARIZATION, "fchyper", 0, 5e8, BOUNDED, "kJ/mol nm4")
            _vdw(table, choices, name, atom.element, kind)
            _vdw_correction(table, name, correction, False)
            if kind == ATOM:
                # Ions and noble gases need no electronegativity
                trained = BOUNDED if atom.bonds > 0 else FIXED
                table.parameter(name, EEM, "eta", 4, 20, trained, "eV")
                table.parameter(name, EEM, "chi", 4, 12, trained, "eV/e")
            if choices.induction != "NONE" and kind != VSITE:
                on = choices.induction_atom if kind == ATOM else choices.induction_shell
                bounds = INDUCTION_POTENTIALS[choices.induction].bounds
                for param, (low, high, unit) in bounds.items():
                    table.parameter(name, INDUCTION, param, low, high,
                                    BOUNDED if on else FIXED, unit)
            if kind == ATOM:
                table.rows[name]["comment"] = atom.comment

    for name in choices.vdw_sites:
        if name not in site_types and name not in second_charges:
            raise GeneratorError("The virtual site %s is to get Van der Waals parameters, but "
                                 "the force field has no such site" % name)

    # The parameters that the user fixed, of the types that are there
    for entry in choices.fixed:
        if entry["type"] in table.rows:
            table.fix(entry["type"], entry["interaction"], entry["parameter"], entry["value"],
                      entry["unit"])

    options: Dict[str, Any] = {
        "nexcl": choices.nexcl,
        "epsilonr": choices.epsilonr,
        "vdwfn": choices.vdw,
        "qdist": "GAUSSIAN" if choices.charge_model == POINT_GAUSSIAN else choices.charge_model,
        "qsymm": choices.symmetric,
        "bondfn": choices.bonds,
        "anglefn": choices.angles,
        "dihfn": choices.dihedrals,
    }
    if choices.induction != "NONE":
        options["icfn"] = choices.induction
    if choices.shell_follows != "no":
        options["fixkshell"] = choices.shell_follows
        if choices.kshell is not None:
            options["kshell"] = choices.kshell
    rules = ["%s:%s:%s" % (itype, param, rule)
             for itype, params in choices.rules.items() for param, rule in params.items()]
    if correction:
        options["vdwcorr"] = True
        rules += ["%s:%s:%s" % (VDW_CORRECTION, param, rule)
                  for param, rule in _VDW_CORRECTION_RULES]
    if rules:
        options["cr"] = " ".join(rules)
    vsites = None
    if sites:
        # What the files of the predefined sites say about them goes along
        about = [text for text in (describe_vsites(name) for name in choices.vsite_files) if text]
        description = "Virtual sites selected in the user interface of ACT"
        if about:
            description += ": " + "; ".join(about)
        vsites = json.dumps({"description": description, "virtual_sites": sites},
                            indent=2) + "\n"
    return GeneratorInput(atomtypes=table.text(), vsites=vsites, options=options)


def offered(functions: Tuple[str, ...], gen_ff: Optional[Any], option: str) -> List[str]:
    """!
    @brief The functions that the program has, as an older alexandria does
    not know the newest ones, such as CROSS_BOND_ANGLES.
    @param functions The functions that can be chosen here.
    @param gen_ff    The description of the command gen_ff from
    act.program_info(), None if it is not known.
    @param option    The option of gen_ff that takes the function.
    @return The functions that the option accepts, in the same order; all
    of them if the program does not tell what it accepts or accepts none of
    them.
    """
    if gen_ff is None or not gen_ff.has_option(option):
        return list(functions)
    accepted = gen_ff.options[option].values
    known = [name for name in functions if name in accepted]
    # A list without any choice is of no use to the page
    return known or list(functions)


def describe(atom_types: Mapping[str, AtomType], gen_ff: Optional[Any] = None) -> Dict[str, Any]:
    """!
    @brief What the browser needs to show the choices.
    @param atom_types The atom types that can be chosen.
    @param gen_ff     The description of the command gen_ff from
    act.program_info(), which tells what functions the program has. With
    None all the functions are offered.
    @return The atom types, the potentials with their parameters and the
    combination rules that are proposed, the charge models, the functions,
    the predefined virtual sites with what their files say about them and
    their sites, the charge of a virtual site and the limits of the numbers.
    """
    def potentials(group: Mapping[str, Potential]) -> List[Dict[str, Any]]:
        """The potentials of a group as a list"""
        return [{"name": p.name, "comment": p.comment,
                 "parameters": [{"name": name, "min": low, "max": high, "unit": unit}
                                for name, (low, high, unit) in p.bounds.items()],
                 "rules": dict(p.rules)} for p in group.values()]

    return {
        "atomtypes": [{"name": t.name, "element": t.element, "bonds": t.bonds,
                       "charge": t.charge, "default": t.default, "vsite": t.vsite,
                       "shell": t.shell, "comment": t.comment} for t in atom_types.values()],
        "vdw": {"default": DEFAULT_VDW, "potentials": potentials(VDW_POTENTIALS),
                "nexcl": list(NEXCL_RANGE)},
        "charge": {"default": DEFAULT_CHARGE_MODEL,
                   "models": [{"name": name, "comment": comment}
                              for name, comment in CHARGE_MODELS.items()],
                   "epsilonr": list(EPSILONR_RANGE), "maxrow": list(ROW_RANGE),
                   "second_charge": POINT_GAUSSIAN},
        "bonds": offered(BOND_FUNCTIONS, gen_ff, FUNCTION_OPTIONS["bonds"]),
        "angles": offered(ANGLE_FUNCTIONS, gen_ff, FUNCTION_OPTIONS["angles"]),
        "dihedrals": offered(DIHEDRAL_FUNCTIONS, gen_ff, FUNCTION_OPTIONS["dihedrals"]),
        "induction": {"default": "NONE", "potentials": potentials(INDUCTION_POTENTIALS)},
        "vsites": [{"name": name, "label": label, "sigma_hole": sigma,
                    "description": describe_vsites(name), "sites": read_vsites(name)}
                   for name, (label, sigma) in VSITE_FILES.items()],
        "vsite_charge": {"min": VSITE_CHARGE_RANGE[0], "max": VSITE_CHARGE_RANGE[1],
                         "limits": list(VSITE_CHARGE_LIMITS)},
        "rules": list(COMBINATION_RULES),
        "site_kinds": [{"name": name, "atoms": natom, "parameters": list(names)}
                       for name, (natom, names) in SITE_KINDS.items()],
        "shells": {"follows": [{"name": name, "comment": comment}
                               for name, comment in SHELL_FOLLOWS.items()],
                   "kshell": list(KSHELL_RANGE)},
    }
