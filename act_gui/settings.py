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
@brief The settings of the server.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import library

## The directory with the files that come with the package
PACKAGE_DIR = Path(__file__).resolve().parent


## The installed force field that is used when the user gives none, see
## library. A test force field of ACT until the new default force field
## exists; changing the default means changing this one place.
DEFAULT_LIBRARY_ID = "Alexandria/ACS-pg1"


def find_default_forcefield(dirs: List[Path]) -> Optional[Path]:
    """!
    @brief The force field that is used when the user gives none.
    @param dirs The directories of the installed force fields.
    @return The file named by the environment variable ACT_GUI_FORCEFIELD if
    it is set, otherwise the installed force field DEFAULT_LIBRARY_ID, None
    if that is not installed.
    """
    env = os.environ.get("ACT_GUI_FORCEFIELD")
    if env:
        return Path(env)
    found = library.find(dirs, DEFAULT_LIBRARY_ID)
    return found.path if found is not None else None


@dataclass
class Settings:
    """!
    @brief Everything that can be configured.

    The limits protect the machine: an interface that anybody can reach
    must not accept files or calculations of any size.
    """
    ## Where uploads and the directories of the jobs are kept
    data_dir: Path
    ## desktop: one user on the local machine. server: several users.
    mode: str = "desktop"
    ## The secret that the browser must present, made at start-up
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    ## The force field that is used when the user gives none, see
    ## find_default_forcefield. None if there is none.
    default_forcefield: Optional[Path] = None
    ## What the interface calls the default force field
    default_forcefield_label: str = "ACS-pg1 (test force field, polarizable)"
    ## The directories with the force fields that can be chosen, see
    ## library.default_dirs. None for those.
    forcefield_dirs: Optional[List[Path]] = None
    ## The largest file that can be uploaded, in bytes
    max_upload_bytes: int = 5 * 1024 * 1024
    ## The largest molprop file that can be uploaded, in bytes
    max_molprop_bytes: int = 200 * 1024 * 1024
    ## The longest SMILES string, the same as alexandria accepts
    max_smiles_length: int = 2000
    ## Seconds after which a calculation is stopped
    job_timeout: float = 600.0
    ## The number of calculations that run at the same time
    max_workers: int = 2
    ## The number of calculations that can wait or run before new ones are refused
    max_active_jobs: int = 8
    ## The file with the settings of the user that are kept between runs,
    ## such as the cluster for long jobs. None to keep them in memory only.
    config_file: Optional[Path] = None
    ## The ssh program with which the interface logs in on a cluster
    ssh_program: str = "ssh"
    ## Where the jobs on the cluster are noted and their results are kept,
    ## between runs. None for a directory below the data directory.
    cluster_jobs_dir: Optional[Path] = None
    ## The working directory of the user: where the files of a job on the
    ## cluster are put when it is done, for the tasks that say so, each job
    ## in a directory of its own. None to put them nowhere.
    work_dir: Optional[Path] = None
    ## The number of progress reports of a job that are kept for plotting
    max_progress_points: int = 5000
    ## The largest number of processes of a calculation on this machine
    max_processes: int = field(default_factory=lambda: max(1, min(8, os.cpu_count() or 1)))
    ## The smallest and largest value of options with a number, by task and option
    limits: Dict[str, Dict[str, Tuple[float, float]]] = field(default_factory=lambda: {
        "simulate": {"nsteps": (1, 200000), "nstener": (1, 100000),
                     "nstxout": (1, 100000), "temp": (0, 5000),
                     "deltat": (1e-6, 0.01)},
        "minimize": {"maxiter": (1, 100000)},
        "nma": {"maxiter": (1, 100000), "linewidth": (0.1, 500)},
        "geometry_ff": {"bfactor": (0.01, 1), "afactor": (0.01, 1), "factor": (0.01, 1)},
        "train": {"max_iter": (1, 100000), "max_generations": (1, 10000),
                  "temp": (1e-6, 1e6), "seed": (0, 2147483647)},
        "b2": {"T1": (1, 10000), "T2": (1, 10000), "dT": (0, 10000), "maxdimer": (1, 10000000)},
    })

    def __post_init__(self) -> None:
        """!
        @brief Make the paths absolute and check the mode.
        @throws ValueError for an unknown mode.
        """
        self.data_dir = Path(self.data_dir).expanduser().resolve()
        if self.forcefield_dirs is None:
            self.forcefield_dirs = library.default_dirs()
        self.forcefield_dirs = [Path(d).expanduser() for d in self.forcefield_dirs]
        if self.default_forcefield is None:
            self.default_forcefield = find_default_forcefield(self.forcefield_dirs)
        if self.default_forcefield is not None:
            self.default_forcefield = Path(self.default_forcefield).expanduser()
        if self.config_file is not None:
            self.config_file = Path(self.config_file).expanduser()
        if self.cluster_jobs_dir is None:
            self.cluster_jobs_dir = self.data_dir / "cluster"
        self.cluster_jobs_dir = Path(self.cluster_jobs_dir).expanduser().resolve()
        if self.work_dir is not None:
            self.work_dir = Path(self.work_dir).expanduser().resolve()
        if self.mode not in ("desktop", "server"):
            raise ValueError("The mode must be desktop or server, not '%s'" % self.mode)

    @property
    def uploads_dir(self) -> Path:
        """!
        @brief The directory with the uploaded files.
        @return The path, below the data directory.
        """
        return self.data_dir / "uploads"

    @property
    def jobs_dir(self) -> Path:
        """!
        @brief The directory with one directory for each job.
        @return The path, below the data directory.
        """
        return self.data_dir / "jobs"
