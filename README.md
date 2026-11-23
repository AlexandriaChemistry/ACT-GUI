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
server from the xvg files of the program; the spectrum also as the xvg
file itself.

The tab Dimer scan does what the script `dimer_scan` of ACT-utilities
does: for a structure of two compounds you pick an atom of each (the
closest pair is proposed), a range of distances and whether bond lengths
are constrained. For each distance the second compound is moved along the
line between the two atoms and `alexandria minimize` relaxes the dimer
with these atoms frozen. The interaction energy is plotted against the
distance while the scan runs; a click on a point shows the minimized dimer
at that distance. This needs an `alexandria` whose `minimize -json` has
the interaction energies of a dimer.

The tab Second virial computes the second virial coefficient of a dimer
with `alexandria b2`, here on several processes or as a job on the
cluster, and shows B2(T) with its classical part and the corrections for
the force and the torque.

The tab OpenMM gives an archive with which the molecule is simulated with
OpenMM and the same force field: the coordinates and the force field file
that `alexandria gentop -openmm` writes, the settings of the simulation, a
script that runs it with `ActOpenMMSim` of ACT-utilities, and a text that
says what is what. With a cluster set, the simulation itself can run there:
the same files go along, the job runs `python3 run.py` (OpenMM and
ACT-utilities have to be there, for instance in the environment that the
settings of the cluster load), on a GPU if the job asks for one, and what
the script writes comes back; the potential energy is plotted when the job
is opened.

The force field workspace has one force field at a time, which is made
there or opened from a file:

- *Generate* makes a new force field with `alexandria gen_ff` from a few
  choices: the charge model, the potentials with their combination rules,
  the atom types and which of them get a shell or a second charge, and
  predefined virtual sites. Parameters with a known value are fixed at
  that value from files with a line
  `interaction|potential|atom type|parameter|value|unit` for each, as
  `actgui` read them. It does what the program `actgui` of
  ACT-utilities, which it replaces, did. The table of atom types and the virtual sites that
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
  then only the compounds that are ticked there are used. The histograms
  of the bond lengths, angles and dihedrals are shown, one type at a
  time; a click on a bar lists from which compounds, calculations and
  atoms its values come (`geometry_ff -samples`), to understand odd
  values.
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
the result of the validation of `alexandria edit_mp -validate`. The
validation and the properties are shown under the name of the file; a
click on a property shows only the compounds that have it, and a click on
a compound shows its details under the table. A click on
the header of a column sorts the table by it, a second click the other way
around and a third gives the order of the file again. Calculations that are
in the file twice with the same results, which `train_ff` uses once, are
reported; *Remove them and save* writes the file without them, as
`edit_mp -validate -o` does, under the name of the file or another.

With a file open, a force field (the default, the one of the force field
workspace or an uploaded one) can be compared with its data: `alexandria
train_ff -nooptimize` computes the properties that are chosen for every
compound, and the page shows the statistics and, for each property, the
values of the force field against the reference values. Only the
properties that were chosen are shown, in the order in which they were
ticked, the first one first, also of a job on the cluster. The legend of a
plot tells the compounds for training from those for testing. A plot is
zoomed with the wheel of the mouse or the buttons + and −, moved by
dragging, and shown whole again with *All* or a double click. The plot can be
saved as a PDF file and the data as an xvg file.

In a plot of a term of the interaction energy, such as the dispersion of
SAPT against that of the force field, a click on a point shows the
structure of that dimer, from the molprop file that is open, in a 3D view
with the two compounds in different colours, and a table with the
reference (SAPT), the force field (ACT) and their difference for every
term of that structure. The points come from the result of `train_ff` in
JSON, which names the calculation of each; with an older `alexandria`
whose result does not have them the plot is made from the xvg file and
cannot be clicked. For a compound with several structures, such as a
dimer, a histogram shows which energies the reference and the force field
have, for each term of the energy.

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
is the one that `train_ff` gets. Instead, *Open selection file…* in the
card of the comparison takes a selection file of the user, which
`train_ff` then gets as it is; the table is not used. The server checks
the file first: every line a name, a bar and `Train`, `Test` or `Ignore`,
and at least one compound of the molprop file for training. The page
tells how many compounds it has for training and for testing, and which
names the molprop file does not have, which `train_ff` leaves out.

Other programs can ask the server for the same file with a request to
`/api/selection`, with the compounds by name or by monomer and a part for
testing that is drawn with a seed, and have a selection file read with a
request to `/api/selection/read`.

