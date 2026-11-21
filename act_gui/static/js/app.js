/*
 * This source file is part of the Alexandria Chemistry Toolkit.
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; either version 2
 * of the License, or (at your option) any later version.
 */
/**
 * @file The page of the molecule workspace: it sends the molecule and the
 * choices of the user to the server, follows the calculations and shows
 * their results in the viewer, the tables and the plots.
 *
 * All text that comes from the server or from files is put on the page as
 * text, never as markup.
 */
import { $, $$, alertBox, call, el, fmt, moveInk, pill, runTask, saveFile, seg, session, showOnly, tabs, toast, upload } from "./common.js";
import { css, esc, limits, markPoint, plot, redrawPlots } from "./plot.js";
import { startForceFields } from "./forcefield.js";
import { startMolprops } from "./molprop.js";
import { startCluster } from "./cluster.js";

/* global $3Dmol */

/** Coordinates in the files of ACT are in nm, the viewer works in Å. */
const NM = 10;

/** What the page knows. */
const state = {
  jobs: { nma: null, sim: null, openmm: null },  // the finished jobs whose files can be saved
  input: null,          // { smiles } or { file, label }
  forcefield: {},       // {} for the default or { ref, label }, ref as the server takes it
  mol: null,            // the molecule that was loaded, see moleculeFrom()
  xyz: null,            // the coordinates shown in the charges tab
  min: null,            // result of the minimization
  nma: null,            // result of the normal mode analysis
  sim: { points: [], series: "EPOT", job: null, pdb: null, nsteps: 0 },
  scan: { parts: [], points: [], job: null, running: null, picked: -1, pdb: null },  // the scan of a dimer: its compounds, points and the structure shown
  ui: { style: "ball", colour: "charge", labels: true, tab: "charges", focus: -1, ghost: true, mode: 0,
        reverse: false },   // reverse: wavenumbers from high to low in the spectrum
};

/* ---------- the server ---------- */

/**
 * Start a calculation on the molecule and wait for its result.
 * @param {string} task       charges, minimize, nma or simulate.
 * @param {object} options    The options of the task.
 * @param {function} onUpdate Called with the job each time it is asked for.
 * @returns {Promise<object>} The job when it has finished.
 * @throws {Error} If the calculation failed or was stopped.
 */
function runJob(task, options = {}, onUpdate = null, extra = {}) {
  const molecule = state.input.smiles ? { smiles: state.input.smiles } : { file: state.input.file };
  const forcefield = state.forcefield.ref || {};
  return runTask({ task, molecule, forcefield, options, ...extra }, onUpdate);
}

/**
 * Save a plot as a PDF file, made by plotXVG on the server.
 * @param {Element} button The button that was pressed.
 * @param {string} jobId   The job that has the data.
 * @param {string} task    The task of the job, for the name of the file.
 * @param {string} option  The option of the command that wrote the data.
 * @param {string} series  The series to plot, all if empty.
 * @param {boolean} reverse Whether the x-axis runs from large to small values.
 */
function savePdf(button, jobId, task, option, series = "", reverse = false) {
  const params = {};
  if (series) params.series = series;
  if (reverse) params.reverse = "true";
  // The name tells what is plotted, as the server names it
  const name = [task, option, ...(series ? series.split(",") : []), ...(reverse ? ["reversed"] : [])].join("-") + ".pdf";
  if (jobId) saveFile(button, `/jobs/${jobId}/plots/${option}`, params, "Making PDF…", name);
}

/* ---------- the molecule ---------- */

/**
 * Turn the description of a molecule from ACT into what the page shows:
 * the atoms with their net charge, that of the atom and its shells, and
 * the bonds between the atoms.
 * @param {object} molecule The object "molecule" of a result.
 * @returns {object} name, formula, charge, atoms, bonds, nshell and index,
 * the position of each atom in the list of particles.
 */
function moleculeFrom(molecule) {
  const particles = molecule.particles;
  const index = [], local = new Map();
  particles.forEach((p, i) => { if (p.particletype === "Atom") { local.set(i, index.length); index.push(i); } });
  const atoms = index.map(i => ({ elem: particles[i].element, name: particles[i].name, type: particles[i].type,
                                  q: particles[i].charge + (particles[i].charge_vs || 0) }));
  let nshell = 0;
  particles.forEach(p => {
    if (p.particletype === "Atom") return;
    nshell += 1;
    // The charge of a shell or virtual site counts for the atom it belongs to
    const parent = (p.parents || []).find(i => local.has(i));
    if (parent !== undefined) atoms[local.get(parent)].q += p.charge;
  });
  const bonds = molecule.bonds.filter(b => local.has(b.atoms[0]) && local.has(b.atoms[1]))
    .map(b => [local.get(b.atoms[0]), local.get(b.atoms[1]), b.order]);
  return { name: molecule.name, formula: molecule.formula, charge: molecule.charge, mass: molecule.mass,
           atoms, bonds, nshell, index };
}

/**
 * The coordinates of the atoms in Å.
 * @param {number[][]} coords The coordinates of all particles in nm.
 * @returns {number[][]} The coordinates of the atoms.
 */
const atomCoords = coords => state.mol.index.map(i => coords[i].map(v => v * NM));

