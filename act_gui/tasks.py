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

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from . import dimerscan, evaluation, ffgen, geometry, library, training, virial
from .jobs import InputFile
from .settings import Settings
from .uploads import UploadStore

## What the task needs: a molecule, a force field, a molprop file, or the
## choices from which a force field is made
MOLECULE, FORCEFIELD, MOLPROP, GENERATOR = "molecule", "forcefield", "molprop", "generator"
## A selection of the compounds of the molprop file, and the properties
## to compare a force field on
SELECTION, OBSERVABLES = "selection", "observables"
## A selection of the compounds of a molprop file only when the user chose
## some of them: without one the command uses all compounds of the file
CHOSEN = "chosen"
## What is trained of a force field
TRAINING = "training"
## The settings of a scan of the distance between the compounds of a dimer
SCAN = "scan"
## What an OpenMM simulation of the package writes, by the name under which it is fetched
OPENMM_RESULTS = {"energy": "energy.xvg", "temperature": "temperature.xvg", "final": "final.pdb",
                  "trajectory": "trajectory.pdb", "output": "output.txt"}
OPENMM_FILES = tuple(OPENMM_RESULTS)

## What the name of a parameter looks like, such as chi or bondlength
_PARAMETER_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
## What the identifier of a particle type or an interaction looks like,
## such as h_z, Na+ or c3_b~h_b. No spaces: those separate identifiers.
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_+~=#:.*-]{0,79}$")
## The largest number of identifiers in one change of a force field
MAX_IDENTIFIERS = 200


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
    ## Options that are given if the program has them: an older program
    ## runs the task without them
    optional: Mapping[str, Any] = field(default_factory=dict)
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
    ## What the request must give: molecule, forcefield, molprop, generator
    needs: Tuple[str, ...] = (MOLECULE, FORCEFIELD)
    ## The option of the force field file that the task writes, which
    ## later tasks can use, None if it writes none
    forcefield: Optional[str] = None
    ## The workspace of the interface that the task belongs to
    workspace: str = "molecule"
    ## Files that the command writes without an option for them, by the
    ## name under which they can be fetched
    collect: Mapping[str, str] = field(default_factory=dict)
    ## Whether the plots compare two sets of values point by point
    scatter: bool = False
    ## The function that makes what is kept of the result, None for all of it
    digest: Optional[Callable[[Any], Any]] = None
    ## Whether the task can run as a job on the cluster of the user
    cluster: bool = False
    ## Whether the command runs on several processes, with an individual
    ## of the population of train_ff for each
    parallel: bool = False
    ## A job that runs the command more than once, such as a scan, by the
    ## name of the function that does it; None for one run of the command
    runner: Optional[str] = None
    ## Whether the command runs on several processes that share the work,
    ## without a population
    mpi: bool = False
    ## Whether the task runs only as a job on the cluster
    cluster_only: bool = False
    ## Whether the results of a job on the cluster come by themselves when
    ## the job is done: the files that it wrote are put in the working
    ## directory of the user and the page shows its plots
    deliver: bool = False


