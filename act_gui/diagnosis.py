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
@brief Why a job on the cluster failed, and what to change: read from the
output of Slurm and the log files of the job that were fetched.

The output of a job that failed is long: the banner of ACT, the same
message from each MPI process, a block of mpirun about slots. The line
that tells what went wrong is somewhere in between, and sometimes only in
the log file. The rules below look for the lines that tell, from the most
specific to the least: something of the cluster (a program, module or
environment that is not there, too few cores for mpirun, the time limit,
memory, the GPU), then what the program said (Fatal, an error block, an
exception), then a crash. Compounds that got no topology are told as well,
since they are often why a training set is empty.

What comes out is for the user: one sentence why, one sentence what to
change, the kind of fix, and the lines that tell, each with the file and
the line where it was found.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

## The kinds of fix: the settings of the cluster, the processes of the job,
## its time, its memory, the GPU, the input of the calculation, a report
## of an error of the program, or nothing in particular
FIXES = ("settings", "processes", "time", "memory", "gpu", "input", "report", "")
## The largest part of a file that is read; of a larger file the start and
## the end are read
MAX_READ = 16 * 1024 ** 2
## The largest number of lines that are shown
MAX_LINES = 12
## The largest number of names in a sentence
MAX_NAMES = 6
## The longest line that is kept
MAX_LINE = 300
## The files of a job that are read, besides the output of Slurm: those
## with these endings
TEXT_ENDINGS = (".log", ".out", ".txt", ".err")

## A line of a file: the file, the number of the line (None if not known),
## the text
Line = Tuple[str, Optional[int], str]