/**
 * The largest charge on an atom, for the colour scale.
 * @returns {number} At least 0.1 e, rounded up to a tenth.
 */
function chargeScale() {
  const qmax = Math.max(0.1, ...state.mol.atoms.map(a => Math.abs(a.q)));
  return Math.ceil(qmax * 10 - 1e-9) / 10;
}

/* ---------- the viewer ---------- */

let V = null, triedV = false, zoomed = false;

/**
 * Make the viewer, or tell that the browser cannot show it.
 * @returns {object|null} The viewer.
 */
function makeViewer() {
  const host = $("#gl");
  try {
    if (typeof $3Dmol === "undefined") throw new Error("no viewer");
    const v = $3Dmol.createViewer(host, { backgroundColor: css("--stage"), antialias: true });
    if (!v) throw new Error("no viewer");
    return v;
  } catch (e) {
    const note = document.createElement("div");
    note.className = "nogl";
    note.textContent = "The 3D view needs WebGL, which this browser does not offer here.";
    host.parentNode.appendChild(note);
    return null;
  }
}

/**
 * The atoms in the form that the viewer takes.
 * @param {number[][]} xyz  The coordinates of the atoms in Å.
 * @param {number[][]} disp The displacement of each atom, for a vibration.
 * @returns {object[]} The atoms with their bonds.
 */
function viewerAtoms(xyz, disp = null) {
  const a = state.mol.atoms.map((at, i) => ({ elem: at.elem, atom: at.elem + (i + 1), serial: i + 1,
    x: xyz[i][0], y: xyz[i][1], z: xyz[i][2], bonds: [], bondOrder: [], hetflag: true, properties: { charge: at.q } }));
  state.mol.bonds.forEach(([i, j, o]) => {
    const order = Math.max(1, Math.round(o));
    a[i].bonds.push(j); a[i].bondOrder.push(order); a[j].bonds.push(i); a[j].bondOrder.push(order);
  });
  if (disp) a.forEach((at, i) => { at.dx = disp[i][0]; at.dy = disp[i][1]; at.dz = disp[i][2]; });
  return a;
}

/**
 * How the atoms are drawn.
 * @returns {object} The style for the viewer.
 */
function styleSpec() {
  const s = chargeScale();
  const cs = state.ui.colour === "charge" ? { colorscheme: { prop: "charge", gradient: new $3Dmol.Gradient.RWB(-s, s) } } : {};
  if (state.ui.style === "stick") return { stick: { radius: 0.16, ...cs } };
  if (state.ui.style === "sphere") return { sphere: { scale: 0.85, ...cs } };
  return { stick: { radius: 0.12, ...cs }, sphere: { scale: 0.26, ...cs } };
}

/**
 * Put the charge or the name on the atoms.
 * @param {number[][]} xyz The coordinates of the atoms in Å.
 */
function labelAtoms(xyz) {
  if (!state.ui.labels) return;
  xyz.forEach((c, i) => {
    const at = state.mol.atoms[i];
    if (at.elem === "H" && state.ui.tab !== "charges") return;
    const text = state.ui.colour === "charge" ? (at.q > 0 ? "+" : "−") + Math.abs(at.q).toFixed(2) : at.elem + (i + 1);
    V.addLabel(text, { position: { x: c[0], y: c[1], z: c[2] }, fontSize: 11, fontColor: css("--ink"),
      backgroundColor: css("--surface"), backgroundOpacity: 0.82, borderThickness: 0, inFront: true,
      showBackground: true, alignment: "center" });
  });
}