@dataclass(frozen=True)
class Prepared:
    """!
    @brief What a request becomes: the options of the command and the
    files that the server makes for it.
    """
    ## The keyword arguments for act.run(), without those of the run itself
    options: Dict[str, Any]
    ## Input files to write in the directory of the job, by option name
    inputs: Dict[str, Sequence[InputFile]] = field(default_factory=dict)
    ## The number of processes that the command runs on
    processes: int = 1
    ## The properties that a comparison or a training compares, in the
    ## order in which they were chosen
    observables: Tuple[str, ...] = ()


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
    Task(name="dimer_scan", command="minimize",
         title="Interaction energy as a function of the distance in a dimer",
         options=_CHARGE_OPTIONS,
         progress=True,
         downloads=("xvg",),
         needs=(MOLECULE, FORCEFIELD, SCAN), runner="dimer_scan"),
    Task(name="b2", command="b2",
         title="Second virial coefficient of a dimer",
         options=_CHARGE_OPTIONS + ("T1", "T2", "dT", "maxdimer"),
         fixed={"json": True},
         outputs={"b2": "B2T.xvg", "eh": "mayer.xvg"},
         downloads=("b2", "eh", "g"),
         plots=("b2", "eh"),
         digest=virial.digest, cluster=True, mpi=True, deliver=True),
    Task(name="openmm_run", command="run.py",
         title="Simulation of the molecule with OpenMM",
         downloads=OPENMM_FILES,
         plots=("energy", "temperature"),
         needs=(), cluster=True, cluster_only=True),
    # --- the force field workspace ---
    Task(name="gen_ff", command="gen_ff",
         title="Generate a force field",
         fixed={"json": True},
         outputs={"o": "forcefield.xml"},
         downloads=("o", "f", "vs"),
         needs=(GENERATOR,), forcefield="o", workspace="forcefield"),
    Task(name="geometry_ff", command="geometry_ff",
         title="Bonds, angles and dihedrals from a molprop file",
         options=("gen_bcc", "bfactor", "afactor", "factor"),
         # The histograms, with for each bin where some of its values come from
         fixed={"json": True, "samples": geometry.MAX_SAMPLES},
         # Whether the bond lengths and angles of each type are normally distributed
         optional={"analyse": True},
         outputs={"o": "forcefield.xml"},
         downloads=("o", "g"),
         needs=(FORCEFIELD, MOLPROP, CHOSEN), forcefield="o", workspace="forcefield",
         digest=geometry.digest),
    Task(name="ff_describe", command="edit_ff",
         title="Read and check a force field",
         fixed={"json": True},
         needs=(FORCEFIELD,), workspace="forcefield"),
    Task(name="ff_edit", command="edit_ff",
         title="Change parameters of a force field",
         options=("p", "a", "val", "min", "max", "mut", "nonneg", "force"),
         fixed={"json": True},
         outputs={"o": "forcefield.xml"},
         downloads=("o",),
         needs=(FORCEFIELD,), forcefield="o", workspace="forcefield"),
    Task(name="ff_shells", command="edit_ff",
         title="Fix or release the force constants of the shells",
         options=("fixkshell", "a", "kshell"),
         fixed={"json": True},
         outputs={"o": "forcefield.xml"},
         downloads=("o",),
         needs=(FORCEFIELD,), forcefield="o", workspace="forcefield"),
    Task(name="ff_repair", command="edit_ff",
         title="Write a force field without the problems that can be repaired",
         fixed={"json": True, "validate": True},
         outputs={"o": "forcefield.xml"},
         downloads=("o",),
         needs=(FORCEFIELD,), forcefield="o", workspace="forcefield"),
    # --- the molprop workspace ---
    Task(name="mp_describe", command="edit_mp",
         title="Read and check a molprop file",
         # Without -nomolinfo edit_mp describes the compounds with the IUPAC
         # names of its own table of compounds, which are not those of the
         # file that train_ff and geometry_ff read: a selection with these
         # names would leave such compounds out without a word
         fixed={"json": True, "validate": True, "molinfo": False},
         downloads=("g",),
         needs=(MOLPROP,), workspace="molprop", digest=evaluation.described),
    Task(name="mp_clean", command="edit_mp",
         title="Write a molprop file without calculations that are there twice",
         # As it is validated: the calculations that are there twice with
         # the same results once, as train_ff uses them, and the names of
         # the file
         fixed={"validate": True, "molinfo": False},
         outputs={"o": "cleaned.xml"},
         downloads=("o", "g"),
         needs=(MOLPROP,), workspace="molprop"),
    Task(name="evaluate", command="train_ff",
         title="Compare a force field with the data of a molprop file",
         fixed={"json": True, "optimize": False},
         downloads=("g",) + evaluation.PLOTS,
         plots=evaluation.PLOTS,
         needs=(FORCEFIELD, MOLPROP, SELECTION, OBSERVABLES), workspace="molprop",
         collect=evaluation.DIMER_FILES, scatter=True, digest=evaluation.digest,
         cluster=True),
    Task(name="train", command="train_ff",
         title="Train a force field on the data of a molprop file",
         options=("optimizer", "max_iter", "max_generations", "seed", "random_init",
                  "temp") + training.SETTINGS,
         fixed={"json": True},
         outputs={"o": "trained.xml"},
         progress=True,
         downloads=("o", "g") + evaluation.PLOTS,
         plots=evaluation.PLOTS,
         needs=(FORCEFIELD, MOLPROP, SELECTION, OBSERVABLES, TRAINING), forcefield="o",
         workspace="molprop", collect=evaluation.DIMER_FILES, scatter=True,
         digest=training.digest, cluster=True, parallel=True),
)}

## The values that the option mut of edit_ff takes
_MUTABILITIES = ("Fixed", "Bounded")


