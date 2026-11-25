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
@brief The interaction energy of a dimer as a function of the distance
between its two compounds, as the script dimer_scan of ACT-utilities
computes it.

The user picks an atom in each compound. For each distance the second
compound is moved along the line between the two atoms until they are at
that distance, and alexandria minimize relaxes the dimer with the two atoms
frozen, optionally with the bond lengths constrained. The interaction
energy of the structure it finds is the point at that distance. All of it is
one job: the scan reports each point when it is done, and keeps the
minimized structures so that the page can show one.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Mapping, Optional

## The most distances of a scan
MAX_POINTS = 200
## The shortest and longest distance in nm
DISTANCE_RANGE = (0.05, 3.0)
## The defaults: from, to and the step, in nm, as the script has them
DEFAULTS = {"rmin": 0.24, "rmax": 0.6, "step": 0.01}
## The tolerance of the minimizations, as the script has it
TOLERANCE = 1e-9
## The terms of the interaction energy in the xvg file, in this order, if
## the force field has them
TERMS = ("EPOT", "COULOMB", "INDUCTION", "DISPERSION", "EXCHANGE")
## The file of the minimized structure in the directory of a distance
STRUCTURE = "final.pdb"


class DimerScanError(ValueError):
    """!
    @brief A scan that cannot be done. The message is for the user.
    """


def settings(spec: Any) -> Dict[str, Any]:
    """!
    @brief Check the settings of a scan.
    @param spec The request: atom1 and atom2, the numbers from one of an
    atom in the first and one in the second compound, rmin, rmax and step,
    the distances in nm, and constrain, whether bonds are constrained.
    @return The settings, with the defaults where nothing was given.
    @throws DimerScanError if one of them is not possible.
    """
    if not isinstance(spec, Mapping):
        raise DimerScanError("Give the atoms between which the distance is scanned")
    for key in spec:
        if key not in ("atom1", "atom2", "rmin", "rmax", "step", "constrain"):
            raise DimerScanError("A scan has no setting '%s'" % (key,))
    made: Dict[str, Any] = {}
    for key in ("atom1", "atom2"):
        value = spec.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise DimerScanError("Pick an atom in each compound")
        made[key] = value
    if made["atom1"] == made["atom2"]:
        raise DimerScanError("Pick two different atoms, one in each compound")
    for key, default in DEFAULTS.items():
        value = spec.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(value):
            raise DimerScanError("The distances are numbers in nm")
        made[key] = float(value)
    low, high = DISTANCE_RANGE
    if not low <= made["rmin"] < made["rmax"] <= high:
        raise DimerScanError("The distances go from at least %g to at most %g nm, the first "
                             "below the last" % (low, high))
    if made["step"] <= 0:
        raise DimerScanError("The step of the distance is above zero")
    if len(distances(made)) > MAX_POINTS:
        raise DimerScanError("A scan has at most %d distances: take a larger step"
                             % MAX_POINTS)
    constrain = spec.get("constrain", False)
    if not isinstance(constrain, bool):
        raise DimerScanError("Constraining the bonds is yes or no")
    made["constrain"] = constrain
    return made


def distances(scan: Mapping[str, Any]) -> List[float]:
    """!
    @brief The distances of a scan.
    @param scan The settings.
    @return The distances in nm, from the first to the last.
    """
    count = int(math.floor((scan["rmax"] - scan["rmin"]) / scan["step"] + 1e-6)) + 1
    return [scan["rmin"] + i * scan["step"] for i in range(count)]


def read_pdb(text: str) -> List[Dict[str, Any]]:
    """!
    @brief The atoms of a pdb file as gentop writes it.
    @param text The file.
    @return For each atom its line, number, residue and position in Angstrom.
    @throws DimerScanError if a line of an atom cannot be read.
    """
    atoms = []
    for line in text.splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        try:
            atoms.append({"line": line, "serial": int(line[6:11]), "residue": int(line[22:26]),
                          "x": float(line[30:38]), "y": float(line[38:46]),
                          "z": float(line[46:54])})
        except ValueError as exc:
            raise DimerScanError("The structure of the dimer cannot be read") from exc
    return atoms


def placed(atoms: List[Dict[str, Any]], atom1: int, atom2: int, distance: float) -> str:
    """!
    @brief The dimer with the second compound moved along the line between
    the two atoms, such that they are at the distance.
    @param atoms    The atoms, see read_pdb(). The compounds are the first
    residue and the others.
    @param atom1    The number of an atom of the first compound.
    @param atom2    The number of an atom of the second compound.
    @param distance The distance in nm.
    @return The pdb file.
    @throws DimerScanError if the atoms are not one in each compound.
    """
    by_serial = {atom["serial"]: atom for atom in atoms}
    first = atoms[0]["residue"] if atoms else None
    for serial in (atom1, atom2):
        if serial not in by_serial:
            raise DimerScanError("The dimer has no atom %d" % serial)
    if by_serial[atom1]["residue"] != first:
        raise DimerScanError("Atom %d is not in the first compound" % atom1)
    if by_serial[atom2]["residue"] == first:
        raise DimerScanError("Atom %d is not in the second compound" % atom2)
    a, b = by_serial[atom1], by_serial[atom2]
    delta = [b[k] - a[k] for k in ("x", "y", "z")]
    now = math.sqrt(sum(d * d for d in delta))
    if now < 1e-6:
        raise DimerScanError("The two atoms are at the same place")
    shift = [(10 * distance - now) * d / now for d in delta]
    lines = []
    for atom in atoms:
        x, y, z = atom["x"], atom["y"], atom["z"]
        if atom["residue"] != first:
            x, y, z = x + shift[0], y + shift[1], z + shift[2]
        line = atom["line"]
        lines.append("%s%8.3f%8.3f%8.3f%s" % (line[:30], x, y, z, line[54:]))
    return "\n".join(lines + ["END"]) + "\n"