/** Draw what belongs to the selected tab in the viewer. */
function scene() {
  const ui = state.ui;
  $("#qlegend").hidden = ui.colour !== "charge" || !state.mol;
  const picked = state.scan.points[state.scan.picked];
  const titles = { charges: "Structure and charges", minimize: state.min ? "Minimized and original" : "Structure",
    modes: state.nma ? `Vibration at ${state.nma.modes[ui.mode].frequency.value.toFixed(0)} cm⁻¹` : "Structure",
    simulate: state.sim.pdb ? "Trajectory" : "Structure", openmm: "Structure",
    scan: state.scan.pdb && picked ? `Minimized at ${picked.distance.toFixed(3)} nm` : "Structure" };
  $("#scene-title").textContent = titles[ui.tab];
  if ($("#view-molecule").hidden || !state.mol) return;   // never draw into a hidden canvas
  if (!V && !triedV) { triedV = true; V = makeViewer(); }
  if (!V) return;
  V.stopAnimate(); V.removeAllModels(); V.removeAllShapes(); V.removeAllLabels();
  V.setBackgroundColor(css("--stage"));
  const s = chargeScale();
  $("#qlo").textContent = `−${s.toFixed(1)} e`;
  $("#qhi").textContent = `+${s.toFixed(1)} e`;
  if (ui.tab === "modes" && state.nma) {
    const mode = state.nma.modes[ui.mode];
    const disp = state.mol.index.map(i => mode.displacements[i]);
    const m = V.addModel();
    m.addAtoms(viewerAtoms(atomCoords(state.nma.coordinates.minimized), disp));
    m.frames = [m.atoms];   // vibrate() needs a first frame
    m.setStyle({}, styleSpec());
    m.vibrate(12, 0.55, true, V, { radius: 0.06, radiusRatio: 2.2, mid: 0.7, color: css("--accent") });
    V.animate({ loop: "backAndForth", interval: 45 });
  } else if (ui.tab === "minimize" && state.min) {
    const x = state.min.coordinates;
    if (ui.ghost) {
      const g = V.addModel();
      g.addAtoms(viewerAtoms(atomCoords(x.before)));
      g.setStyle({}, { stick: { radius: 0.09, color: css("--series2"), opacity: 0.55 } });
    }
    const m = V.addModel();
    m.addAtoms(viewerAtoms(atomCoords(x.after)));
    m.setStyle({}, styleSpec());
    labelAtoms(atomCoords(x.after));
  } else if (ui.tab === "scan" && state.scan.pdb) {
    // The minimized dimer at the distance that was clicked
    const m = V.addModel(state.scan.pdb, "pdb", { keepH: true });
    m.selectedAtoms({}).forEach((at, i) => { if (state.mol.atoms[i]) at.properties = { ...at.properties, charge: state.mol.atoms[i].q }; });
    m.setStyle({}, styleSpec());
    scanAtoms(m.selectedAtoms({}).map(at => [at.x, at.y, at.z]));
  } else if (ui.tab === "simulate" && state.sim.pdb) {
    // The viewer leaves out the hydrogen atoms of a pdb file unless told otherwise
    const m = V.addModelsAsFrames(state.sim.pdb, "pdb", { keepH: true });
    // The trajectory has the atoms in the order of the molecule, in every frame
    const frames = m.frames && m.frames.length ? m.frames : [m.selectedAtoms({})];
    frames.forEach(frame => {
      if (Array.isArray(frame)) frame.forEach((at, i) => { if (state.mol.atoms[i]) at.properties = { ...at.properties, charge: state.mol.atoms[i].q }; });
    });
    m.setStyle({}, styleSpec());
    V.animate({ loop: "forward", interval: 70 });
  } else {
    const m = V.addModel();
    m.addAtoms(viewerAtoms(state.xyz));
    m.setStyle({}, styleSpec());
    labelAtoms(state.xyz);
    if (ui.tab === "charges" && ui.focus >= 0) {
      const c = state.xyz[ui.focus];
      V.addSphere({ center: { x: c[0], y: c[1], z: c[2] }, radius: 0.62, color: css("--accent"), opacity: 0.35 });
    }
    if (ui.tab === "scan") scanAtoms(state.xyz);
  }
  if (!zoomed) { V.zoomTo(); V.zoom($("#gl").clientWidth < 500 ? 0.9 : 1.15); zoomed = true; }
  V.render();
}

/* ---------- loading a molecule ---------- */

/** Fill the line of facts about the molecule. */
function chips() {
  const m = state.mol, host = $("#molchips");
  host.textContent = "";
  const add = (label, value) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    if (label) chip.append(label + " ");
    const b = document.createElement("b");
    b.textContent = value;
    chip.append(b);
    host.append(chip);
  };
  if (m.name) add("", m.name);
  add("", m.formula);
  add("", `${m.atoms.length} atoms` + (m.nshell ? ` and ${m.nshell} shells` : ""));
  add("net charge", `${m.charge} e`);
  add("mass", `${m.mass.value.toFixed(2)} ${m.mass.unit}`);
  add("force field", state.forcefield.label || state.defaultForcefield);
}

/** Fill the table of charges. */
function chargeTable() {
  const s = chargeScale(), table = $("#qtable");
  table.innerHTML = `<tr><th>Atom</th><th>Type</th><th class="n">Charge (e)</th></tr>` + state.mol.atoms.map((a, i) =>
    `<tr class="pick${i === state.ui.focus ? " sel" : ""}" data-i="${i}"><td>${esc(a.elem)}${i + 1}</td><td class="num">${esc(a.type)}</td>` +
    `<td class="n">${fmt(a.q, 3)}<span class="qbar"><i></i></span></td></tr>`).join("");
  // The bars are sized here: the page cannot carry style attributes
  $$("tr.pick", table).forEach((row, i) => {
    const q = state.mol.atoms[i].q, bar = $(".qbar i", row), w = Math.min(1, Math.abs(q) / s) * 50;
    bar.style.width = w + "%";
    bar.style.background = q < 0 ? "var(--neg)" : "var(--pos)";
    bar.style[q < 0 ? "right" : "left"] = "50%";
  });
  $("#q-empty").hidden = true;
}

/** Forget the results of the previous molecule. */
function resetResults() {
  state.min = null; state.nma = null; state.jobs = { nma: null, sim: null, openmm: null };
  pill("omm-pill", "");
  $("#ir-pdf").hidden = true; $("#ir-xvg").hidden = true; $("#e-pdf").hidden = true;
  state.sim = { points: [], series: state.sim.series, job: null, pdb: null, nsteps: 0 };
  state.ui.focus = -1; state.ui.mode = 0;
  $("#rmsd").textContent = "–"; $("#dE").textContent = "–";
  for (const id of ["etable", "modetable", "thermo"]) document.getElementById(id).textContent = "";
  for (const id of ["irplot", "eplot"]) document.getElementById(id).textContent = "";
  for (const id of ["min-pill", "nma-pill"]) pill(id, "");
  $("#simbar").style.width = "0";
  $("#simtime").textContent = "";
}

