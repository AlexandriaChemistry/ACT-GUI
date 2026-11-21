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
- *Virtual sites* defines sites by point and click: two, three or four
  atoms of a molecule are picked in the viewer or in a table, the kind of
  site and the bounds of its parameters are chosen, and the site is shown
  where it would be. The definitions are on the types of the atoms, in the
  form of `alexandria gen_ff -vs`; they are used when a force field is
  generated and can be saved as a file. *Open file* adds the sites of such
  a file, one that was saved here or one that was written for `gen_ff -vs`.
- *Geometry* takes the bond lengths, angles and dihedral angles from the
  compounds of a molprop file with `alexandria geometry_ff`. The file is
  one of its own, or the file that is open in the tab *Molprop data*, and
  then only the compounds that are ticked there are used.
- *Edit and validate* lists the parameters with their value, bounds and
  mutability, changes them with `alexandria edit_ff`, and shows the
  problems that `edit_ff -validate` finds, with a button to write the force
  field without those that writing removes. Of a particle type the charge
  and the width of its distribution (zeta) can be changed, also those of a
  virtual site; the mass cannot.

- *Compare* lists what differs between the force field of the workspace
  and another one, the default or a file: the parameters with another
  value, with other bounds or another way of training, and those that
  only one of the two has. After a training, "What changed" does this
  for the trained force field and the one it started from.

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
saved as a PDF file and the data as an xvg file. For a compound with
several structures, such as a dimer, a histogram shows which energies the
reference and the force field have, for each term of the energy.

The table of compounds says what each compound is used for: training,
testing, or nothing. A click changes it for one compound, and the buttons
above the table for all compounds that the table shows, which are those
with the text that is searched for. A comparison then gives the
statistics for both data sets.

The same choices make a selection file for `train_ff -sel`, which the
card *Selection file* saves under the name of the molprop file with the
extension `.dat`. It has the compounds that are ticked in the table, each
for training or for testing:

- A box in front of each compound says whether it is used, and the box in
  the header does so for all compounds that the table shows. The table can
  be limited to the compounds that have values of one property.
- For a file with dimers the card lists the monomers. Ticking monomers
  chooses the dimers they are in: with the dimers A#B, A#C and B#C,
  ticking only A gives A#B and A#C but not B#C. The other setting uses a
  dimer only when all its monomers are ticked. The monomers are the parts
  of the name of a compound between the # signs when there are as many as
  the compound has molecules; otherwise the names or formulas that
  `edit_mp` gives of those.
- *Draw at random* uses a percentage of the ticked compounds for testing
  and the others for training.
- *Open* reads a selection file that exists already, as `alexandria` reads
  it: a line has the name of a compound, a bar and `Train`, `Test` or
  `Ignore`, and the two compounds of a dimer may be in the other order
  than in the molprop file. The table then says what the file says;
  compounds of the molprop file that the selection does not have are not
  used. The page tells which names of the selection are not in the molprop
  file and how many lines were not understood.

A comparison or a training, on this computer or on the cluster, uses what
the table says at that moment, so a selection that was opened or made here
is the one that `train_ff` gets.

Other programs can ask the server for the same file with a request to
`/api/selection`, with the compounds by name or by monomer and a part for
testing that is drawn with a seed, and have a selection file read with a
request to `/api/selection/read`.

A force field can also be trained on the data of the file, with `alexandria
train_ff`: you choose the kinds of parameters to train (such as `sigma`
and `epsilon`) from those that the force field has and that are not fixed,
the properties to train on with a weight for each, the optimizer (Monte Carlo, or the
genetic algorithm with or without Monte Carlo, which needs an even number
of processes), the number of iterations and generations, and the part of the
compounds that is kept for testing, unless they were chosen in the table. `train_ff` has one set of parameters
for each process, so the number of processes is the size of the
population; more than one process needs `mpirun`, `mpiexec` or `srun` in
the `PATH`, or the program in the environment variable `ACT_MPIEXEC`. The
page follows the training with a plot of χ², from what
`train_ff -progress` writes, lists the trained parameters and compares the
trained force field with the data. The trained force field can be saved
or opened in the force field workspace. A calculation on the desktop may
take ten minutes, and a job on the cluster the time it asks for. A training
is told to stop before that (`train_ff -maxh`, with a tenth of the time
left for the end of the program, two minutes at least and half an hour at
most): it then ends in order, with the best force field it found, and the
page says that its time was up. Long trainings belong on the cluster, see
below. This needs an `alexandria` that has `train_ff -maxh`, also on the
cluster; with an older one on the desktop a training is stopped at the
limit without results, as before.