def _identifiers(value: Any) -> str:
    """!
    @brief Check the particle types or interactions of which a parameter is
    changed.
    @param value One identifier or a list of them.
    @return The identifiers as edit_ff takes them, with spaces in between.
    @throws RequestError if one does not look like an identifier.
    """
    names: List[Any] = list(value) if isinstance(value, (list, tuple)) else [value]
    if not names or len(names) > MAX_IDENTIFIERS:
        raise RequestError("Give between 1 and %d identifiers" % MAX_IDENTIFIERS)
    for name in names:
        if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name):
            raise RequestError("'%s' is not the identifier of a particle type or an "
                               "interaction" % (name,))
    return " ".join(names)


def _edit_options(given: Dict[str, Any]) -> Dict[str, Any]:
    """!
    @brief Check the options of a change of a force field.
    @param given The options of the request that the task lists.
    @return The options for edit_ff.
    @throws RequestError if the change is not clear or not accepted.
    """
    options = dict(given)
    name = options.get("p")
    if not isinstance(name, str) or not _PARAMETER_NAME.fullmatch(name):
        raise RequestError("Give the name of the parameter to change")
    if "a" not in options:
        # Without it edit_ff changes the parameter for every type
        raise RequestError("Give the particle type or interaction to change")
    options["a"] = _identifiers(options["a"])
    for number in ("val", "min", "max"):
        value = options.get(number)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or value != value or abs(value) == float("inf")):
            raise RequestError("Option '%s' must be a number" % number)
    if "mut" in options and options["mut"] not in _MUTABILITIES:
        raise RequestError("The mutability must be %s" % " or ".join(_MUTABILITIES))
    if "nonneg" in options and options["nonneg"] not in ("yes", "no"):
        raise RequestError("Option 'nonneg' must be yes or no")
    if "force" in options and not isinstance(options["force"], bool):
        raise RequestError("Option 'force' must be true or false")
    if not any(key in options for key in ("val", "min", "max", "mut", "nonneg")):
        raise RequestError("Give what to change: the value, a bound or the mutability")
    if "mut" in options:
        # edit_ff leaves a parameter that training does not change as it is
        # unless it is forced, also when that is what is to be changed: from
        # Fixed to Bounded it would do nothing
        options.setdefault("force", True)
    return options


## What can be done with the force constants of the shells, see the option
## -fixkshell of edit_ff: fix them with alpha or the charge following, or
## let them follow from both again
_SHELL_ACTIONS = ("alpha", "charge", "release")

## The force constants of shells that can be given, in kJ/mol nm2
_KSHELL_RANGE = ffgen.KSHELL_RANGE


def _shell_options(given: Dict[str, Any]) -> Dict[str, Any]:
    """!
    @brief Check the options of a change of the force constants of the shells.
    @param given The options of the request that the task lists: fixkshell,
    a with the shell types (all if not given) and kshell, the force
    constant (the current one if not given).
    @return The options for edit_ff.
    @throws RequestError if the change is not clear or not accepted.
    """
    options = dict(given)
    if options.get("fixkshell") not in _SHELL_ACTIONS:
        raise RequestError("Give what to do with the force constants of the shells: %s"
                           % ", ".join(_SHELL_ACTIONS))
    if "a" in options:
        options["a"] = _identifiers(options["a"])
    if "kshell" in options:
        value = options["kshell"]
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or value != value
                or not _KSHELL_RANGE[0] <= value <= _KSHELL_RANGE[1]):
            raise RequestError("The force constant must be a number between %g and %g kJ/mol nm2"
                               % _KSHELL_RANGE)
        if options["fixkshell"] == "release":
            raise RequestError("A force constant is only used if it is fixed")
    return options


def _forcefield(reference: Any, settings: Settings, uploads: UploadStore,
                written: Optional[Callable[[str], Optional[Path]]],
                default: bool) -> Path:
    """!
    @brief Find the force field that a request names.
    @param reference The object "forcefield" of the request: "file" with
    the identifier of an upload, "job" with the identifier of a job that
    wrote a force field, "library" with the identifier of an installed
    force field (see library), "default" that is true for the default force
    field, or none of these where the default may be used without asking.
    @param settings  The settings, for the default force field and the library.
    @param uploads   The uploaded files.
    @param written   The function that gives the force field a job wrote.
    @param default   Whether the default force field may be used.
    @return The path of the file.
    @throws RequestError if there is no such force field.
    """
    reference = reference or {}
    if not isinstance(reference, Mapping):
        raise RequestError("The force field must be an object")
    file_id = reference.get("file")
    job_id = reference.get("job")
    library_id = reference.get("library")
    if sum(x is not None for x in (file_id, job_id, library_id)) > 1:
        raise RequestError("Give one force field, not two")
    if library_id is not None:
        found = library.find(settings.forcefield_dirs, library_id) if isinstance(library_id, str) else None
        if found is None:
            raise RequestError("The force field '%s' is not installed" % library_id)
        return found.path
    if file_id is not None:
        upload = uploads.get(file_id)
        if upload is None or upload.kind != "forcefield":
            raise RequestError("The force field file is not known, upload it again")
        return uploads.path(upload)
    if job_id is not None:
        path = written(job_id) if written is not None and isinstance(job_id, str) else None
        if path is None:
            raise RequestError("That force field is not there any more, make it again")
        return path
    if not default and reference.get("default") is not True:
        raise RequestError("The request has no force field")
    if settings.default_forcefield is None:
        raise RequestError("There is no default force field: choose one, or install the "
                           "force fields of ACT (the conda package forcefields)")
    return settings.default_forcefield