/** Load the molecule of the input line: make its charges and show it. */
async function loadMolecule() {
  const smiles = $("#smiles").value.trim();
  if (!state.pendingFile && !smiles) { toast("Give a SMILES string or upload a file with a molecule."); return; }
  const button = $("#load");
  button.disabled = true; button.textContent = "Loading…";
  alertBox("");
  try {
    if (state.pendingFile) {
      const up = await upload(state.pendingFile, "molecule");
      state.input = { file: up.id, label: up.label };
    } else {
      state.input = { smiles };
    }
    if (state.pendingForcefield) {
      const up = await upload(state.pendingForcefield, "forcefield");
      state.forcefield = { ref: { file: up.id }, label: up.label };
      // Sent once: chosen again it is the same upload
      if (state.ownForcefield && state.ownForcefield.file === state.pendingForcefield) state.ownForcefield.ref = state.forcefield.ref;
      state.pendingForcefield = null;
    }
    const job = await runJob("charges");
    const compound = job.result.molecules[0];
    state.mol = moleculeFrom(compound.molecule);
    state.xyz = atomCoords(compound.coordinates.positions);
    resetResults();
    chips(); chargeTable();
    for (const id of ["run-min", "run-nma", "run-sim", "run-omm"]) document.getElementById(id).disabled = false;
    scanForm();
    zoomed = false;
    scene();
  } catch (e) {
    alertBox("The molecule could not be loaded. " + e.message);
  } finally {
    button.disabled = false; button.textContent = "Load molecule";
  }
}

/* ---------- minimize ---------- */

/** The names of the energy terms for a person. */
const TERMS = { BONDS: "Bonds", ANGLES: "Angles", LINEAR_ANGLES: "Linear angles", PROPER_DIHEDRALS: "Dihedrals",
  IMPROPER_DIHEDRALS: "Improper dihedrals", DISPERSION: "Dispersion", EXCHANGE: "Exchange", COULOMB: "Coulomb",
  INDUCTION: "Induction", POLARIZATION: "Polarization", VDW: "Van der Waals", EPOT: "Potential energy" };

/** Minimize the energy and show the result. */
async function minimize() {
  const button = $("#run-min");
  button.disabled = true; pill("min-pill", "Minimizing…", "warn"); alertBox("");
  try {
    const job = await runJob("minimize");
    const r = job.result;
    state.min = r;
    pill("min-pill", r.converged ? "Converged" : "Not converged: " + r.status, r.converged ? "good" : "warn");
    $("#rmsd").innerHTML = `${(r.rmsd.value * NM).toFixed(3)} <small>Å</small>`;
    const before = r["Energies before"], after = r["Energies after"];
    $("#dE").innerHTML = `${fmt(after.EPOT.value - before.EPOT.value, 1)} <small>kJ/mol</small>`;
    const rows = Object.keys(TERMS).filter(k => k in before && k in after && (before[k].value !== 0 || after[k].value !== 0));
    $("#etable").innerHTML = `<tr><th>Term (kJ/mol)</th><th class="n">Before</th><th class="n">After</th><th class="n">Change</th></tr>` +
      rows.map(k => `<tr class="${k === "EPOT" ? "total" : ""}"><td>${TERMS[k]}</td><td class="n">${fmt(before[k].value)}</td>` +
        `<td class="n">${fmt(after[k].value)}</td><td class="n">${fmt(after[k].value - before[k].value)}</td></tr>`).join("");
    scene();
  } catch (e) {
    pill("min-pill", "Failed", "bad"); alertBox("The minimization failed. " + e.message);
  } finally { button.disabled = false; }
}

/* ---------- normal modes ---------- */

/**
 * Read the columns of an xvg file.
 * @param {string} text The content of the file.
 * @returns {number[][]} The rows of numbers.
 */
function parseXvg(text) {
  return text.split("\n").filter(l => l.trim() && !"#@&".includes(l.trim()[0]))
    .map(l => l.trim().split(/\s+/).map(Number)).filter(r => r.every(isFinite));
}

/**
 * Select a normal mode and show its vibration.
 * @param {number} i The number of the mode, from zero.
 */
function pickMode(i) {
  state.ui.mode = i;
  modesPanel();
  scene();
  const row = $(`#modetable tr[data-i="${i}"]`);
  if (row) row.scrollIntoView({ block: "nearest" });
}

/**
 * The rows of a table for nested values with units.
 * @param {object} obj    The values.
 * @param {string} prefix The name of the group the values are in.
 * @returns {string} The rows.
 */
function unitRows(obj, prefix = "") {
  let rows = "";
  for (const [key, v] of Object.entries(obj)) {
    if (v && typeof v === "object" && "value" in v) {
      rows += `<tr><td>${esc(prefix ? prefix + ", " + key.toLowerCase() : key)}</td><td class="n">${fmt(v.value)}</td><td>${esc(v.unit)}</td></tr>`;
    } else if (v && typeof v === "object") {
      rows += unitRows(v, key);
    }
  }
  return rows;
}