A force field can also be trained on the data of the file, with `alexandria
train_ff`: you choose the kinds of parameters to train (such as `sigma`
and `epsilon`) from those that the force field has and that are not fixed,
the properties to train on with a weight for each, the optimizer (Monte Carlo, or the
genetic algorithm with or without Monte Carlo, which needs two processes
at least), the number of iterations and generations, the number of
individuals (sets of parameters, `train_ff -pop_size`) and the part of the
compounds that is kept for testing, unless they were chosen in the table.
Without a number of individuals there is one for each process. The genetic
algorithm needs an even number of them, and may have more individuals than
processes, which then compute several in turn; Monte Carlo alone has at
most one for each process. With fewer individuals than processes the number
of processes is a multiple of the number of individuals, such that each gets
as many. More than one process needs `mpirun`, `mpiexec` or `srun` in
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
individuals as there were. Monte Carlo alone leaves no
individuals and does not read them, so it continues from the force field
only. *Start anew* goes back to a training from the beginning. A training
that stopped because its time was up is continued in the same way.

The tab Settings has the screen mode and, on the desktop, the Slurm
cluster for long jobs: the server, your user name there and a working
directory. The comparison can then run as a job on the cluster, and the
tab Cluster lists the jobs, see below.

The screen is light, dark (white on black) or hippy (yellow on dark
purple, with orange borders). The button at the right of the top bar goes
to the next mode, the tab Settings offers the three. On the desktop the
choice is kept in the file with your settings (see below) under
`"appearance": {"theme": "dark"}`, and the page starts in it the next
time.

Files are saved under a name that the user chooses, starting from a name
that the page proposes: a force field after the file it was opened from
(with `-changed` when it was changed), a trained force field and a
selection after the molprop file, plots and data after what they show.
Chrome and Edge show the dialog of the system, with the folder; other
browsers, such as Firefox, Safari and DuckDuckGo, do not let a page do
that, so the page asks for the name and the browser puts the file in its
folder for downloads, or asks where when it is set to ask for each
download.

Not there yet: the server for several users.

The page uses [3Dmol.js](https://3dmol.org) (BSD-3-Clause, in
`act_gui/static/vendor/`) to show molecules.

## Installing with conda

The conda package `act-gui` on the channel `alexandriachemistry` brings
everything along: the Python package `act` of ACT and the program
`alexandria` (through the package `alexandria-python`), FastAPI, uvicorn,
plotXVG and the force fields of the package `forcefields`.

    conda install -c alexandriachemistry -c conda-forge act-gui
    act-gui

The package is made with the recipe in `conda/` by the workflow
`.github/workflows/conda.yml`: pull requests build and test it, a tag
`v<version>` (the version of `act_gui/__init__.py`, which the recipe has
as well) uploads it with the label `main`. Its test starts the server and
computes the charges of water with the `alexandria` of the environment.
The package needs `alexandria-python` 1.95b or newer on the channel; as
long as there is none, the workflow builds it without the test and does
not upload it. To build it yourself:

    rattler-build build --recipe conda/recipe.yaml \
        --channel alexandriachemistry --channel conda-forge

## Requirements

Without conda:

- ACT with the Python package `act` (ACT 1.95b or newer) and
  the `alexandria` program in the `PATH`, or its path in the environment
  variable `ACT_ALEXANDRIA`;
- Python 3.9 or newer with `fastapi` and `uvicorn`;
- optionally `plotxvg`, to save plots as PDF files. Without it the page
  does not show the buttons for that;
