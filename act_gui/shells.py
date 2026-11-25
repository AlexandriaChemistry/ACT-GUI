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
@brief The particles of a molecule with its shells where the force field
puts them: relaxed in the field of the other particles, not on their atoms.

gentop describes the particles of a molecule, with the shells on the atoms
they belong to. A shell that is relaxed is displaced from its atom, by the
field of the charges around it, which is what makes the atom polarizable
and what the viewers of the page show. The positions come from a single
point calculation, alexandria simulate -sp -ws, which relaxes the shells
and writes all particles to a pdb file; simulate does not describe the
particles, so both programs run, as one job.

The two programs do not have the same particles for a force field with
second charges: gentop turns the second charge of an atom into a virtual
site on the atom, since GROMACS and OpenMM have no second charges
(ForceField::expandSecondCharges), where simulate, minimize and nma keep
it on the atom. Those virtual sites are put back here, see fold(), such
that the particles are those of the other programs.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Mapping, Optional

## The largest distance in nm between an atom in the structure of gentop
## and the same atom in the structure with the relaxed shells: the files
## have three decimals in Angstrom
SAME_PLACE = 5e-4

## The name of the file with the relaxed structure, in the directory RELAX_DIR of the job
RELAXED = "relaxed.pdb"
RELAX_DIR = "relax"

## The options of the command that also go to the single point calculation
SHARED = ("ff", "qalg")


class ShellError(ValueError):
    """!
    @brief Shells that cannot be given their relaxed positions. The message
    is for the user.
    """


def _expanded(particle: Mapping[str, Any], particles: List[Mapping[str, Any]]) -> bool:
    """!
    @brief Whether a particle is a second charge that gentop made a virtual
    site of: a virtual site on one atom with the name that
    ForceField::expandSecondCharges gives it, v1 and the type of the atom,
    with a number after it if that name was taken.
    @param particle  The particle.
    @param particles All particles.
    @return True if it is one.
    """
    parents = particle.get("parents")
    if particle.get("particletype") != "VSite" or not isinstance(parents, list) \
            or len(parents) != 1:
        return False
    parent = parents[0]
    if isinstance(parent, bool) or not isinstance(parent, int) \
            or not 0 <= parent < len(particles):
        return False
    owner = particles[parent]
    name = "v1" + str(owner.get("type", ""))
    mine = str(particle.get("type", ""))
    return owner.get("particletype") == "Atom" and "charge_vs" not in owner and \
        (mine == name or re.fullmatch(re.escape(name) + r"_\d+", mine) is not None)


def fold(compound: Any) -> Any:
    """!
    @brief Put the second charges that gentop made virtual sites of back on
    their atoms, as charge_vs, such that the particles are those of
    simulate, minimize and nma.
    @param compound A compound as gentop describes it: "molecule" with
    "particles" and "bonds", and "coordinates" with "positions", one for
    each particle.
    @return The compound without those virtual sites, with the indices of
    the parents and of the bonds as they are then. The compound itself if
    it has none, or if it is not as expected.
    """
    if not isinstance(compound, Mapping) or not isinstance(compound.get("molecule"), Mapping):
        return compound
    molecule = compound["molecule"]
    particles = molecule.get("particles")
    coordinates = compound.get("coordinates")
    positions = coordinates.get("positions") if isinstance(coordinates, Mapping) else None
    if not isinstance(particles, list) or not isinstance(positions, list) \
            or len(positions) != len(particles) \
            or not all(isinstance(p, Mapping) for p in particles):
        return compound
    gone = [k for k, particle in enumerate(particles) if _expanded(particle, particles)]
    if not gone:
        return compound
    # One second charge for an atom
    owners = [particles[k]["parents"][0] for k in gone]
    if len(set(owners)) != len(owners):
        return compound
    place: Dict[int, int] = {}
    kept: List[Dict[str, Any]] = []
    for k, particle in enumerate(particles):
        if k not in gone:
            place[k] = len(kept)
            kept.append(dict(particle))
    for k, owner in zip(gone, owners):
        kept[place[owner]]["charge_vs"] = particles[k].get("charge", 0)
    for particle in kept:
        if isinstance(particle.get("parents"), list):
            if any(parent not in place for parent in particle["parents"]):
                return compound
            particle["parents"] = [place[parent] for parent in particle["parents"]]
    bonds = []
    for bond in molecule.get("bonds") or []:
        atoms = bond.get("atoms") if isinstance(bond, Mapping) else None
        if not isinstance(atoms, list) or any(atom not in place for atom in atoms):
            return compound
        bonds.append(dict(bond, atoms=[place[atom] for atom in atoms]))
    made = dict(compound)
    made["molecule"] = dict(molecule, particles=kept, bonds=bonds)
    made["coordinates"] = dict(coordinates, positions=[positions[k] for k in range(len(particles))
                                                       if k not in gone])
    return made