/** Draw the spectrum and fill the tables of the normal modes. */
function modesPanel() {
  const r = state.nma;
  if (!r) return;
  const freq = r.modes.map(m => m.frequency.value), inten = r.modes.map(m => m.intensity.value);
  const xmax = Math.ceil((Math.max(...freq, 1000) + 200) / 200) * 200;
  // The part of the spectrum that is inside the axis
  const imax = Math.max(...inten, 1e-12), spec = (state.spectrum || []).filter(p => p[0] >= 0 && p[0] <= xmax);
  const smax = Math.max(...spec.map(p => p[1]), 1e-12);
  plot("irplot", { label: "Infrared spectrum", xlim: state.ui.reverse ? [xmax, 0] : [0, xmax], ylim: [0, 1.08], xlabel: "Wavenumber (cm⁻¹)",
    ylabel: "Relative intensity", height: 230, aspect: 0.5, yfmt: v => v.toFixed(1),
    series: [{ type: "line", x: spec.map(p => p[0]), y: spec.map(p => p[1] / smax), fill: true },
             { type: "sticks", x: freq, selected: state.ui.mode }], onPick: pickMode });
  $("#modetable").innerHTML = `<tr><th class="n">Mode</th><th class="n">cm⁻¹</th><th class="n">Rel. int.</th><th class="n">Red. mass</th></tr>` +
    freq.map((f, i) => `<tr class="pick${i === state.ui.mode ? " sel" : ""}" data-i="${i}"><td class="n">${i + 1}</td><td class="n">${f.toFixed(1)}</td>` +
      `<td class="n">${(inten[i] / imax).toFixed(2)}</td><td class="n">${r.modes[i].reduced_mass.value.toFixed(2)}</td></tr>`).join("");
  const thermo = r.Thermochemistry && r.Thermochemistry.Alexandria;
  if (thermo) {
    const key = Object.keys(thermo).filter(k => k.startsWith("T=")).pop();
    $("#thermo-title").textContent = "Thermochemistry at " + key.slice(2) + " K";
    $("#thermo").innerHTML = `<tr><th>Quantity</th><th class="n">Value</th><th>Unit</th></tr>` + unitRows(thermo[key]);
  }
}

/** Run the normal mode analysis and show the result. */
async function normalModes() {
  const button = $("#run-nma");
  button.disabled = true; pill("nma-pill", "Running…", "warn"); alertBox("");
  try {
    const job = await runJob("nma");
    state.nma = job.result;
    state.spectrum = parseXvg(await (await call(`/jobs/${job.id}/files/ir`)).text());
    state.jobs.nma = job.id; $("#ir-pdf").hidden = !session.pdf; $("#ir-xvg").hidden = false;
    const inten = state.nma.modes.map(m => m.intensity.value);
    state.ui.mode = inten.indexOf(Math.max(...inten));
    pill("nma-pill", `${state.nma.modes.length} modes`, "good");
    modesPanel(); scene();
  } catch (e) {
    pill("nma-pill", "Failed", "bad"); alertBox("The normal mode analysis failed. " + e.message);
  } finally { button.disabled = false; }
}
$("#modetable").addEventListener("click", e => { const r = e.target.closest("tr.pick"); if (r) pickMode(+r.dataset.i); });

/* ---------- simulate ---------- */

/** Draw the energy or the temperature of the simulation so far. */
function energyPlot() {
  const pts = state.sim.points, key = state.sim.series;
  if (!pts.length) return;
  const x = pts.map(p => p.time.value), y = pts.map(p => key === "T" ? p.temperature.value : p.energies[key]);
  const names = { EPOT: "Potential energy (kJ/mol)", EKIN: "Kinetic energy (kJ/mol)", ETOT: "Total energy (kJ/mol)", T: "Temperature (K)" };
  const last = pts[pts.length - 1];
  const xmax = Math.max(last.time.value * (last.nsteps / Math.max(1, last.step)), last.time.value, 1e-6);
  plot("eplot", { label: names[key] + " during the simulation", xlim: [0, last.step ? xmax : 1], ylim: limits(y),
    xlabel: "Time (ps)", ylabel: names[key], height: 260, yfmt: v => Math.abs(v) >= 1000 ? v.toFixed(0) : v.toFixed(1),
    series: [{ type: "line", x, y, endpoint: true }] });
}