def build_request(task: Task, request: Mapping[str, Any], settings: Settings,
                  uploads: UploadStore,
                  written: Optional[Callable[[str], Optional[Path]]] = None,
                  described: Optional[Callable[[str], Optional[Any]]] = None,
                  processes: Optional[int] = None,
                  pools: Optional[Callable[[str], Optional[Path]]] = None) -> Prepared:
    """!
    @brief Turn a request from the browser into the options of a command.
    @param task     The task.
    @param request  The request. What it must have depends on the task:
    "molecule" with either "smiles" or "file", "forcefield" with "file" or
    "job", "molprop" with "file" and, for a selection of its compounds,
    "described" with the job that described the file and "sets" with the
    compounds that are for testing or not used, "generator" with the
    choices for a new force field, "observables" with the properties to
    compare and "weights" with how much they count, "training" with the kinds of parameters to train ("fit") and
    the part of the compounds for testing ("test"), "processes" for a task
    that runs on several, and "options".
    @param settings The settings, for the limits and the default force field.
    @param uploads  The uploaded files.
    @param written  The function that gives the force field that a job
    wrote, by the identifier of the job, or None if there is none.
    @param described The function that gives the compounds of a molprop
    file as a job described them, by the identifier of the job.
    @param processes The number of processes that the command will run on,
    when the caller decides that, as for a job on a cluster. None to take
    it from the request, within the limit of the settings.
    @param pools    The function that gives the gene pool that a training
    wrote, by the identifier of the job, or None if there is none.
    @return The keyword arguments for act.run(), without the arguments of
    the run itself, and the input files that the server makes. The types of
    the values are checked later by act.
    @throws RequestError if the request is not accepted.
    """
    if not isinstance(request, Mapping):
        raise RequestError("The request must be an object")
    options: Dict[str, Any] = {}
    inputs: Dict[str, Sequence[InputFile]] = {}

    if MOLECULE in task.needs:
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

    if FORCEFIELD in task.needs:
        # An uploaded file, one that a job wrote or, for a calculation on a
        # molecule, the default
        options["ff"] = _forcefield(request.get("forcefield"), settings, uploads, written,
                                    default=MOLECULE in task.needs)

    if MOLPROP in task.needs:
        molprop = request.get("molprop")
        if not isinstance(molprop, Mapping):
            raise RequestError("The request has no molprop file")
        upload = uploads.get(molprop.get("file"))
        if upload is None or upload.kind != "molprop":
            raise RequestError("The molprop file is not known, upload it again")
        options["mp"] = [uploads.path(upload)]
        if SELECTION in task.needs or (CHOSEN in task.needs
                                       and molprop.get("sets") is not None):
            # All compounds of the file, by the names that edit_mp read,
            # or those that the user chose
            job_id = molprop.get("described")
            molecules = described(job_id) if described is not None and isinstance(job_id, str) \
                else None
            if molecules is None:
                raise RequestError("The molprop file has to be read before it is used, "
                                   "open it again")
            try:
                test = training.test_fraction(request.get("training")) \
                    if TRAINING in task.needs else 0.0
                mine = molprop.get("selection")
                if mine is not None and SELECTION in task.needs:
                    # A selection file of the user, as it is
                    if molprop.get("sets") is not None or test > 0:
                        raise RequestError("The selection file says which compounds are used "
                                           "and which are for testing; give no other choice")
                    text = evaluation.user_selection(molecules, mine)
                else:
                    text = evaluation.selection(molecules, test, molprop.get("sets"))
                inputs["sel"] = [InputFile("selection.dat", text)]
            except (evaluation.EvaluationError, training.TrainingError) as exc:
                raise RequestError(str(exc)) from exc

    if SCAN in task.needs:
        try:
            options["scan"] = dimerscan.settings(request.get("scan"))
        except dimerscan.DimerScanError as exc:
            raise RequestError(str(exc)) from exc

    observables: Tuple[str, ...] = ()
    if OBSERVABLES in task.needs:
        try:
            options.update(evaluation.options(request.get("observables"),
                                              request.get("weights")))
        except evaluation.EvaluationError as exc:
            raise RequestError(str(exc)) from exc
        observables = tuple(request["observables"])

    if TRAINING in task.needs:
        try:
            options.update(training.options(request.get("training")))
        except training.TrainingError as exc:
            raise RequestError(str(exc)) from exc

    if GENERATOR in task.needs:
        # The choices become the files and the options of gen_ff
        try:
            atom_types = ffgen.read_atom_types()
            made = ffgen.build(ffgen.parse_choices(request.get("generator"), atom_types),
                               atom_types)
        except ffgen.GeneratorError as exc:
            raise RequestError(str(exc)) from exc
        options.update(made.options)
        inputs["f"] = [InputFile("atomtypes.csv", made.atomtypes)]
        if made.vsites is not None:
            inputs["vs"] = [InputFile("vsites.json", made.vsites)]

    # Only the options of the task, within the limits
    given = request.get("options") or {}
    if not isinstance(given, Mapping):
        raise RequestError("The options must be an object")
    limits = settings.limits.get(task.name, {})
    chosen: Dict[str, Any] = {}
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
        chosen[name] = value
    if task.name == "ff_edit":
        chosen = _edit_options(chosen)
    elif task.name == "ff_shells":
        chosen = _shell_options(chosen)
    number = 1
    if task.parallel:
        # The processes, the individuals, and an optimizer that can do with them
        chosen.setdefault("optimizer", training.DEFAULT_OPTIMIZER)
        try:
            if processes is None:
                number = training.processes(request.get("processes"), chosen["optimizer"],
                                            settings.max_processes)
            else:
                number = training.processes(processes, chosen["optimizer"], processes)
            individuals = training.population(
                request.get("training") if TRAINING in task.needs else None,
                chosen["optimizer"], number)
            training.settings(chosen, chosen["optimizer"])
        except training.TrainingError as exc:
            raise RequestError(str(exc)) from exc
        chosen["pop_size"] = individuals
        if TRAINING in task.needs:
            # The population of an earlier training, for the same number of individuals
            try:
                earlier = training.continued(request.get("training"))
                if earlier is not None:
                    options.update(training.gene_pool(
                        chosen["optimizer"], individuals, pools(earlier) if pools else None))
            except training.TrainingError as exc:
                raise RequestError(str(exc)) from exc
    elif task.mpi:
        # Processes that share the work, as many as asked for, or as the job on the cluster has
        try:
            number = training.processes(request.get("processes") if processes is None else processes,
                                        training.DEFAULT_OPTIMIZER,
                                        settings.max_processes if processes is None else processes)
        except training.TrainingError as exc:
            raise RequestError(str(exc)) from exc
    elif request.get("processes") not in (None, 1):
        raise RequestError("%s runs on one process" % task.title)
    options.update(chosen)
    options.update(task.fixed)
    return Prepared(options=options, inputs=inputs, processes=number, observables=observables)


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


def program_lacks(task: Task, program: Any) -> Optional[str]:
    """!
    @brief Whether the alexandria program can do a task at all.

    The interface gives some options with every run of a task, such as the
    one for results in JSON format. A program that does not have them is
    older than the interface, and the user should hear that instead of the
    name of an option that was never asked for.
    @param task    The task.
    @param program The description of the program from act.program_info().
    @return What the program lacks, as a message for the user, or None if
    it has the command and the options that the task always uses.
    """
    advice = (" The program is older than this interface: install a newer version of ACT.")
    command = program.commands.get(task.command)
    if command is None:
        return ("The alexandria program does not have the command %s, which is needed to: %s.%s"
                % (task.command, task.title[:1].lower() + task.title[1:], advice))
    missing = [name for name in list(task.fixed) + list(task.outputs)
               if not command.has_option(name)]
    if missing:
        return ("The alexandria program does not have the option%s %s of %s, which this "
                "interface needs.%s"
                % ("" if len(missing) == 1 else "s",
                   " and ".join("-" + name for name in missing), task.command, advice))
    return None


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
            "needs": list(task.needs), "workspace": task.workspace,
            "cluster": task.cluster, "parallel": task.parallel, "options": described}
