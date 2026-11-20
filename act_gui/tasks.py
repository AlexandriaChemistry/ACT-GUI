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
@brief The calculations that the interface offers and how a request from
the browser becomes the arguments of a command of alexandria.

The browser never sends a command line. It sends the name of a task, the
molecule, the force field and a few options. Only options that are listed
here for the task are accepted, the files are looked up by the identifier
they got when they were uploaded, and the names of the output files are
made here. Whatever the browser sends, it cannot make the program read or
write a file it chooses, or switch on an option that is not listed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

from .settings import Settings
from .uploads import UploadStore


class RequestError(ValueError):
    """!
    @brief A request that is not accepted. The message is for the user.
    """


@dataclass(frozen=True)
class Task:
    """!
    @brief A calculation that the interface offers.
    """
    ## Name of the task in requests
    name: str
    ## The command of alexandria that does it
    command: str
    ## What the task does, for the interface
    title: str
    ## The options of the command that the user can set
    options: Tuple[str, ...] = ()
    ## Options that are always given
    fixed: Mapping[str, Any] = field(default_factory=dict)
    ## Output files with a name of our choice, by option name. They are
    ## written in the directory of the job.
    outputs: Mapping[str, str] = field(default_factory=dict)
    ## Whether the command reports its progress
    progress: bool = False
    ## The output files that can be downloaded, by option name
    downloads: Tuple[str, ...] = ()
    ## The output files with data that can be saved as a plot, by option name
    plots: Tuple[str, ...] = ()
    ## Whether the output files are offered as an archive for OpenMM
    package: bool = False


## The options to choose how the charges are made, the same for all tasks
_CHARGE_OPTIONS = ("qalg",)

## The tasks by name
TASKS: Dict[str, Task] = {task.name: task for task in (
    Task(name="charges", command="gentop",
         title="Charges and topology",
         options=_CHARGE_OPTIONS,
         fixed={"json": True},
         outputs={"c": "molecule.pdb"},
         downloads=("c",)),
    Task(name="minimize", command="minimize",
         title="Energy minimization",
         options=_CHARGE_OPTIONS + ("minalg", "maxiter", "toler"),
         fixed={"json": True},
         outputs={"c": "minimized.pdb"},
         downloads=("c", "g")),
    Task(name="nma", command="nma",
         title="Normal mode analysis",
         options=_CHARGE_OPTIONS + ("minalg", "maxiter", "toler", "linewidth"),
         fixed={"ir": True, "json": True},
         outputs={"c": "minimized.pdb"},
         downloads=("c", "ir", "g"),
         plots=("ir",)),
    Task(name="simulate", command="simulate",
         title="Molecular dynamics simulation",
         options=_CHARGE_OPTIONS + ("nsteps", "deltat", "temp", "nstener", "nstxout",
                                    "seed", "minimize", "constrain"),
         fixed={"json": True},
         outputs={"c": "final.pdb"},
         progress=True,
         downloads=("o", "e", "c", "g"),
         plots=("e",)),
    Task(name="openmm", command="gentop",
         title="Files for a simulation with OpenMM",
         options=_CHARGE_OPTIONS,
         outputs={"openmm": "openmm.xml", "c": "conf.pdb"},
         downloads=("openmm", "c", "g"),
         package=True),
)}


def build_request(task: Task, request: Mapping[str, Any], settings: Settings,
                  uploads: UploadStore) -> Dict[str, Any]:
    """!
    @brief Turn a request from the browser into the options of a command.
    @param task     The task.
    @param request  The request: "molecule" with either "smiles" or "file",
    optionally "forcefield" with "file", and "options".
    @param settings The settings, for the limits and the default force field.
    @param uploads  The uploaded files.
    @return The keyword arguments for act.run(), without the arguments of
    the run itself. The types of the values are checked later by act.
    @throws RequestError if the request is not accepted.
    """
    if not isinstance(request, Mapping):
        raise RequestError("The request must be an object")
    options: Dict[str, Any] = {}

    # The molecule: a SMILES string or an uploaded file
    molecule = request.get("molecule")
    if not isinstance(molecule, Mapping):
        raise RequestError("The request has no molecule")
    smiles = molecule.get("smiles")
    file_id = molecule.get("file")
    if (smiles is None) == (file_id is None):
        raise RequestError("Give a SMILES string or a molecule file, not both")
    if smiles is not None:
        if not isinstance(smiles, str) or not smiles.strip():
            raise RequestError("The SMILES string is empty")
        if len(smiles) > settings.max_smiles_length:
            raise RequestError("The SMILES string is longer than %d characters"
                               % settings.max_smiles_length)
        options["smiles"] = smiles.strip()
    else:
        upload = uploads.get(file_id)
        if upload is None or upload.kind != "molecule":
            raise RequestError("The molecule file is not known, upload it again")
        options["f"] = uploads.path(upload)

    # The force field: an uploaded file or the default
    forcefield = request.get("forcefield") or {}
    if not isinstance(forcefield, Mapping):
        raise RequestError("The force field must be an object")
    ff_id = forcefield.get("file")
    if ff_id is None:
        options["ff"] = settings.default_forcefield
    else:
        upload = uploads.get(ff_id)
        if upload is None or upload.kind != "forcefield":
            raise RequestError("The force field file is not known, upload it again")
        options["ff"] = uploads.path(upload)

    # Only the options of the task, within the limits
    given = request.get("options") or {}
    if not isinstance(given, Mapping):
        raise RequestError("The options must be an object")
    limits = settings.limits.get(task.name, {})
    for name, value in given.items():
        if name not in task.options:
            raise RequestError("The task %s has no option '%s'" % (task.name, name))
        if value is None or value == "":
            continue
        if name in limits:
            low, high = limits[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise RequestError("Option '%s' must be a number" % name)
            if not low <= value <= high:
                raise RequestError("Option '%s' must be between %g and %g" % (name, low, high))
        options[name] = value
    options.update(task.fixed)
    return options


def find_task(name: Any) -> Task:
    """!
    @brief Look up a task.
    @param name The name in the request.
    @return The task.
    @throws RequestError if there is no such task.
    """
    if not isinstance(name, str) or name not in TASKS:
        raise RequestError("Unknown task '%s'" % (name,))
    return TASKS[name]


def describe_task(task: Task, command_info: Optional[Any]) -> Dict[str, Any]:
    """!
    @brief What the browser needs to know about a task to build its form.
    @param task         The task.
    @param command_info The description of the command from
    act.program_info(), or None if the program is not available.
    @return Name, title and for each option the type, the default, the
    allowed values and the help text.
    """
    described = []
    for name in task.options:
        entry: Dict[str, Any] = {"name": name}
        if command_info is not None and command_info.has_option(name):
            option = command_info.options[name]
            entry.update({"type": option.type, "default": option.default,
                          "values": list(option.values),
                          "description": option.description})
        described.append(entry)
    return {"name": task.name, "title": task.title, "command": task.command,
            "progress": task.progress, "downloads": list(task.downloads),
            "plots": list(task.plots), "package": task.package,
            "options": described}