/** Run a simulation, plot the energies as they come and show the trajectory. */
async function simulate() {
  const nsteps = Math.round(+$("#nsteps").value), deltat = +$("#dt").value / 1000, temp = +$("#temp").value;
  if (!(nsteps > 0) || !(deltat > 0) || !(temp >= 0)) { toast("Give the number of steps, the time step and the temperature."); return; }
  // About two hundred points in the plot and fifty frames in the viewer
  const options = { nsteps, deltat, temp, minimize: true, nstener: Math.max(1, Math.round(nsteps / 200)),
                    nstxout: Math.max(1, Math.round(nsteps / 50)) };
  const button = $("#run-sim"), stop = $("#stop-sim");
  button.disabled = true; stop.hidden = false; alertBox("");
  state.sim.points = []; state.sim.pdb = null; state.sim.nsteps = nsteps;
  state.jobs.sim = null; $("#e-pdf").hidden = true;
  $("#simbar").style.width = "0";
  scene();
  try {
    const job = await runJob("simulate", options, j => {
      state.sim.job = j.id;
      if (j.progress.length) {
        state.sim.points.push(...j.progress);
        const last = state.sim.points[state.sim.points.length - 1];
        $("#simbar").style.width = Math.min(100, 100 * last.step / nsteps) + "%";
        $("#simtime").textContent = `${last.time.value.toFixed(3)} ps · ${last.temperature.value.toFixed(0)} K`;
        energyPlot();
      } else if (j.status === "running" && !state.sim.points.length) {
        $("#simtime").textContent = "Minimizing first…";
      }
    });
    $("#simbar").style.width = "100%";
    state.sim.pdb = await (await call(`/jobs/${job.id}/files/o`)).text();
    state.jobs.sim = job.id; $("#e-pdf").hidden = !session.pdf;
    scene();
  } catch (e) {
    if (e.message !== "The calculation was stopped") alertBox("The simulation failed. " + e.message);
    $("#simtime").textContent = e.message === "The calculation was stopped" ? "Stopped" : "";
  } finally { button.disabled = false; stop.hidden = true; state.sim.job = null; }
}
$("#stop-sim").onclick = () => { if (state.sim.job) call("/jobs/" + state.sim.job, { method: "DELETE" }).catch(() => {}); };

/* ---------- scan of the distance in a dimer ---------- */

/**
 * The compounds of the molecule: the atoms that bonds connect, the first compound first.
 * @returns {number[][]} For each compound the places of its atoms.
 */
function compoundsOf() {
  const n = state.mol.atoms.length, group = Array.from({ length: n }, (_, i) => i);
  const find = i => (group[i] === i ? i : (group[i] = find(group[i])));
  for (const [i, j] of state.mol.bonds) group[find(i)] = find(j);
  const parts = new Map();
  for (let i = 0; i < n; i++) {
    const g = find(i);
    if (!parts.has(g)) parts.set(g, []);
    parts.get(g).push(i);
  }
  return [...parts.values()];
}

/** Offer the atoms of the two compounds, the closest pair first chosen, or say why no scan can be done. */
function scanForm() {
  const parts = state.mol ? compoundsOf() : [];
  state.scan = { parts, points: [], job: null, running: null, picked: -1, pdb: null };
  $("#scanplot").textContent = ""; $("#scan-plotnote").textContent = ""; $("#scan-xvg").hidden = true; pill("scan-pill", "");
  const ok = parts.length === 2;
  $("#run-scan").disabled = !ok;
  $("#scan-note").textContent = !state.mol ? "" : ok ? `The dimer has compounds of ${parts[0].length} and ${parts[1].length} atoms.`
    : `A scan needs a structure of two compounds; this one has ${parts.length}. Load a dimer, for instance a pdb file with both compounds.`;
  for (const [k, id] of [[0, "scan-atom1"], [1, "scan-atom2"]]) {
    const select = $("#" + id);
    select.textContent = "";
    for (const i of parts[k] || []) select.append(el("option", { value: String(i + 1) }, `${state.mol.atoms[i].elem}${i + 1} (${state.mol.atoms[i].type})`));
  }
  if (!ok) return;
  // The closest pair of atoms, as the script takes it
  let best = [parts[0][0], parts[1][0]], d2 = Infinity;
  for (const i of parts[0]) for (const j of parts[1]) {
    const d = state.xyz[i].reduce((s, v, k) => s + (v - state.xyz[j][k]) ** 2, 0);
    if (d < d2) { d2 = d; best = [i, j]; }
  }
  $("#scan-atom1").value = String(best[0] + 1);
  $("#scan-atom2").value = String(best[1] + 1);
}

/**
 * Mark the two atoms of the scan in the viewer.
 * @param {number[][]} xyz The positions of the atoms in Å.
 */
function scanAtoms(xyz) {
  for (const id of ["scan-atom1", "scan-atom2"]) {
    const c = xyz[Number($("#" + id).value) - 1];
    if (c) V.addSphere({ center: { x: c[0], y: c[1], z: c[2] }, radius: 0.62, color: css("--accent"), opacity: 0.35 });
  }
}

/** Draw the interaction energy against the distance, as far as the scan is. */
function scanPlot() {
  const done = state.scan.points.filter(p => p.energies && p.energies.EPOT !== undefined);
  if (!done.length) { $("#scanplot").textContent = ""; return; }
  const x = done.map(p => p.distance), y = done.map(p => p.energies.EPOT);
  const marked = state.scan.picked >= 0 ? { series: 1, id: state.scan.picked } : null;
  plot("scanplot", { label: "Interaction energy against distance", xlim: limits(x, 0.04), ylim: limits(y, 0.1), height: 300,
                     xlabel: "Distance (nm)", ylabel: "Interaction energy (kJ/mol)", zoom: true, marked,
                     series: [{ type: "line", x, y }, { type: "points", x, y, ids: done.map(p => p.index), size: 7, pickable: true }],
                     onPoint: (_, index) => scanStructure(index) });
  const failed = state.scan.points.length - done.length;
  $("#scan-plotnote").textContent = `${done.length} ${done.length === 1 ? "distance" : "distances"}` +
    (failed ? `, ${failed} of which the minimization failed` : "") + ". Click a point to see the minimized dimer at that distance.";
}

