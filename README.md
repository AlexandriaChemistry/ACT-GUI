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
- the tasks charges, minimize, nma and simulate, for a molecule from a file
  or from a SMILES string, with the default force field or one that is
  uploaded.

## Requirements

- ACT with the Python package `act` (branch `python-api` of ACT for now) and
  the `alexandria` program in the `PATH`, or its path in the environment
  variable `ACT_ALEXANDRIA`;
- Python 3.9 or newer with `fastapi` and `uvicorn`.

## Running on the desktop

    python -m act_gui

prints an address and opens it in the browser. The server listens on the
local machine only, and the address has a key without which the server does
not answer. Uploads and results are kept in a temporary directory that is
removed at the end; `--data DIR` keeps them in a directory.

## Tests

    python -m unittest discover -s tests -t .

The tests of the server use a function in the place of the program. The
tests in `tests/test_alexandria.py` run the real program when
`ACT_ALEXANDRIA` is set.

## The default force field

`act_gui/data/forcefields/ACS-pg1.xml` is a test force field of ACT. It is
used until the new default force field of ACT exists. The environment
variable `ACT_GUI_FORCEFIELD` selects another file.

## License

GNU General Public License version 2 or later, as ACT. See `LICENSE`.
