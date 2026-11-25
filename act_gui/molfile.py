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
@brief A structure of a molprop file as an SDF file, from which gentop
makes the particles of the force field: the atoms, the shells and the
virtual sites, with their charges.

gentop reads a compound of a molprop file only if it is a monomer, and a
dimer only from a structure file. A pdb file has no charges, from which
RDKit cannot find the bond orders of an ion, so the structure goes as an
SDF file, with the bonds of the molprop file and the formal charges.

A molprop file gives the charge of each compound, not that of each atom,
and a bond in an aromatic ring or in a charged group such as a carboxylate
has the order 1.5. The bonds in a ring are written as aromatic, which RDKit
turns into single and double bonds itself. The other bonds of order 1.5
become single or double bonds, and each atom gets the formal charge that
its bonds give it, such that the formal charges of a compound add up to
its charge. If they cannot, the compound gets no formal charges.
"""
from __future__ import annotations

import itertools
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

## The usual valences of the elements of organic compounds, the number of
## bonds of a neutral atom. An element without them, such as that of a
## metal ion, has a charge only if it is a compound by itself.
VALENCES: Mapping[str, Tuple[int, ...]] = {
    "H": (1,), "B": (3,), "C": (4,), "N": (3,), "O": (2,), "F": (1,), "Si": (4,),
    "P": (3, 5), "S": (2, 4, 6), "Cl": (1,), "Se": (2, 4, 6), "Br": (1,), "I": (1,)}

## The most bonds of order 1.5 outside rings in one compound of which all
## ways to write them as single and double bonds are tried
MAX_CHOICES = 12

## The bond type of an aromatic bond in an SDF file
AROMATIC = 4


class MolfileError(ValueError):
    """!
    @brief A structure that cannot be written as an SDF file. The message is
    for the user.
    """


def _in_ring(count: int, bonds: Sequence[Tuple[int, int, float]], index: int) -> bool:
    """!
    @brief Whether a bond is in a ring: whether its atoms are connected
    without it.
    @param count The number of atoms.
    @param bonds The bonds: the atoms, from zero, and the order.
    @param index The place of the bond.
    @return True if it is in a ring.
    """
    neighbours: List[List[int]] = [[] for _ in range(count)]
    for place, (i, j, _) in enumerate(bonds):
        if place != index:
            neighbours[i].append(j)
            neighbours[j].append(i)
    start, goal = bonds[index][0], bonds[index][1]
    seen, todo = {start}, [start]
    while todo:
        atom = todo.pop()
        for other in neighbours[atom]:
            if other == goal:
                return True
            if other not in seen:
                seen.add(other)
                todo.append(other)
    return False


def _formal(element: str, valence: float, aromatic: bool) -> Tuple[int, Tuple[int, ...]]:
    """!
    @brief The formal charge of an atom from the sum of the orders of its
    bonds.
    @param element  The element.
    @param valence  The sum of the orders of its bonds, 1.5 for an aromatic bond.
    @param aromatic Whether it has an aromatic bond, after which its
    charge is not certain: a nitrogen in pyridine and one in pyrrole have
    other numbers of bonds and the same charge.
    @return The charge, and the charges that it may have instead, which
    the charge of its compound decides. A carbon with three bonds is a
    cation or an anion.
    """
    allowed = VALENCES.get(element)
    if allowed is None:
        return 0, ()
    bonds = int(valence + 0.5)
    if bonds in allowed:
        return 0, ()
    if element == "C":
        return (0, (1, -1)) if bonds == 3 else (0, ())
    if element == "B":
        charge = 3 - bonds
    else:
        below = [v for v in allowed if v <= bonds]
        charge = bonds - (max(below) if below else min(allowed))
    if aromatic:
        # The bonds of an aromatic ring do not say whether the atom has one more
        return 0, (charge,)
    return charge, ()


def formal_charges(elements: Sequence[str], bonds: Sequence[Tuple[int, int, float]],
                   aromatic: Set[int], members: Sequence[int],
                   charge: int) -> List[int]:
    """!
    @brief The formal charges of the atoms of one compound, such that they
    add up to its charge where the bonds allow that.
    @param elements The element of each atom of the structure.
    @param bonds    The bonds of the structure with their orders, single and
    double bonds where the orders were 1.5 outside rings.
    @param aromatic The places of the bonds that are aromatic.
    @param members  The atoms of the compound.
    @param charge   The charge of the compound.
    @return The formal charge of each atom of the compound, in the order of members.
    """
    if len(members) == 1:
        return [charge]
    place = {atom: k for k, atom in enumerate(members)}
    valence = [0.0] * len(members)
    ringed = [False] * len(members)
    for index, (i, j, order) in enumerate(bonds):
        for atom in (i, j):
            if atom in place:
                valence[place[atom]] += order
                ringed[place[atom]] = ringed[place[atom]] or index in aromatic
    charges, others = [], []
    for k, atom in enumerate(members):
        certain, possible = _formal(elements[atom], valence[k], ringed[k])
        charges.append(certain)
        others.append(possible)
    # Those whose charge is not certain make up for what is missing
    rest = charge - sum(charges)
    for k in range(len(members)):
        fitting = [q for q in others[k] if q * rest > 0 and abs(q) <= abs(rest)]
        if fitting:
            charges[k] = fitting[0]
            rest -= fitting[0]
    return charges


def _orders(count: int, elements: Sequence[str], bonds: Sequence[Tuple[int, int, float]],
            fragments: Sequence[Tuple[Sequence[int], int]]) -> Tuple[List[Tuple[int, int, float]],
                                                                    Set[int], List[int]]:
    """!
    @brief The bonds as they are written, and the formal charges.
    @param count     The number of atoms.
    @param elements  The element of each atom.
    @param bonds     The bonds of the molprop file.
    @param fragments The atoms and the charge of each compound.
    @return The bonds with single, double or triple bonds and the order 1.5
    of an aromatic one, the places of the aromatic bonds, and the formal
    charge of each atom.
    @throws MolfileError if a bond has an order that cannot be written.
    """
    written = list(bonds)
    aromatic: Set[int] = set()
    loose: List[int] = []
    for index, (_, _, order) in enumerate(bonds):
        if order in (1, 2, 3):
            continue
        if order != 1.5:
            raise MolfileError("A bond has the order %g, which an SDF file cannot have" % order)
        if _in_ring(count, bonds, index):
            aromatic.add(index)
        else:
            loose.append(index)
    owner: Dict[int, int] = {}
    for number, (members, _) in enumerate(fragments):
        for atom in members:
            owner[atom] = number
    charges = [0] * count
    for number, (members, charge) in enumerate(fragments):
        mine = [index for index in loose if owner.get(bonds[index][0]) == number]
        best: Optional[Tuple[Tuple[int, int, int], List[Tuple[int, int, float]], List[int]]] = None
        # Of very many only the single bonds
        choices = [(1,) * len(mine)] if len(mine) > MAX_CHOICES \
            else list(itertools.product((1, 2), repeat=len(mine)))
        for choice in choices:
            trial = list(written)
            for index, order in zip(mine, choice):
                trial[index] = (bonds[index][0], bonds[index][1], order)
            found = formal_charges(elements, trial, aromatic, members, charge)
            # The charges as they should add up, as few as can be, and not on a carbon
            score = (abs(sum(found) - charge), sum(abs(q) for q in found),
                     sum(1 for atom, q in zip(members, found) if q and elements[atom] == "C"))
            if best is None or score < best[0]:
                best = (score, trial, found)
        if best is None:
            continue
        written = best[1]
        # Charges that do not add up are a guess that went wrong, as for
        # a compound without bonds: then RDKit has to do without
        if sum(best[2]) == charge:
            for atom, q in zip(members, best[2]):
                charges[atom] = q
    return written, aromatic, charges


def sdf(name: str, atoms: Sequence[Mapping[str, object]],
        bonds: Sequence[Tuple[int, int, float]],
        fragments: Sequence[Tuple[Sequence[int], int]]) -> str:
    """!
    @brief A structure as an SDF file (V2000).
    @param name      The name of the compound, the title of the file.
    @param atoms     The atoms: element and x, y and z in Angstrom.
    @param bonds     The bonds: the atoms, from zero, and the order.
    @param fragments The atoms, from zero, and the charge of each compound;
    empty for one compound without charge.
    @return The file.
    @throws MolfileError if it cannot be written.
    """
    count = len(atoms)
    if not 0 < count <= 999 or len(bonds) > 999:
        raise MolfileError("An SDF file has at most 999 atoms and 999 bonds")
    for i, j, _ in bonds:
        if not (0 <= i < count and 0 <= j < count) or i == j:
            raise MolfileError("A bond is between atoms that are not there")
    elements = [str(atom["element"]) for atom in atoms]
    parts = list(fragments) or [(list(range(count)), 0)]
    written, aromatic, charges = _orders(count, elements, bonds, parts)
    title = "".join(c if c.isprintable() else " " for c in name)[:80]
    lines = [title, "  ACT-GUI", ""]
    lines.append("%3d%3d  0  0  0  0  0  0  0  0999 V2000" % (count, len(written)))
    for atom in atoms:
        lines.append("%10.4f%10.4f%10.4f %-3s 0  0  0  0  0  0  0  0  0  0  0  0"
                     % (float(atom["x"]), float(atom["y"]), float(atom["z"]),  # type: ignore[arg-type]
                        atom["element"]))
    for index, (i, j, order) in enumerate(written):
        kind = AROMATIC if index in aromatic else int(order)
        lines.append("%3d%3d%3d  0" % (i + 1, j + 1, kind))
    charged = [(atom, q) for atom, q in enumerate(charges) if q]
    # At most eight charges on a line
    for start in range(0, len(charged), 8):
        chunk = charged[start:start + 8]
        lines.append("M  CHG%3d" % len(chunk) + "".join("%4d%4d" % (atom + 1, q) for atom, q in chunk))
    lines.append("M  END")
    lines.append("$$$$")
    return "\n".join(lines) + "\n"