def structure(workdir: "os.PathLike[str]", index: int) -> Path:
    """!
    @brief The minimized structure of a distance of a scan.
    @param workdir The directory of the job.
    @param index   The place of the distance.
    @return The pdb file.
    @throws DimerScanError if there is none.
    """
    path = Path(workdir) / ("%04d" % index) / STRUCTURE
    if isinstance(index, bool) or not 0 <= index < MAX_POINTS or not path.is_file():
        raise DimerScanError("There is no structure at this distance")
    return path


def _cancelled() -> Exception:
    """!
    @brief The error with which a scan that is stopped ends, as act.run ends.
    @return The error.
    """
    return type("ActCancelledError", (RuntimeError,), {})("The scan was stopped")


def run_scan(run: Callable[..., Any], command: str, *, workdir: "os.PathLike[str]",
             scan: Mapping[str, Any], timeout: Optional[float] = None, stop: Any = None,
             on_progress: Optional[Callable[[Any], None]] = None, **options: Any) -> Any:
    """!
    @brief Do a scan, as one job.
    @param run         The function that runs a command of alexandria, act.run.
    @param command     The command of the task, minimize.
    @param workdir     The directory of the job.
    @param scan        The settings, see settings().
    @param timeout     The time limit of each command.
    @param stop        The event that stops the scan.
    @param on_progress Called with each point when it is done.
    @param options     The molecule, force field and charges, as for minimize.
    @return An object with data, the atoms and the points, and files, the
    xvg file with the energies.
    @throws DimerScanError if the dimer cannot be scanned.
    """
    workdir = Path(workdir)
    common = {key: value for key, value in options.items() if key in ("ff", "qalg")}
    molecule = {key: value for key, value in options.items() if key in ("f", "smiles")}
    # The dimer as alexandria reads it: the atoms, a residue for each compound
    start = workdir / "start"
    start.mkdir(parents=True, exist_ok=True)
    dimer = start / "dimer.pdb"
    run("gentop", workdir=start, timeout=timeout, stop=stop, **common, **molecule, c=dimer)
    atoms = read_pdb(dimer.read_text(encoding="utf-8", errors="replace"))
    if len({atom["residue"] for atom in atoms}) != 2:
        raise DimerScanError("A scan needs a structure of two compounds, this one has %d"
                             % len({atom["residue"] for atom in atoms}))
    placed(atoms, scan["atom1"], scan["atom2"], scan["rmin"])
    freeze = workdir / "freeze.ndx"
    freeze.write_text("[ freeze ]\n%d %d\n" % (scan["atom1"], scan["atom2"]))
    points: List[Dict[str, Any]] = []
    for index, distance in enumerate(distances(scan)):
        if stop is not None and stop.is_set():
            raise _cancelled()
        here = workdir / ("%04d" % index)
        here.mkdir(parents=True, exist_ok=True)
        begin = here / "start.pdb"
        begin.write_text(placed(atoms, scan["atom1"], scan["atom2"], distance))
        # The structure with the shells and the virtual sites, for the viewer
        extra: Dict[str, Any] = {"freeze": freeze, "c": here / STRUCTURE, "json": True,
                                 "toler": TOLERANCE, "ws": True}
        if scan["constrain"]:
            extra["constrain"] = "bonds"
        point: Dict[str, Any] = {"index": index, "distance": distance}
        try:
            result = run(command, workdir=here, timeout=timeout, stop=stop, **common,
                         f=begin, **extra)
        except Exception as exc:  # pylint: disable=broad-except
            if (stop is not None and stop.is_set()) or type(exc).__name__ == "ActCancelledError":
                raise
            point["error"] = str(exc)
        else:
            data = result.data if isinstance(result.data, Mapping) else {}
            inter = data.get("Interaction energies") or {}
            point["energies"] = {term: value["value"] for term, value in inter.items()
                                 if isinstance(value, Mapping) and "value" in value}
            after = (data.get("Energies after") or {}).get("EPOT")
            if isinstance(after, Mapping):
                point["epot"] = after.get("value")
            point["status"] = data.get("status", "")
        points.append(point)
        if on_progress is not None:
            on_progress(point)
    xvg = workdir / "scan.xvg"
    xvg.write_text(_xvg(points))
    return SimpleNamespace(data={"atoms": [scan["atom1"], scan["atom2"]], "points": points,
                                 "constrain": scan["constrain"]},
                           files={"xvg": [xvg]})


def _xvg(points: List[Mapping[str, Any]]) -> str:
    """!
    @brief The energies of a scan as an xvg file: the distance and a column
    for each term that the force field has.
    @param points The points.
    @return The file.
    """
    found = [term for term in TERMS if any(term in p.get("energies", {}) for p in points)]
    lines = ['@ title "Interaction energy"', '@ xaxis label "Distance (nm)"',
             '@ yaxis label "Energy (kJ/mol)"']
    lines += ['@ s%d legend "%s"' % (i, term) for i, term in enumerate(found)]
    for p in points:
        if "energies" in p:
            lines.append("%10g" % p["distance"]
                         + "".join("  %12g" % p["energies"].get(term, 0.0) for term in found))
    return "\n".join(lines) + "\n"
