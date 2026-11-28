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
@brief The force fields that are installed, from which the user can choose.

The package forcefields of the repository ForceFields puts its files in
share/act/forcefields of the prefix, one directory for each kind, such as
Water/TIP4P-2005.xml. A force field is known by its path below such a
directory without .xml, Water/TIP4P-2005, which stays the same when the
files are installed somewhere else. The files are found again at each
request: there are a few dozen small ones, of which only the first lines
are read.
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

## The environment variable with directories of force fields of the user,
## separated as in PATH, which come before the installed ones
ENV_DIRS = "ACT_FORCEFIELDS"


@dataclass(frozen=True)
class LibraryForceField:
    """!
    @brief A force field file of the library.
    """
    ## The path below the directory of the library without .xml, such as Water/SPC
    id: str
    ## The file
    path: Path
    ## The paper or other source of the force field, empty if the file has none
    reference: str = ""
    ## When the file was signed, empty if the file has none
    timestamp: str = ""

    @property
    def name(self) -> str:
        """!
        @return The name of the file without .xml.
        """
        return self.id.rsplit("/", 1)[-1]

    @property
    def category(self) -> str:
        """!
        @return The directories of the file below that of the library,
        such as Water, empty for a file directly in it.
        """
        return self.id.rsplit("/", 1)[0] if "/" in self.id else ""

    def describe(self) -> Dict[str, str]:
        """!
        @return What the page shows of the force field, without the path,
        which is no business of a browser of another machine.
        """
        return {"id": self.id, "name": self.name, "category": self.category,
                "reference": self.reference, "timestamp": self.timestamp}


def default_dirs() -> List[Path]:
    """!
    @brief The directories in which force fields are looked for.
    @return In this order: those of the environment variable ACT_FORCEFIELDS,
    forcefields in the directory of the environment variable ACTDATA, and
    share/act/forcefields of the installation of ACT, or of this Python if
    the package act is not there or has no classes to tell where ACT is,
    as the package alexandria-python of conda-forge.
    """
    dirs = [Path(d) for d in os.environ.get(ENV_DIRS, "").split(os.pathsep) if d]
    actdata = os.environ.get("ACTDATA")
    if actdata:
        dirs.append(Path(actdata) / "forcefields")
    data = ""
    try:
        import act
        # Empty without the classes of ACT
        data = act.data_directory()
    except (ImportError, AttributeError, OSError):
        pass
    if not data:
        import sys
        data = os.path.join(sys.prefix, "share", "act")
    dirs.append(Path(data) / "forcefields")
    unique: List[Path] = []
    for d in dirs:
        d = d.expanduser()
        if d not in unique:
            unique.append(d)
    return unique


def _header(path: Path) -> Optional[Dict[str, str]]:
    """!
    @brief Read the reference and time stamp at the top of a force field file.
    @param path The file.
    @return The reference and the time stamp, empty strings for what the
    file does not have, or None if it is not a force field of ACT.
    """
    found = {"reference": "", "timestamp": ""}
    depth = 0
    try:
        for event, elem in ET.iterparse(str(path), events=("start", "end")):
            if event == "end":
                depth -= 1
                continue
            depth += 1
            if depth == 1:
                if elem.tag != "alexandria_chemistry":
                    return None
            elif depth == 2:
                if elem.tag == "option" and elem.get("key") == "reference":
                    found["reference"] = elem.get("value", "")
                elif elem.tag == "version":
                    found["timestamp"] = elem.get("timestamp", "")
                else:
                    # The options and the version come first
                    break
    except (ET.ParseError, OSError):
        return None
    return found if depth > 0 else None


def scan(dirs: Iterable[Path]) -> List[LibraryForceField]:
    """!
    @brief Find the force fields in directories and their subdirectories.
    @param dirs The directories, of which the first has priority: a force
    field that is in two of them is taken from the first.
    @return The force fields, ordered by kind and name.
    """
    found: Dict[str, LibraryForceField] = {}
    for base in dirs:
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.xml")):
            relative = path.relative_to(base)
            if any(part.startswith(".") for part in relative.parts):
                continue
            ff_id = relative.with_suffix("").as_posix()
            if ff_id in found:
                continue
            header = _header(path)
            if header is not None:
                found[ff_id] = LibraryForceField(id=ff_id, path=path, **header)
    return sorted(found.values(), key=lambda f: (f.category.lower(), f.name.lower()))


def find(dirs: Iterable[Path], ff_id: str) -> Optional[LibraryForceField]:
    """!
    @brief The force field with an identifier.
    @param dirs  The directories of the library.
    @param ff_id The identifier, such as Water/SPC.
    @return The force field, or None if the library does not have it. Only
    identifiers of the list are accepted, so a request cannot name another
    file.
    """
    for ff in scan(dirs):
        if ff.id == ff_id:
            return ff
    return None
