# ACT-GUI

Graphical user interface of the
[Alexandria Chemistry Toolkit](https://github.com/AlexandriaChemistry/ACT)
(ACT), for the browser. A small web server runs the commands of ACT through
the Python package `act` and a page shows the results: molecules in three
dimensions with their charges, energy minimization, normal modes with the
infrared spectrum and thermochemistry, and short simulations.

The plan for this interface is in the ACT repository,
`docs/python-api-gui-plan.md`.

## Status

In development. What is there:

- the server: uploads of molecule and force field files, the job engine and
  the interface for the page (`act_gui/`), with tests;
- the molecule workspace of the page (`act_gui/static/`): a molecule from a
  SMILES string or a file, with the default force field or one that is
  uploaded, shown in three dimensions and coloured by charge; the table of
  charges; energy minimization with the original structure superimposed,
  the RMSD and the energy terms; normal modes with the infrared spectrum,
  the animated vibrations and the thermochemistry; short simulations with
  the energies plotted while the simulation runs and the trajectory
  animated afterwards.

The spectrum and the energies can be saved as PDF files, which
[plotXVG](https://github.com/AlexandriaChemistry/plotXVG) makes on the
server from the xvg files of the program.

The tab OpenMM gives an archive with which the molecule is simulated with
OpenMM and the same force field: the coordinates and the force field file
that `alexandria gentop -openmm` writes, the settings of the simulation, a
script that runs it with `ActOpenMMSim` of ACT-utilities, and a text that
says what is what.

Not there yet: the force field and molprop workspaces, and the server for
several users.

The page uses [3Dmol.js](https://3dmol.org) (BSD-3-Clause, in
`act_gui/static/vendor/`) to show molecules.

## Requirements

- ACT with the Python package `act` (branch `python-api` of ACT for now) and
  the `alexandria` program in the `PATH`, or its path in the environment
  variable `ACT_ALEXANDRIA`;
- Python 3.9 or newer with `fastapi` and `uvicorn`;
- optionally `plotxvg`, to save plots as PDF files. Without it the page
  does not show the buttons for that.

## Running on the desktop

    python -m act_gui

prints an address and opens it in the browser. The server listens on the
local machine only, and the address has a key without which the server does
not answer. Uploads and results are kept in a temporary directory that is
removed at the end; `--data DIR` keeps them in a directory.

## Tests

    python -m unittest discover -s tests -t .

The tests of the server use a function in the place of the program. With
`ACT_ALEXANDRIA` set, the tests in `tests/test_alexandria.py` run the real
program, and `tests/test_openmm_package.py` runs ten steps of the archive
for OpenMM when OpenMM and ACT-utilities are installed. The tests in
`tests/e2e` drive the page in Firefox without a window, through selenium,
with the real program; with `ACT_GUI_SHOTS` set to a directory they save
pictures of the page there.

## The default force field

`act_gui/data/forcefields/ACS-pg1.xml` is a test force field of ACT. It is
used until the new default force field of ACT exists. The environment
variable `ACT_GUI_FORCEFIELD` selects another file.

## License

GNU General Public License version 2 or later, as ACT. See `LICENSE`.