*Continue training* sets the form up to train further from a training
that is done, here or on the cluster: the next one starts from the trained
force field, without random values. With the genetic algorithm it also
starts from the individuals that the earlier training ended with
(`train_ff -gpin` with the file that `-gpout` wrote), which needs as many
processes as there were individuals. Monte Carlo alone leaves no
individuals and does not read them, so it continues from the force field
only. *Start anew* goes back to a training from the beginning. A training
that stopped because its time was up is continued in the same way.

The tab Cluster, on the desktop, takes the Slurm cluster for long jobs:
the server, your user name there and a working directory. The comparison
can then run as a job on the cluster, and the tab lists the jobs, see
below.

Not there yet: the server for several users.

The page uses [3Dmol.js](https://3dmol.org) (BSD-3-Clause, in
`act_gui/static/vendor/`) to show molecules.

## Requirements

- ACT with the Python package `act` (the main branch of ACT) and
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

## The cluster for long jobs

Long jobs go to a Slurm cluster on which you have an account. In the
tab Cluster you give the name of the server (a host name or a name from
your ssh configuration), your user name there and a working directory,
with its full path or as a path in your home directory. They are kept in
the file `settings.json` in the directory `act-gui` of your configuration
directory (`~/.config`, or `XDG_CONFIG_HOME`), which is read when the
interface starts; `--config FILE` names another file. The file looks like

    {
      "cluster": {
        "server": "csb",
        "user": "yourname",
        "workdir": "act-jobs",
        "load": "module",
        "load_name": "ACT",
        "partition": "CLUSTER,CLUSTER-AMD",
        "gpu_partition": "GPU,CLUSTER-AMD",
        "gpu_gres": "gpu:1",
        "account": "",
        "launcher": "srun",
        "program": "alexandria",
        "poll_seconds": 120
      }
    }

Only the first three have to be given; the others are filled in with these
values, which are those for the cluster csb, and can be changed in the tab.
`load` says how ACT is made available in a job: `module` (`module load`
with the name in `load_name`), `source` (the shell reads the file in
`load_name`, such as the `ACTRC` of an installation), `conda` (the
environment in `load_name` is activated) or `none`. Jobs without a GPU go
to the partitions in `partition`; jobs that need one go to those in
`gpu_partition` and ask for `gpu_gres` with `--gres`. `program` is the
name of the program of ACT on the cluster: `alexandria`, or `alexandria_d`
for a build in double precision.

The interface logs in with the `ssh` program of your machine, which uses
your own keys and settings. It never asks for a password or a key and
stores none, so the login has to work without a question: set up a key for
the server, or open a connection in a terminal with connection sharing
(`ControlMaster`) if the server asks for a password or a code each time.
The button Test the login logs in once and tells whether the directory is
there and the server has Slurm. The three settings are checked before they
are used: a name that ssh could take for an option, or a directory with a
character that means something to a shell, is refused.

### Jobs

With a cluster set, a calculation that can run there has the choice "On
the cluster": the comparison of a force field with a molprop file, and
the training of a force field. A training has one set of parameters for
each process of the job. You give the job a name (or leave that to the interface),
the number of processes, a time limit and whether it needs a GPU. The
interface then shows the job before anything is sent: the script for
Slurm, the command and the files that go along. The files get a name
after the option they are for (`ff.xml`, `mp.xml`), so no name of a file
on your machine goes to the cluster. The command is made with the options
that the `alexandria` of your machine knows; the program on the cluster,
whose name is a setting, has to know them as well.

Sending a job is one login: the files and the script go to the directory
`workdir/name-of-the-job`, where `sbatch` is started. The tab Cluster
lists the jobs with their state. The interface asks the cluster for the
state of all jobs in one login, not more often than `poll_seconds` says
(the button Ask the cluster now asks sooner, but not within half a
minute), and only while the page is open. When a job has ended its
results are fetched in one more login, and Show results shows them as
those of a calculation on your machine. A training writes how far it is
in a file in its directory; the last line of it comes along in the login
that asks for the state, and the list shows the generation and the lowest
χ² so far. Of what the cluster sends only these numbers are kept. After a login that did not work
the cluster is not asked again until you press the button: a cluster may
block an account that keeps trying.

What is known about the jobs, and their results, are kept in the
directory `act-gui/cluster-jobs` of your data directory (`~/.local/share`,
or `XDG_DATA_HOME`), so that the jobs are there when the interface starts
again; `--cluster-jobs DIR` names another directory. Stop cancels a job
with `scancel`. Forget takes a job that is done from the list, with its
results on your machine. Nothing is ever removed on the cluster: the
directory of a job stays there until you remove it yourself.

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