def read_text(path: Path) -> Tuple[str, int]:
    """!
    @brief Read a file of a job, or its start and its end if it is large.
    @param path The file.
    @return The text, and the number of the first line of the end part if
    only the start and the end were read (0 if all was read).
    @throws OSError if the file cannot be read.
    """
    size = path.stat().st_size
    with open(path, "rb") as handle:
        if size <= MAX_READ:
            return handle.read().decode("utf-8", errors="replace"), 0
        head = handle.read(MAX_READ // 2)
        handle.seek(size - MAX_READ // 2)
        tail = handle.read()
    # The lines of the end are not counted; they get no number
    return (head.decode("utf-8", errors="replace") + "\n"
            + tail.decode("utf-8", errors="replace")), head.count(b"\n") + 2


def _lines(texts: Mapping[str, Any]) -> List[Line]:
    """!
    @brief The lines of the files.
    @param texts For each file its text, or its text and the number of the
    first line without a number, see read_text().
    @return The lines in the order of the files.
    """
    found: List[Line] = []
    for name, value in texts.items():
        text, unnumbered = (value, 0) if isinstance(value, str) else value
        for index, line in enumerate(text.splitlines(), start=1):
            stripped = line.rstrip()
            if stripped:
                found.append((name, None if unnumbered and index >= unnumbered else index,
                              stripped[:MAX_LINE]))
    return found


def _names(names: Sequence[str]) -> str:
    """!
    @brief A list of names in a sentence, the first few of a long one.
    @param names The names, without doubles.
    @return The names separated by commas.
    """
    shown = ", ".join(names[:MAX_NAMES])
    return shown + (", …" if len(names) > MAX_NAMES else "")


class _Found:
    """!
    @brief What a rule found: why, what to change, the kind of fix, the
    lines that tell, and what the job could ask for instead.
    """

    def __init__(self, reason: str, hint: str, fix: str, lines: Sequence[Line] = (),
                 suggest: Optional[Dict[str, Any]] = None):
        """!
        @brief Note what was found.
        @param reason  Why the job failed.
        @param hint    What to change.
        @param fix     The kind of fix, one of FIXES.
        @param lines   The lines that tell.
        @param suggest What the job could ask for instead.
        """
        self.reason = reason
        self.hint = hint
        self.fix = fix
        self.lines = list(lines)
        self.suggest = suggest or {}


def _first(lines: Sequence[Line], pattern: "re.Pattern[str]") -> Tuple[Optional[Line], Any]:
    """!
    @brief The first line that matches.
    @param lines   The lines.
    @param pattern The pattern.
    @return The line and the match, None and None if no line matches.
    """
    for line in lines:
        match = pattern.search(line[2])
        if match:
            return line, match
    return None, None


def _all(lines: Sequence[Line], pattern: "re.Pattern[str]") -> List[Tuple[Line, Any]]:
    """!
    @brief All lines that match.
    @param lines   The lines.
    @param pattern The pattern.
    @return The lines with their match.
    """
    return [(line, match) for line in lines for match in [pattern.search(line[2])] if match]


## A program that the shell did not find, or a file that the script reads:
## what bash, sh and srun say about a line of the script
_NOT_FOUND = re.compile(r"(?:line \d+: |execve\(\): )([^:\s]+): (?:command not found|"
                        r"No such file or directory)$|^([^:\s]+): command not found$")
## A module that is not there, as Lmod and Environment Modules say it
_MODULE = re.compile(r"module\(s\) are unknown:\s*\"?([^\"]+)\"?|Unable to locate a modulefile "
                     r"for '([^']+)'|module: command not found")
## A conda environment that is not there
_CONDA = re.compile(r"Could not find conda environment:\s*(\S+)|"
                    r"EnvironmentNameNotFound(?!:\s*Could)|"
                    r"EnvironmentLocationNotFound:.*?(\S+)$")
## mpirun that finds fewer cores than processes
_SLOTS = re.compile(r"not enough slots available in the system to satisfy the\s+(\d+)")
## Slurm that stops a job
_TIME = re.compile(r"CANCELLED AT \S+ DUE TO TIME LIMIT")
_CANCELLED = re.compile(r"CANCELLED AT \S+")
## Memory that ran out
_MEMORY = re.compile(r"oom[-_ ]kill|Out Of Memory|Exceeded (?:job|step) memory limit|"
                     r"std::bad_alloc|Cannot allocate memory|out of memory", re.IGNORECASE)
## A GPU that is not there or failed
_GPU = re.compile(r"no CUDA-capable device|CUDA_ERROR_NO_DEVICE|cudaErrorNoDevice|"
                  r"hipErrorNoDevice|CUDA error|HIP error|"
                  r"Invalid generic resource \(gres\)", re.IGNORECASE)
## What ACT says when it stops
_FATAL = re.compile(r"^Fatal:\s*(.+)$")
_BLOCK_HEADERS = re.compile(r"^(Error in user input|Fatal error|Invalid input value|"
                            r"Software inconsistency error|File input/output error|"
                            r"Inconsistency in user input|Internal error|Not implemented|"
                            r"Range checking error|Invalid value|Error):?\s*$")
_EXCEPTION = re.compile(r"what\(\):\s*(.+)$")
_PYTHON = re.compile(r"^([A-Za-z_.]*(?:Error|Exception)):\s*(.*)$")
## A crash
_CRASH = re.compile(r"Segmentation fault|signal 11|SIGSEGV|Bus error|core dumped|"
                    r"signal 6|SIGABRT|Floating point exception|Illegal instruction")
_MPI_ABORT = re.compile(r"MPI_ABORT was invoked on rank (\d+)|calling\s+\"abort\"")
## Compounds that got no topology, and why
_TOPOLOGY = re.compile(r"Cannot make topology for (\S+)")
_PARAMETER = re.compile(r"Missing parameter in force field:\s*(\S+)\s+(\S+)")
_ATOMTYPE = re.compile(r"Cannot find atomtype (\S+)")
## Lines that tell nothing, at the end of an output
_NOISE = re.compile(r"^-+$|Thanks for using|https?://|Please check (?:the )?output in|"
                    r"^\s*$|Program finished")


def _setup(lines: Sequence[Line], program: str) -> Optional[_Found]:
    """!
    @brief A program, module or conda environment of the job that was not found.
    @param lines The lines of the files.
    @param program The name of the program of the settings.
    @return What was found, None if nothing.
    """
    line, match = _first(lines, _MODULE)
    if line:
        name = (match.group(1) or match.group(2) or "").strip() if match.lastindex else ""
        if "command not found" in line[2]:
            return _Found("The cluster has no command module in a job.",
                          "Choose another way to make ACT available in the settings of the "
                          "cluster (Settings): a conda environment or a file to read.",
                          "settings", [line])
        return _Found("The module %s is not known on the cluster." % (name or "of the settings"),
                      "Give the name of the module as “module avail” lists it on the cluster, "
                      "in the settings of the cluster (Settings).", "settings", [line])
    line, match = _first(lines, _CONDA)
    if line:
        name = next((group for group in match.groups() if group), "")
        return _Found("The conda environment %s is not there on the cluster."
                      % (name or "of the settings"),
                      "Give the name of an environment that “conda env list” shows on the "
                      "cluster, in the settings of the cluster (Settings).", "settings", [line])
    line, match = _first(lines, _NOT_FOUND)
    if line:
        name = match.group(1) or match.group(2)
        if Path(name).name in ("conda", "mamba", "micromamba"):
            hint = ("Conda is not known in a job: make ACT available with a module or a file "
                    "to read instead, in the settings of the cluster (Settings).")
        elif Path(name).name in ("mpirun", "srun"):
            hint = ("Set how the processes are started (mpirun or srun) in the settings of the "
                    "cluster (Settings), or make MPI available with ACT.")
        elif name in (program, "alexandria", "alexandria_d", "python3"):
            hint = ("Set how ACT is made available in a job (a module, a conda environment or "
                    "a file to read) and the name of the program, in the settings of the "
                    "cluster (Settings).")
        else:
            hint = ("Check the file or program in the settings of the cluster (Settings): "
                    "how ACT is made available in a job.")
        return _Found("%s was not found on the cluster." % name, hint, "settings", [line])
    return None


def _cluster(lines: Sequence[Line], tasks: int, minutes: int,
             most_minutes: int) -> Optional[_Found]:
    """!
    @brief Too few cores for mpirun, the time limit, memory or the GPU.
    @param lines The lines of the files.
    @param tasks        The number of processes of the job.
    @param minutes      The time limit of the job.
    @param most_minutes The longest time that a job can ask for.
    @return What was found, None if nothing.
    """
    line, match = _first(lines, _SLOTS)
    if line:
        return _Found("mpirun could not start the %s processes: it found fewer cores on the "
                      "nodes that Slurm gave the job." % match.group(1),
                      "Ask for fewer processes, or start the processes with srun instead of "
                      "mpirun in the settings of the cluster (Settings).", "processes", [line])
    line, _ = _first(lines, _TIME)
    if line:
        more = min(2 * minutes, most_minutes)
        return _Found("Slurm stopped the job when the %d minutes it asked for were up." % minutes,
                      "Send it again with more time." if more > minutes else
                      "The job asked for the longest time already: give it less to do.",
                      "time", [line], {"minutes": more} if more > minutes else None)
    line, _ = _first(lines, _MEMORY)
    if line and not _GPU.search(line[2]):
        return _Found("The job ran out of memory.",
                      "Use a partition with more memory per core, or fewer processes on a "
                      "node%s." % ("" if tasks > 1 else ", or a smaller calculation"),
                      "memory", [line])
    line, _ = _first(lines, _GPU)
    if line:
        return _Found("The job found no GPU, or the GPU failed.",
                      "Check the partition with GPUs and the GPU resource (gres) in the "
                      "settings of the cluster (Settings), or send the job without a GPU.",
                      "gpu", [line], {"gpu": False})
    return None


def _blocks(lines: Sequence[Line]) -> List[Tuple[Line, str, str]]:
    """!
    @brief The messages of the error blocks of ACT: a header such as
    “Error in user input:” and the lines after it.
    @param lines The lines.
    @return Each block with the first line of its message (the header if
    it has none), its header and its message; the message is empty for one
    that says to look above.
    """
    found = []
    for index, line in enumerate(lines):
        header = _BLOCK_HEADERS.match(line[2])
        if not header or index == 0 or not any(
                "Program:" in before[2] for before in lines[max(0, index - 6):index]):
            continue
        message = []
        for after in lines[index + 1:index + 6]:
            if after[2].startswith("For more information") or after[2].startswith("----"):
                break
            message.append(after)
        text = " ".join(after[2].strip() for after in message)
        found.append((message[0] if message else line, header.group(1),
                      "" if text.startswith("See message above") else text))
    return found


def _program(lines: Sequence[Line], tasks: int) -> Optional[_Found]:
    """!
    @brief What the program said when it stopped: a Fatal line, an error
    block, an exception of C++ or of Python.
    @param lines The lines of the files.
    @param tasks The number of processes of the job.
    @return What was found, None if nothing.
    """
    fatal = _all(lines, _FATAL)
    blocks = _blocks(lines)
    if fatal or any(message for _, _, message in blocks):
        message = fatal[0][1].group(1) if fatal else next(m for _, _, m in blocks if m)
        marked = [line for line, _ in fatal] or [line for line, _, m in blocks if m]
        if "Software inconsistency" in " ".join(header for _, header, _ in blocks):
            return _Found("alexandria stopped with an error of the program: %s" % message,
                          "This is an error of ACT. Please report it with the output of the "
                          "job (slurm.out) and its script.", "report", marked)
        if "Training set is empty" in message and tasks > 1:
            return _Found("alexandria stopped: %s" % message,
                          "Each of the %d processes needs compounds to train on. Use fewer "
                          "processes, or more compounds; see below why compounds were left out, "
                          "if any were." % tasks, "processes", marked)
        return _Found("alexandria stopped: %s" % message,
                      "Change what the message says in the calculation and send it as a new "
                      "job from its tab.", "input", marked)
    line, match = _first(lines, _EXCEPTION)
    if line:
        return _Found("alexandria stopped with an exception: %s" % match.group(1),
                      "If the input is right, this is an error of ACT. Please report it with "
                      "the output of the job (slurm.out).", "report", [line])
    traceback, _ = _first(lines, re.compile(r"^Traceback \(most recent call last\)"))
    if traceback:
        start = lines.index(traceback)
        errors = _all(lines[start:], _PYTHON)
        if errors:
            line, match = errors[0]
            if match.group(1).endswith("ModuleNotFoundError"):
                return _Found("The script stopped: %s" % match.group(2),
                              "Install the module in the environment of the jobs, or make "
                              "one available that has it, in the settings of the cluster "
                              "(Settings).", "settings", [line])
            return _Found("The script stopped with %s: %s" % (match.group(1), match.group(2)),
                          "Change the settings of the calculation, or report it with the "
                          "output of the job (slurm.out).", "input", [line])
    return None


def _crash(lines: Sequence[Line]) -> Optional[_Found]:
    """!
    @brief A program that crashed.
    @param lines The lines of the files.
    @return What was found, None if nothing.
    """
    line, _ = _first(lines, _CRASH)
    if line:
        return _Found("The program crashed (%s)." % line[2].strip()[:120],
                      "This is an error of the program. Please report it with the output of "
                      "the job (slurm.out) and its script.", "report", [line])
    return None


def _topology(lines: Sequence[Line]) -> Optional[_Found]:
    """!
    @brief Compounds that got no topology with the force field, and what
    the force field lacks for them.
    @param lines The lines of the files.
    @return What was found, None if nothing.
    """
    compounds: List[str] = []
    marked: List[Line] = []
    for line, match in _all(lines, _TOPOLOGY):
        if match.group(1) not in compounds:
            compounds.append(match.group(1))
            # The sentence names them all; a few lines show where
            if len(compounds) <= 2:
                marked.append(line)
    if not compounds:
        return None
    types: List[str] = []
    for line, match in _all(lines, _ATOMTYPE):
        if match.group(1) not in types:
            types.append(match.group(1))
            if len(types) <= 3:
                marked.append(line)
    parameters: List[str] = []
    for line, match in _all(lines, _PARAMETER):
        name = "%s %s" % (match.group(1), match.group(2))
        if name not in parameters:
            parameters.append(name)
            if len(parameters) <= 3:
                marked.append(line)
    lacks = []
    if types:
        lacks.append("the atom type%s %s" % ("s" if len(types) > 1 else "", _names(types)))
    if parameters:
        lacks.append("parameters for %d interaction%s such as %s"
                     % (len(parameters), "s" if len(parameters) > 1 else "",
                        _names(parameters[:2])))
    reason = "%d compound%s got no topology with this force field: %s." % (
        len(compounds), "s" if len(compounds) > 1 else "", _names(compounds))
    if lacks:
        reason += " The force field lacks %s." % " and ".join(lacks)
    return _Found(reason, "Add what the force field lacks (tab Force field), or leave these "
                  "compounds out of the selection.", "input", marked)


def _cancelled(lines: Sequence[Line]) -> Optional[_Found]:
    """!
    @brief A job that was stopped on the cluster, not by its time limit.
    @param lines The lines of the files.
    @return What was found, None if nothing.
    """
    line, _ = _first(lines, _CANCELLED)
    if line:
        return _Found("The job was stopped on the cluster (scancel, or by the administrators).",
                      "Send it again if that was not meant.", "", [line])
    return None


def _ending(lines: Sequence[Line], count: int = 6) -> List[Line]:
    """!
    @brief The last lines of the output of Slurm that tell something.
    @param lines The lines.
    @param count The number of lines.
    @return The lines.
    """
    first = lines[0][0] if lines else ""
    told = [line for line in lines if line[0] == first and not _NOISE.search(line[2])]
    return told[-count:]


def _unique(lines: Sequence[Line]) -> List[Dict[str, Any]]:
    """!
    @brief The lines for the user: each text once, with the number of times
    it was there.
    @param lines The lines.
    @return For each line the file, the number of the line, the text and the count.
    """
    shown: Dict[str, Dict[str, Any]] = {}
    for name, number, text in lines:
        if text in shown:
            continue
        shown[text] = {"file": name, "line": number, "text": text, "count": 0}
        if len(shown) >= MAX_LINES:
            break
    return list(shown.values())


def diagnose(texts: Mapping[str, Any], *, state: str, exit_status: Optional[int],
             tasks: int = 1, minutes: int = 60, most_minutes: int = 30 * 1440,
             program: str = "alexandria") -> Dict[str, Any]:
    """!
    @brief Why a job failed, and what to change.
    @param texts        The files that were read: for each name its text, or
    what read_text() gave; the output of Slurm first.
    @param state        failed (the program ended with an exit status that is
    not zero) or lost (the job ended without one).
    @param exit_status  The exit status of the program, None if there is none.
    @param tasks        The number of processes of the job.
    @param minutes      The time limit of the job.
    @param most_minutes The longest time that a job can ask for.
    @param program      The name of the program of the settings.
    @return An object with "reason" and "hint", sentences for the user;
    "fix", one of FIXES; "suggest", what the job could ask for instead
    (minutes, gpu); "details", more sentences, such as the compounds that
    got no topology; "lines", the lines that tell, each with its "file",
    "line" (None if not known), "text" and "count", the number of times
    it is there; and "files", the names of the files that were read.
    """
    lines = _lines(texts)
    # The most specific first: the cluster before the program, the
    # program before a crash, which often follows from it
    found = (_setup(lines, program) or _cluster(lines, tasks, minutes, most_minutes)
             or _program(lines, tasks) or _crash(lines))
    topology = _topology(lines)
    details: List[str] = []
    if found is None and topology is not None:
        found, topology = topology, None
    if found is None:
        found = _cancelled(lines) if state == "lost" else None
    if found is None:
        line, match = _first(lines, _MPI_ABORT)
        ending = _ending(lines)
        if state == "lost":
            found = _Found("The job ended without a result: it was stopped, or it took longer "
                           "than the %d minutes it asked for." % minutes,
                           "Look at the output of the job; send it again with more time if it "
                           "ran out of time.", "time", ending,
                           {"minutes": min(2 * minutes, most_minutes)}
                           if minutes < most_minutes else None)
        else:
            found = _Found("The program ended with status %s%s." % (
                exit_status, " after one process stopped all (MPI_ABORT)" if line else ""),
                "The lines below are the last ones of the output; the files of the job "
                "may tell more.", "", ([line] if line else []) + ending)
    if topology is not None:
        details.append(topology.reason)
        if found.fix == "input":
            # The program stopped since compounds were left out
            found.hint = topology.hint
    counts: Dict[str, int] = {}
    for _, _, text in lines:
        counts[text] = counts.get(text, 0) + 1
    shown = _unique(found.lines + (topology.lines if topology is not None else []))
    for line in shown:
        line["count"] = counts.get(line["text"], 1)
    return {"reason": found.reason, "hint": found.hint, "fix": found.fix,
            "suggest": found.suggest, "details": details, "lines": shown,
            "files": list(texts)}


def submit_hint(said: str) -> str:
    """!
    @brief What to change when sbatch did not accept a job.
    @param said What sbatch said.
    @return A sentence for the user, empty if nothing is known.
    """
    rules: Sequence[Tuple[str, str]] = (
        (r"invalid partition", "The partition is not one of the cluster: “sinfo” lists them. "
         "Change it in the settings of the cluster (Settings)."),
        (r"invalid account|account.*(?:not|invalid)|Invalid qos",
         "Give an account that you may use, or none, in the settings of the cluster (Settings)."),
        (r"time limit|MaxWallDuration|PartitionTimeLimit",
         "Ask for less time: the partition allows less."),
        (r"node configuration is not available|More processors requested|"
         r"Requested node configuration|ntasks",
         "Ask for fewer processes, or use another partition."),
        (r"gres|generic resource", "Check the GPU resource (gres) and the partition with GPUs "
         "in the settings of the cluster (Settings)."),
        (r"Disk quota exceeded|No space left", "There is no room in the working directory on "
         "the cluster: remove old jobs there."),
        (r"sbatch: command not found", "The server has no Slurm, or not in a login without a "
         "terminal: check the server in the settings of the cluster (Settings)."),
    )
    for pattern, hint in rules:
        if re.search(pattern, said, re.IGNORECASE):
            return hint
    return ""

