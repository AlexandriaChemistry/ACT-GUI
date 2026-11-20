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

The wavenumber axis of the spectrum can be reversed, to run from high to
low as in measured spectra. The spectrum and the energies can be saved as PDF files, which
[plotXVG](https://github.com/AlexandriaChemistry/plotXVG) makes on the
server from the xvg files of the program.

The tab OpenMM gives an archive with which the molecule is simulated with
OpenMM and the same force field: the coordinates and the force field file
that `alexandria gentop -openmm` writes, the settings of the simulation, a
script that runs it with `ActOpenMMSim` of ACT-utilities, and a text that
says what is what.

The force field workspace has one force field at a time, which is made
there or opened from a file:

- *Generate* makes a new force field with `alexandria gen_ff` from a few
  choices: the charge model, the potentials with their combination rules,
  the atom types and which of them get a shell or a second charge, and
  predefined virtual sites. It does what the program `actgui` of
  ACT-utilities did. The table of atom types and the virtual sites that
  `gen_ff` got can be saved as well.
- *Geometry* takes the bond lengths, angles and dihedral angles from the
  compounds of a molprop file with `alexandria geometry_ff`.
- *Edit and validate* lists the parameters with their value, bounds and
  mutability, changes them with `alexandria edit_ff`, and shows the
  problems that `edit_ff -validate` finds, with a button to write the force
  field without those that writing removes.

Every step writes a new file on the server, which can be saved and which
the molecule workspace can use for its calculations.

The molprop workspace opens a molprop file and shows what is in it: the
compounds with their formula, the compounds they consist of, the number of
calculations and the methods, the number of values of each property, and
the result of the validation of `alexandria edit_mp -validate`.

With a file open, a force field (the default, the one of the force field
workspace or an uploaded one) can be compared with its data: `alexandria
train_ff -nooptimize` computes the properties that are chosen for every
compound, and the page shows the statistics and, for each property, the
values of the force field against the reference values. The plot can be
saved as a PDF file and the data as an xvg file.

Not there yet: virtual sites by point and click, the comparison of two
force fields, a choice of the compounds to compare on, histograms of the
energies of a compound, and the server for several users.

The page uses [3Dmol.js](https://3dmol.org) (BSD-3-Clause, in
`act_gui/static/vendor/`) to show molecules.

## Requirements

- ACT with the Python package `act` (the main branch of ACT; the force
  field workspace needs the branch `python-api-2` until that is merged) and
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
pictures of the page there. With `ACT_TESTDATA` set to the directory
`src/act/testdata` of the source of ACT, the tests that need a molprop file
run as well.

## The atom types and virtual sites of the generator

`act_gui/data/generator/` has the atom types that a new force field can be
made of (`atomtypes_gui.csv`) and the predefined virtual sites, copied from
the directory `share` of ACT-utilities. They move here for good when
`actgui` is removed from ACT-utilities.

## The default force field

`act_gui/data/forcefields/ACS-pg1.xml` is a test force field of ACT. It is
used until the new default force field of ACT exists. The environment
variable `ACT_GUI_FORCEFIELD` selects another file.

## License

GNU General Public License version 2 or later, as ACT. See `LICENSE`.
