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
@brief The archive with which a molecule is simulated with OpenMM.

alexandria gentop writes the force field for OpenMM and the coordinates of
the molecule, with the same names for the residue and the atoms. This
module adds the settings of the simulation, a script that runs it with the
class ActOpenMMSim of ACT-utilities and a text that says what is what, and
puts the five files in one zip archive.

The archive is for one molecule in vacuum, as it is shown in the interface.
The text in the archive says what to change for a liquid.
"""
from __future__ import annotations

import io
import math
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Tuple

## The names of the files in the archive
PDB_NAME = "conf.pdb"
XML_NAME = "openmm.xml"
DAT_NAME = "simulation.dat"
RUN_NAME = "run.py"
README_NAME = "README.md"
## The directory in the archive that holds the files
FOLDER = "act-openmm"

## The smallest and largest value of each setting
LIMITS: Dict[str, Tuple[float, float]] = {
    "steps": (1, 1_000_000_000),
    "dt": (0.01, 4.0),
    "temperature": (0.0, 5000.0),
}


class PackageError(ValueError):
    """!
    @brief Settings that are not accepted. The message is for the user.
    """


@dataclass(frozen=True)
class PackageSettings:
    """!
    @brief The settings of the simulation that the user chooses.
    """
    ## The number of steps
    steps: int = 100000
    ## The time step in fs
    dt: float = 0.5
    ## The temperature in K
    temperature: float = 298.15

    @property
    def save_every(self) -> int:
        """!
        @brief How often the energies are written.
        @return A number of steps that gives about a thousand lines.
        """
        return max(1, self.steps // 1000)

    @property
    def frame_every(self) -> int:
        """!
        @brief How often the coordinates are written.
        @return A number of steps that gives about a hundred frames.
        """
        return max(1, self.steps // 100)


def _number(name: str, value: Any, whole: bool = False) -> float:
    """!
    @brief Check one setting.
    @param name  The name of the setting.
    @param value What the request gave: a number or a text with a number.
    @param whole Whether the number must be whole.
    @return The number.
    @throws PackageError if it is no number or outside the limits.
    """
    try:
        if isinstance(value, bool):
            raise ValueError
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PackageError("The setting '%s' must be a number" % name) from exc
    low, high = LIMITS[name]
    if not math.isfinite(number) or not low <= number <= high:
        raise PackageError("The setting '%s' must be between %g and %g" % (name, low, high))
    if whole and number != int(number):
        raise PackageError("The setting '%s' must be a whole number" % name)
    return number


def parse_settings(steps: Any = None, dt: Any = None, temperature: Any = None) -> PackageSettings:
    """!
    @brief Make the settings from what a request gives.
    @param steps       The number of steps, the default if None.
    @param dt          The time step in fs, the default if None.
    @param temperature The temperature in K, the default if None.
    @return The settings.
    @throws PackageError if a value is not accepted.
    """
    default = PackageSettings()
    return PackageSettings(
        steps=default.steps if steps is None else int(_number("steps", steps, whole=True)),
        dt=default.dt if dt is None else _number("dt", dt),
        temperature=default.temperature if temperature is None
        else _number("temperature", temperature))


def simulation_dat(settings: PackageSettings) -> str:
    """!
    @brief The settings of the simulation in the format of act_openmm.py.
    @param settings What the user chose.
    @return The text of the file simulation.dat.
    """
    return """# Settings of an OpenMM simulation with an Alexandria force field, read by
# the class ActOpenMMSim (act_openmm.py of ACT-utilities). Made by the user
# interface of ACT for one molecule in vacuum. Change what you need.

# Run control. Time step in picoseconds.
dt                               = {dt:.6g}*picoseconds
minimizationSteps                = 1000
equilibrationSteps               = 0
steps                            = {steps}

# Output: every how many steps the energies (energy.csv) and the coordinates
# (trajectory.pdb) are written. Zero for no file.
saveEnergy                       = {save}
savePdb                          = {frame}
saveXtc                          = 0
saveChk                          = 0
outStep                          = True
outTime                          = True
outPotentialEnergy               = True
outKineticEnergy                 = True
outTemperature                   = True
outDensity                       = False
outSeparator                     = ;

# Interactions. NoCutoff computes all pairs, for a molecule in vacuum. For
# a liquid in a periodic box use PME, or LJPME, with a cut-off.
nonbondedMethod                  = NoCutoff
nonbondedCutoff                  = 1.0*nanometers
dielectricConstant               = 1.0
ewaldErrorTolerance              = 1e-4
useDispersionCorrection          = False
useSwitchingFunction             = False

# Temperature in kelvin. The shells of a polarizable force field are kept
# at temperature_s. Friction coefficients in 1/picosecond.
temperature_c                    = {temperature:.6g}*kelvin
temperature_s                    = 1.0*kelvin
friction_c                       = 5.0/picosecond
friction_s                       = 20.0/picosecond
useAndersenThermostat            = False
useMonteCarloBarostat            = False
useMonteCarloAnisotropicBarostat = False
pressure                         = 1.0*atmospheres
barostatInterval                 = 25