- optionally the force fields of the repository
  [ForceFields](https://github.com/AlexandriaChemistry/ForceFields),
  installed in the prefix of ACT, see below.

## Installed force fields

Wherever a force field is chosen, the page also offers the force fields
that are installed, grouped by the directory they are in (Water, HF,
Inorganic, ...), with their reference as a tooltip. The force field tab
opens them with *Installed force fields* and *Open*. The server looks for
`.xml` files of ACT, also in subdirectories, in these directories, of which
the first one that has a force field counts:

- the directories in the environment variable `ACT_FORCEFIELDS`, separated
  as in `PATH`, for force fields of your own;
- `forcefields` in the directory of the environment variable `ACTDATA`;
- `share/act/forcefields` of the installation of ACT, where the package
  `forcefields` puts them.

A force field is known by its path below such a directory without `.xml`,
such as `Water/TIP4P-2005`; the page never sees where the file is.

## Running on the desktop

    act-gui

(or `actgui`, the name of the program that this interface replaces, or
`python -m act_gui`) prints an address and opens it in the browser. The
server listens on the local machine only, and the address has a key
without which the server does not answer. Uploads and results are kept in
a temporary directory that is removed at the end; `--data DIR` keeps them
in a directory.

Without installing ACT-GUI, the script `actgui` in this directory starts
it from here, with the Python of `.venv` here if there is one (or that of
`ACT_GUI_PYTHON`, or `python3`). The Python package `act` and the program
`alexandria` are found as always; from the source and the build of ACT,
for instance:

    PYTHONPATH=/path/to/ACT/python ACT_ALEXANDRIA=/path/to/ACT/build/bin/alexandria ./actgui

A link to the script in a directory of your `PATH` works too.

## The cluster for long jobs

Long jobs go to a Slurm cluster on which you have an account. In the
tab Settings you give the name of the server (a host name or a name from
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
the cluster": the comparison of a force field with a molprop file, the
training of a force field, the second virial coefficient and a simulation
with OpenMM. A training has one set of parameters for
each process of the job. You give the job a name (or leave that to the interface),
the number of processes, a time limit and whether it needs a GPU. Each
process gets a physical core of its own (`--hint=nomultithread`): Slurm
counts the hardware threads of a core as CPUs, while `mpirun` starts no
more processes than there are cores. The
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
those of a calculation on your machine. All output files come back, also
those that the program writes under their default names without being
asked, such as the log file of a training; Files lists them, and each can
be saved. A training writes how far it is
in a file in its directory; the last line of it comes along in the login
that asks for the state, and the list shows the generation and the lowest
χ² so far. Of what the cluster sends only these numbers are kept. After a login that did not work
the cluster is not asked again until you press the button: a cluster may
block an account that keeps trying.

A job that failed says why, under its row: the interface reads the
output of Slurm and the log files that came back, and picks the lines that
tell. These are the message with which `alexandria` stopped (told once,
with the number of processes that said it), the compounds that got no
topology and what the force field lacks for them, `mpirun` that found
fewer cores than processes, a program, module or conda environment that
the job did not find, the time limit, memory, the GPU, or a crash. With
the reason comes what to change. Each line opens the file it is from, at
that line, and the output, the log and the script can be read on the page.
Send again makes a new job of one that is done, with other processes, time
or GPU, and the settings of the cluster as they are now. It is called
`name-r2`, `name-r3` and so on. Its files are copied from the directory of
the old job on the cluster, so nothing is sent from your machine, also
after the interface was started again. The number of individuals of a
training and the time at which it stops follow the new job. A job with
other input is sent from its tab.

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
run as well. The tests with the real program use the default force field,
see below, or else `forcefields/ACS-pg1.xml` of `ACT_TESTDATA`, and are
skipped without either.

## The atom types and virtual sites of the generator

`act_gui/data/generator/` has the atom types that a new force field can be
made of (`atomtypes_gui.csv`) and the predefined virtual sites. The atom
types are only here since `actgui` was removed from ACT-utilities; the
virtual sites are also in the directory `share` of ACT-utilities, as
examples for `alexandria gen_ff`.

## The logo

`act_gui/static/img/act-logo.png` (in the header of the page) and
`act_gui/static/favicon.ico` (the icon of the page, 16 to 64 pixels) are
made from `docs/act_logo.png` of ACT.

## The default force field

The default force field is the installed force field `Alexandria/ACS-pg1`
(see *Installed force fields*), which the package `forcefields` has, or the
file of the environment variable `ACT_GUI_FORCEFIELD`. It is a test force
field of ACT, the same as `src/act/testdata/forcefields/ACS-pg1.xml`, used
until the new default force field of ACT exists; `DEFAULT_LIBRARY_ID` in
`act_gui/settings.py` names it. Without it the server still starts: the
page offers the installed force fields and files of the user, and says
that the default is not installed.

Its missing bonds, angles, linear angles and dihedrals were filled in with
`admin/fill_forcefield.py` of ACT, so that it describes many more small
organic molecules with H, C, N, O, F, P, S, Cl, Br and I. The geometry of the added
parameters comes from other force fields of ACT, but the torsions are not
trained: low frequencies of normal modes are poor, and some molecules have
imaginary ones.

## License

GNU General Public License version 2 or later, as ACT. See `LICENSE`.