/**
 * Show the minimized dimer of a point of the scan.
 * @param {number} index The place of the distance.
 */
async function scanStructure(index) {
  const job = state.scan.job;
  if (!job) return;
  try {
    const pdb = await (await call(`/jobs/${job}/scan/${index}`)).text();
    if (state.scan.job !== job) return;
    state.scan.picked = index; state.scan.pdb = pdb;
    markPoint("scanplot", { series: 1, id: index });
    scene();
  } catch (e) { alertBox("The structure at this distance could not be fetched. " + e.message); }
}

/** Run the scan and follow it. */
async function runScan() {
  const scan = { atom1: Number($("#scan-atom1").value), atom2: Number($("#scan-atom2").value), rmin: Number($("#scan-rmin").value),
                 rmax: Number($("#scan-rmax").value), step: Number($("#scan-step").value), constrain: $("#scan-constrain").checked };
  if (!(scan.rmin > 0 && scan.rmax > scan.rmin && scan.step > 0)) { toast("Give the distances: from, to and the step, in nm."); return; }
  const button = $("#run-scan"), stop = $("#stop-scan");
  button.disabled = true; stop.hidden = false; alertBox("");
  Object.assign(state.scan, { points: [], job: null, picked: -1, pdb: null });
  $("#scan-xvg").hidden = true;
  scanPlot(); scene();
  pill("scan-pill", "Running…", "warn");
  try {
    const job = await runJob("dimer_scan", {}, j => {
      state.scan.job = j.id; state.scan.running = j.id;
      if (j.progress.length) { state.scan.points.push(...j.progress); scanPlot(); }
    }, { scan });
    state.scan.points = job.result.points;
    scanPlot();
    $("#scan-xvg").hidden = false;
    pill("scan-pill", "Done", "good");
  } catch (e) {
    const stopped = e.message === "The calculation was stopped";
    pill("scan-pill", stopped ? "Stopped" : "Failed", stopped ? "warn" : "bad");
    if (!stopped) alertBox("The scan failed. " + e.message);
  } finally { button.disabled = state.scan.parts.length !== 2; stop.hidden = true; state.scan.running = null; }
}
$("#run-scan").onclick = runScan;
$("#stop-scan").onclick = () => { if (state.scan.running) call("/jobs/" + state.scan.running, { method: "DELETE" }).catch(() => {}); };
$("#scan-xvg").onclick = e => { if (state.scan.job) saveFile(e.currentTarget, `/jobs/${state.scan.job}/files/xvg`, {}, "Saving…", "dimer-scan.xvg"); };
for (const id of ["scan-atom1", "scan-atom2"]) $("#" + id).onchange = () => { if (state.ui.tab === "scan") scene(); };
seg($("#eseries"), v => { state.sim.series = v; energyPlot(); });

/* ---------- files for OpenMM ---------- */

/** Make the force field and the coordinates for OpenMM and save the archive with them. */
async function openmmPackage() {
  const steps = Math.round(+$("#omm-steps").value), dt = +$("#omm-dt").value, temperature = +$("#omm-temp").value;
  if (!(steps > 0) || !(dt > 0) || !(temperature >= 0)) { toast("Give the number of steps, the time step and the temperature."); return; }
  const button = $("#run-omm");
  alertBox("");
  try {
    // The files of the molecule are made once, the settings go into each archive
    if (!state.jobs.openmm) {
      button.disabled = true; pill("omm-pill", "Running…", "warn");
      state.jobs.openmm = (await runJob("openmm")).id;
    }
    const saved = await saveFile(button, `/jobs/${state.jobs.openmm}/package`, { steps, dt, temperature }, "Making archive…", "act-openmm.zip");
    pill("omm-pill", saved ? "Saved" : "Failed", saved ? "good" : "bad");
  } catch (e) {
    pill("omm-pill", "Failed", "bad"); alertBox("The files for OpenMM could not be made. " + e.message);
  } finally { button.disabled = false; }
}

/* ---------- wiring ---------- */