# The integrator for a polarizable force field. Without shells the
# NoseHooverIntegrator is used whatever is given here.
integrator                       = DrudeLangevinIntegrator
drudeMass                        = 0.1
maxDrudeDistance                 = 0.02
commremovalFrequency             = 25

# Constraints: None, HBonds, AllBonds or HAngles
constraints                      = None
rigidWater                       = False
constraintTolerance              = 0.000001

# Where to compute: CUDA, OpenCL, CPU or Reference. Another one is taken
# if this one is not there.
usePlatform                      = CPU
""".format(dt=settings.dt / 1000.0, steps=settings.steps, save=settings.save_every,
           frame=settings.frame_every, temperature=settings.temperature)


def run_script() -> str:
    """!
    @brief The script that runs the simulation.
    @return The text of the file run.py.
    """
    return '''#!/usr/bin/env python3
"""Run the simulation of this directory with OpenMM.

Needs OpenMM and the Python modules of ACT-utilities, see README.md.
"""
from act_openmm import ActOpenMMSim

sim = ActOpenMMSim(pdbfile="{pdb}", datfile="{dat}", xmlfile="{xml}",
                   txtfile="output.txt")
sim.run()
# The energies and the temperature as xvg files, for instance for plotXVG
sim.log_to_xvg("energy.xvg", ["Potential Energy (kJ/mole)"])
sim.log_to_xvg("temperature.xvg", ["Temperature (K)"])
sim.write_coordinates("final.pdb")
'''.format(pdb=PDB_NAME, dat=DAT_NAME, xml=XML_NAME)


def readme(settings: PackageSettings, version: str) -> str:
    """!
    @brief The text that explains the archive.
    @param settings What the user chose.
    @param version  The version of the interface.
    @return The text of the file README.md.
    """
    return """# Simulation with OpenMM and an Alexandria force field

These files were made by the user interface of the Alexandria Chemistry
Toolkit (ACT), version {version}, for the molecule and the force field that
were selected there.

| File | What it is |
|---|---|
| `{pdb}` | The coordinates of the atoms. OpenMM adds the shells of a polarizable force field. Written by `alexandria gentop -c`. |
| `{xml}` | The force field for OpenMM, for this molecule. Written by `alexandria gentop -openmm`. |
| `{dat}` | The settings of the simulation: {steps} steps of {dt:g} fs at {temperature:g} K, one molecule in vacuum. |
| `{run}` | The script that runs the simulation. |

## Running

OpenMM and the Python modules of ACT-utilities are needed, for instance
from conda:

    conda install -c conda-forge -c alexandriachemistry openmm act-utilities

Then, in this directory:

    python {run}

The script minimizes the energy first and then runs the simulation. It
writes the energies to `energy.csv`, the coordinates to `trajectory.pdb`
and `final.pdb`, and what it did to `output.txt`. The files `energy.xvg`
and `temperature.xvg` can be plotted with plotXVG.

## Changing the simulation

All settings are in `{dat}`, with comments. The files describe one
molecule in vacuum. For a liquid, make a box of molecules with a program
such as packmol from `{pdb}`, keeping the names of the residue and the
atoms: OpenMM finds the bonds of each molecule from those names and the
force field file. Then set `nonbondedMethod` to `PME` or `LJPME` in
`{dat}` and choose a thermostat and, for constant pressure,
`useMonteCarloBarostat = True`.

The same force field file can be made on the command line with

    alexandria gentop -ff forcefield.xml -f molecule.sdf -openmm {xml} -c {pdb}

See the manual of ACT, https://alexandriachemistry.github.io/ACT, for more.
""".format(version=version, pdb=PDB_NAME, xml=XML_NAME, dat=DAT_NAME, run=RUN_NAME,
           steps=settings.steps, dt=settings.dt, temperature=settings.temperature)


def build(pdb: Path, xml: Path, settings: PackageSettings, version: str) -> bytes:
    """!
    @brief Make the archive.
    @param pdb      The coordinates that gentop wrote.
    @param xml      The force field for OpenMM that gentop wrote.
    @param settings What the user chose.
    @param version  The version of the interface, for the text.
    @return The zip archive. The same input gives the same bytes.
    @throws PackageError if a file of gentop is missing or empty.
    """
    contents = []
    for name, path in ((PDB_NAME, Path(pdb)), (XML_NAME, Path(xml))):
        if not path.is_file() or path.stat().st_size == 0:
            raise PackageError("The file %s was not made" % name)
        contents.append((name, path.read_bytes(), 0o644))
    contents.append((DAT_NAME, simulation_dat(settings).encode("utf-8"), 0o644))
    contents.append((RUN_NAME, run_script().encode("utf-8"), 0o755))
    contents.append((README_NAME, readme(settings, version).encode("utf-8"), 0o644))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data, mode in contents:
            # A fixed date, so that the archive depends on its contents only
            info = zipfile.ZipInfo("%s/%s" % (FOLDER, name), date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100000 | mode) << 16
            archive.writestr(info, data)
    return buffer.getvalue()