def pdb_positions(text: str) -> List[List[float]]:
    """!
    @brief The positions of the particles in the first structure of a pdb
    file that alexandria wrote with -ws, which has a line for each particle.
    @param text The file.
    @return The position of each particle in nm.
    @throws ShellError if a position cannot be read.
    """
    positions: List[List[float]] = []
    for line in text.splitlines():
        if line.startswith(("ATOM", "HETATM")):
            try:
                positions.append([float(line[30:38]) / 10, float(line[38:46]) / 10,
                                  float(line[46:54]) / 10])
            except ValueError as exc:
                raise ShellError("A position in the structure with the relaxed shells "
                                 "cannot be read") from exc
        elif line.startswith("ENDMDL") and positions:
            break
    return positions


def relaxed(compound: Mapping[str, Any], text: str) -> Dict[str, Any]:
    """!
    @brief Give the shells of a compound the positions of a structure in
    which they are relaxed.
    @param compound The compound as gentop describes it, after fold().
    @param text     The pdb file of the single point calculation, written
    with -ws.
    @return The compound with the positions of that file. The atoms and the
    virtual sites are where they were, the shells are displaced.
    @throws ShellError if the file does not have the same particles, or has
    the atoms somewhere else.
    """
    particles = compound["molecule"]["particles"]
    before = compound["coordinates"]["positions"]
    after = pdb_positions(text)
    if len(after) != len(particles):
        raise ShellError("The structure with the relaxed shells has %d particles, "
                         "the molecule has %d" % (len(after), len(particles)))
    for particle, old, new in zip(particles, before, after):
        if particle.get("particletype") == "Shell":
            continue
        if any(abs(a - b) > SAME_PLACE for a, b in zip(old, new)):
            raise ShellError("The structure with the relaxed shells has its atoms "
                             "somewhere else than the molecule")
    made = dict(compound)
    made["coordinates"] = dict(compound["coordinates"], positions=after)
    return made


def has_shells(compound: Any) -> bool:
    """!
    @brief Whether a compound has shells.
    @param compound The compound as gentop describes it.
    @return True if one of its particles is a shell.
    """
    try:
        return any(p.get("particletype") == "Shell" for p in compound["molecule"]["particles"])
    except (KeyError, TypeError, AttributeError):
        return False


def run_particles(run: Callable[..., Any], command: str, *, workdir: "os.PathLike[str]",
                  timeout: Optional[float] = None, stop: Any = None, **options: Any) -> Any:
    """!
    @brief Describe a molecule with gentop and relax its shells with a
    single point calculation, as one job.
    @param run     The function that runs a command of alexandria, act.run.
    @param command The command of the task, gentop.
    @param workdir The directory of the job.
    @param timeout The time limit of each command.
    @param stop    The event that stops the job.
    @param options The options of gentop: the molecule, the force field,
    how the charges are made and its output files.
    @return The result of gentop. Its first compound has the second charges
    on the atoms, see fold(), the positions of its shells relaxed, and
    "shells": whether it has shells ("present"), whether they are relaxed
    ("relaxed") and, if they are not, why ("reason"). A molecule of which
    the shells cannot be relaxed is described all the same.
    """
    result = run(command, workdir=workdir, timeout=timeout, stop=stop, **options)
    data = result.data
    molecules = data.get("molecules") if isinstance(data, Mapping) else None
    if not isinstance(molecules, list) or not molecules:
        return result
    compound = fold(molecules[0])
    state: Dict[str, Any] = {"present": has_shells(compound), "relaxed": False}
    if state["present"]:
        here = Path(workdir) / RELAX_DIR
        here.mkdir(parents=True, exist_ok=True)
        structure = here / RELAXED
        # The same structure as gentop had: its file, or what gentop wrote
        # of a SMILES string, of which each program makes its own coordinates
        source = options.get("f") if options.get("f") is not None else options.get("c")
        shared = {key: value for key, value in options.items() if key in SHARED}
        try:
            if source is None:
                raise ShellError("There is no structure to relax the shells of")
            run("simulate", workdir=here, timeout=timeout, stop=stop, f=source, sp=True,
                ws=True, c=structure, **shared)
            compound = relaxed(compound, structure.read_text(encoding="utf-8",
                                                             errors="replace"))
            state["relaxed"] = True
        except Exception as exc:  # pylint: disable=broad-except
            if (stop is not None and stop.is_set()) or type(exc).__name__ == "ActCancelledError":
                raise
            state["reason"] = str(exc)
    compound = dict(compound, shells=state)
    return SimpleNamespace(data=dict(data, molecules=[compound] + list(molecules[1:])),
                           files=result.files)