seg($("#style"), v => { state.ui.style = v; scene(); });
seg($("#colour"), v => { state.ui.colour = v; scene(); });
$("#labels").onchange = e => { state.ui.labels = e.target.checked; scene(); };
$("#ghost").onchange = e => { state.ui.ghost = e.target.checked; scene(); };
$("#reset").onclick = () => { zoomed = false; scene(); };
$("#qtable").onclick = e => {
  const r = e.target.closest("tr.pick");
  if (!r) return;
  state.ui.focus = state.ui.focus === +r.dataset.i ? -1 : +r.dataset.i;
  chargeTable(); scene();
};
$("#load").onclick = loadMolecule;
$("#smiles").addEventListener("keydown", e => { if (e.key === "Enter") loadMolecule(); });
$("#smiles").addEventListener("input", () => { state.pendingFile = null; $("#upload").textContent = "Upload molecule file…"; });
$("#upload").onclick = () => $("#molfile").click();
$("#molfile").onchange = e => {
  const file = e.target.files[0];
  if (!file) return;
  state.pendingFile = file;
  $("#upload").textContent = file.name;
  $("#smiles").value = "";
  loadMolecule();
};
$("#ffsel").onchange = e => {
  if (e.target.value === "upload") {
    // The file that was opened, sent once
    const own = state.ownForcefield;
    if (own && own.ref) { state.forcefield = { ref: own.ref, label: own.label }; state.pendingForcefield = null; }
    else if (own) state.pendingForcefield = own.file;
    return;
  }
  state.pendingForcefield = null;
  // The default, or the one that was made or opened in the force field workspace
  state.forcefield = e.target.value === "workspace" && session.forcefield
    ? { ref: session.forcefield.ref, label: session.forcefield.label } : {};
};
// The force field of the force field workspace can be used for molecules
document.addEventListener("act-forcefield", () => {
  const option = $('#ffsel option[value="workspace"]'), current = session.forcefield;
  option.hidden = option.disabled = !current;
  if (current) option.textContent = "From the force field tab: " + current.label;
  if ($("#ffsel").value === "workspace") {
    if (current) state.forcefield = { ref: current.ref, label: current.label };
    else { $("#ffsel").value = "default"; state.forcefield = {}; }
  }
});
// A file of the user, chosen with a button: a choice in the list cannot
// open the file chooser in every browser
$("#ffopen").onclick = () => $("#fffile").click();
$("#fffile").onchange = e => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  state.ownForcefield = { file, ref: null, label: file.name };
  state.pendingForcefield = file;
  const option = $('#ffsel option[value="upload"]');
  option.textContent = file.name;
  option.hidden = option.disabled = false;
  $("#ffsel").value = "upload";
};
$("#run-min").onclick = minimize;
$("#run-nma").onclick = normalModes;
$("#run-sim").onclick = simulate;
$("#run-omm").onclick = openmmPackage;
// The plot on paper has the series that is on the screen
$("#ir-pdf").onclick = e => savePdf(e.currentTarget, state.jobs.nma, "nma", "ir", "", state.ui.reverse);
// The spectrum itself, as the program wrote it
$("#ir-xvg").onclick = e => { if (state.jobs.nma) saveFile(e.currentTarget, `/jobs/${state.jobs.nma}/files/ir`, {}, "Saving…", "nma-ir.xvg"); };

/**
 * Set the direction of the wavenumber axis of the spectrum, on the screen
 * and in the PDF file, and remember it in this browser.
 * @param {boolean} reverse Whether the wavenumbers run from high to low.
 */
function reverseSpectrum(reverse) {
  state.ui.reverse = reverse;
  $("#ir-flip").setAttribute("aria-pressed", reverse);
  try { localStorage.setItem("act-spectrum-reverse", reverse ? "1" : "0"); } catch (e) { /* no storage, no memory */ }
  modesPanel();
}
$("#ir-flip").onclick = () => reverseSpectrum(!state.ui.reverse);
try {
  if (localStorage.getItem("act-spectrum-reverse") === "1") reverseSpectrum(true);
} catch (e) { /* no storage */ }
$("#e-pdf").onclick = e => savePdf(e.currentTarget, state.jobs.sim, "simulate", "e",
  state.sim.series === "T" ? "TEMPERATURE" : state.sim.series);

const TABS = { charges: "tab-charges", minimize: "tab-minimize", modes: "tab-modes", simulate: "tab-simulate", openmm: "tab-openmm", scan: "tab-scan" };
tabs($("#tasks"), name => { state.ui.tab = name; showOnly(Object.values(TABS), TABS[name]); scene(); });
const VIEWS = { molecule: "view-molecule", "force-field": "view-force-field", molprop: "view-molprop", cluster: "view-cluster" };
tabs($("#nav"), name => { showOnly(Object.values(VIEWS), VIEWS[name]); if (name === "molecule") scene(); else if (V) V.stopAnimate(); });
window.addEventListener("resize", () => { redrawPlots(); $$(".tabs").forEach(moveInk); if (V) { V.resize(); V.render(); } });
if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { redrawPlots(); scene(); });

/** Ask the server who we are and what it offers. */
async function start() {
  $$(".tabs").forEach(moveInk);
  try {
    const told = await (await fetch("/api/session", { credentials: "same-origin" })).json();
    if (!told.token) throw new Error(told.detail || "The server does not know this browser.");
    session.token = told.token;
    state.defaultForcefield = told.default_forcefield;
    session.pdf = !!told.pdf;
    $('#ffsel option[value="default"]').textContent = told.default_forcefield;
    $("#smiles").maxLength = told.max_smiles_length;
    $("#status-text").textContent = (told.mode === "desktop" ? "Desktop mode" : "Server") + " · version " + told.version;
    startForceFields(told);
    startMolprops(told);
    // The tab of the cluster is there on the desktop only
    startCluster(told).then(() => $$(".tabs").forEach(moveInk));
    // Open a molecule that is named in the address, as in #smiles=CCO
    const wanted = new URLSearchParams(location.hash.slice(1)).get("smiles");
    if (wanted) { $("#smiles").value = wanted; }
    if (wanted || new URLSearchParams(location.hash.slice(1)).has("load")) await loadMolecule();
  } catch (e) {
    $("#status-text").textContent = "Not connected";
    alertBox(e.message);
  }
}
start();
