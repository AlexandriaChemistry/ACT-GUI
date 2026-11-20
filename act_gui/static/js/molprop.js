/*
 * This source file is part of the Alexandria Chemistry Toolkit.
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; either version 2
 * of the License, or (at your option) any later version.
 */
/**
 * @file The molprop workspace: what is in a file with compounds and their
 * properties, whether the file is consistent, and how well a force field
 * reproduces its data.
 *
 * All text that comes from the server or from files is put on the page as
 * text, never as markup.
 */
import { $, $$, alertBox, call, el, num, pill, runTask, saveFile, seg, session, toast, upload } from "./common.js";
import { limits, plot } from "./plot.js";

/** What the workspace knows. */
const state = {
  data: null,     // the file as edit_mp describes it: summary, molecules and validation
  query: "",      // the text that the name or the formula of a compound must contain
  picked: -1,     // the compound of which the details are shown
  file: null,     // the molprop file on the server: { file, described, label }
  observables: [],  // the properties that a force field can be compared on
  pendingFF: null,  // a force field file that is uploaded when the comparison starts
  ev: null,       // the comparison: { job, files, sets }, sets by the name of the file
  job: null,      // the request for the job on the cluster that is shown, before it is sent
  training: { optimizers: [], max_test: 0.5 },  // what the server says about training
  maxProcesses: 1,  // the most processes of a calculation on this computer
  limits: {},     // the smallest and largest values of options, by task
  tr: { points: [], job: null, running: null },  // the training: its progress, the job when it is done and while it runs
};

/** The most compounds that are put in the table at once. */
const MAX_ROWS = 300;
/** The most messages of the validation that are listed. */
const MAX_MESSAGES = 40;
/** The most points of one data set that are drawn. */
const MAX_POINTS = 5000;
/** What the page calls the files with a comparison. */
const PLOT_NAMES = {
  epotcorr: "Potential energy", forcecorr: "Forces", freqcorr: "Vibrational frequencies", espcorr: "Electrostatic potential",
  dipolecorr: "Dipole", quadrupolecorr: "Quadrupole", alphacorr: "Polarizability", isopol: "Isotropic polarizability",
  anisopol: "Anisotropic polarizability", INTERACTION: "Interaction energy", COULOMB: "Electrostatics",
  ALLELEC: "Electrostatics and induction", INDUCTION: "Induction", EXCHANGE: "Exchange", DISPERSION: "Dispersion",
  EXCHIND: "Exchange and induction", EXCHDISP: "Exchange and dispersion",
};
/** The colours of the data sets in a plot. */
const SET_COLOURS = ["--accent", "--warn", "--series2"];

/**
 * Fill a table with two columns: a name and a number.
 * @param {Element} table  The table.
 * @param {string[]} heads The two headers.
 * @param {object} counts  The numbers by name.
 */
function countTable(table, heads, counts) {
  table.textContent = "";
  table.append(el("thead", {}, el("tr", {}, el("th", {}, heads[0]), el("th", { class: "n" }, heads[1]))));
  const body = el("tbody");
  for (const [name, n] of Object.entries(counts)) body.append(el("tr", {}, el("td", {}, name), el("td", { class: "n" }, String(n))));
  table.append(body);
}

/**
 * The compounds that the table shows.
 * @returns {object[]} For each compound its place in the file and its description.
 */
function compoundsShown() {
  const query = state.query.trim().toLowerCase();
  const all = state.data.molecules.map((molecule, index) => ({ index, molecule }));
  return query ? all.filter(({ molecule: m }) => (m.name + " " + m.iupac + " " + m.formula).toLowerCase().includes(query)) : all;
}

/** Fill the table of compounds. */
function compoundTable() {
  const table = $("#mpw-table"), found = compoundsShown(), shown = found.slice(0, MAX_ROWS);
  table.textContent = "";
  $("#mpw-count").textContent = found.length > shown.length
    ? `the first ${shown.length} of ${found.length}, find a name to see others`
    : `${found.length} ${found.length === 1 ? "compound" : "compounds"}`;
  $("#mpw-empty").hidden = shown.length > 0;
  if (!shown.length) return;
  table.append(el("thead", {}, el("tr", {}, el("th", {}, "Name"), el("th", {}, "Formula"), el("th", { class: "n" }, "Atoms"),
    el("th", { class: "n" }, "Charge"), el("th", { class: "n" }, "Compounds"), el("th", { class: "n" }, "Calculations"), el("th", {}, "Methods"))));
  const body = el("tbody"), invalid = new Set(state.data.validation.invalid || []);
  for (const { index, molecule: m } of shown) {
    const bad = invalid.has(m.name) || invalid.has(m.iupac);
    body.append(el("tr", { class: "pick" + (index === state.picked ? " sel" : ""), data: { i: String(index) } },
      el("td", {}, m.name || m.iupac, bad ? el("span", { class: "pill bad ml8" }, "problem") : ""), el("td", { class: "num" }, m.formula),
      el("td", { class: "n" }, String(m.natom)), el("td", { class: "n" }, String(m.charge)), el("td", { class: "n" }, String(m.fragments.length)),
      el("td", { class: "n" }, String(m.experiments)), el("td", {}, m.methods.join(", "))));
  }
  table.append(body);
}

/** Show the details of the compound that was picked, or hide them. */
function compoundDetail() {
  const card = $("#mpw-detail"), m = state.picked >= 0 ? state.data.molecules[state.picked] : null;
  card.hidden = !m;
  if (!m) return;
  $("#mpw-dname").textContent = m.name || m.iupac;
  const chips = $("#mpw-dchips");
  chips.textContent = "";
  const add = (label, value) => chips.append(el("span", { class: "chip" }, label ? label + " " : "", el("b", {}, value)));
  if (m.iupac && m.iupac !== m.name) add("", m.iupac);
  add("", m.formula);
  add("", `${m.natom} atoms`);
  add("charge", `${m.charge} e`);
  for (const [job, n] of Object.entries(m.jobtypes)) add(job, String(n));
  const frag = $("#mpw-dfrag");
  frag.textContent = "";
  frag.append(el("thead", {}, el("tr", {}, el("th", {}, "Compound"), el("th", { class: "n" }, "Atoms"), el("th", { class: "n" }, "Charge"),
    el("th", { class: "n" }, "Multiplicity"), el("th", {}, "InChI"))));
  const body = el("tbody");
  for (const f of m.fragments) {
    body.append(el("tr", {}, el("td", { class: "num" }, f.formula), el("td", { class: "n" }, String(f.natom)), el("td", { class: "n" }, String(f.charge)),
      el("td", { class: "n" }, String(f.multiplicity)), el("td", { class: "num" }, f.inchi)));
  }
  frag.append(body);
  countTable($("#mpw-dprop"), ["Property", "Values"], m.properties);
}

/** Show the result of the validation. */
function validation() {
  const result = state.data.validation, list = $("#mpw-vlist");
  list.textContent = "";
  const item = (mark, kind, ...content) => list.append(el("li", {}, el("span", { class: kind }, mark), el("span", {}, ...content)));
  if (!result.checked) {
    item("!", "wr", "The file was not checked.");
    pill("mpw-vpill", "Not checked", "warn");
  } else if (result.ok) {
    item("✓", "ok", "No problems found.");
    pill("mpw-vpill", "Valid", "good");
  } else {
    const names = result.invalid || [];
    if (names.length) item("✕", "er", `${names.length} ${names.length === 1 ? "compound has" : "compounds have"} problems: ` + names.slice(0, 20).join(", ") + (names.length > 20 ? " and others" : ""));
    for (const message of (result.messages || []).slice(0, MAX_MESSAGES)) {
      item(message.level === "Error" || message.level === "Fatal" ? "✕" : "!", message.level === "Warning" ? "wr" : "er",
           el("span", { class: "what" }, message.kind), ": " + message.text);
    }
    if ((result.messages || []).length > MAX_MESSAGES) list.append(el("li", { class: "more" }, `and ${result.messages.length - MAX_MESSAGES} more messages`));
    pill("mpw-vpill", `${result.problems} ${result.problems === 1 ? "problem" : "problems"}`, "bad");
  }
}

/* ---------- comparing a force field ---------- */

/** Offer the properties that the data of the file can be compared on. */
function observableBoxes() {
  const host = $("#ev-observables"), s = state.data.summary;
  host.textContent = "";
  const dimers = s.dimers + s.larger > 0, monomers = s.monomers > 0;
  let first = true;
  for (const o of state.observables) {
    if (o.dimer ? !dimers : !monomers) continue;
    // The first of them is what most files are for
    host.append(el("label", { class: "check" }, el("input", { type: "checkbox", data: { name: o.name }, checked: first }), " " + o.label));
    first = false;
  }
}

/**
 * Read a file in which sets of points are separated by lines with "&".
 * @param {string} text The content of an xvg file.
 * @returns {object} xlabel, legends and for each set its x and y values.
 */
function parseSets(text) {
  const out = { xlabel: "", legends: [], sets: [] };
  let current = { x: [], y: [] };
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line || line[0] === "#") continue;
    if (line[0] === "@") {
      const label = /^@\s*xaxis\s+label\s+"([^"]*)"/.exec(line), legend = /^@\s*s\d+\s+legend\s+"([^"]*)"/.exec(line);
      if (label) out.xlabel = label[1];
      if (legend) out.legends.push(legend[1]);
    } else if (line[0] === "&") {
      out.sets.push(current);
      current = { x: [], y: [] };
    } else {
      const [x, y] = line.split(/\s+/).map(Number);
      if (isFinite(x) && isFinite(y)) { current.x.push(x); current.y.push(y); }
    }
  }
  if (current.x.length) out.sets.push(current);
  out.sets = out.sets.filter(set => set.x.length);
  return out;
}

/** Draw the comparison that is chosen: the force field against the reference. */
async function comparisonPlot() {
  const ev = state.ev, option = $("#ev-plotsel").value;
  if (!ev || !option) return;
  if (!ev.sets[option]) {
    try {
      ev.sets[option] = parseSets(await (await call(`/jobs/${ev.job}/files/${option}`)).text());
    } catch (e) { alertBox("The data could not be fetched. " + e.message); return; }
  }
  const data = ev.sets[option];
  if ($("#ev-plotsel").value !== option) return;
  const all = data.sets.flatMap(set => set.x.concat(set.y));
  const lim = limits(all.length ? all : [0, 1], 0.05);
  let thinned = false;
  const series = [{ type: "line", x: lim, y: lim, color: "--muted", width: 1, dash: true }];
  data.sets.forEach((set, i) => {
    // Of very many points every so-maniest is drawn
    const step = Math.ceil(set.x.length / MAX_POINTS);
    thinned = thinned || step > 1;
    const keep = (_, j) => j % step === 0;
    series.push({ type: "points", x: set.x.filter(keep), y: set.y.filter(keep), color: SET_COLOURS[i % SET_COLOURS.length] });
  });
  const name = PLOT_NAMES[option] || option;
  plot("evplot", { label: `${name}: force field against reference`, xlim: lim, ylim: lim, height: 560, aspect: 1,
                   xlabel: "Reference: " + (data.xlabel || name), ylabel: "Force field", series });
  const counts = data.sets.map((set, i) => `${set.x.length} ${data.legends[i] || "points"}`).join(", ");
  $("#ev-plotnote").textContent = `${counts}. The dashed line is where the force field equals the reference.` +
    (thinned ? ` Of each set at most ${MAX_POINTS} points are drawn; the PDF file and the data have all.` : "");
}

/**
 * Show the result of a comparison.
 * @param {object} job    The calculation.
 * @param {string} source Where it ran, if not on this computer.
 */
function compared(job, source = "") {
  $("#ev-source").textContent = source;
  // In the order of the list of names, the main property first
  const result = job.result, files = Object.keys(PLOT_NAMES).filter(name => job.files.includes(name));
  state.ev = { job: job.id, files, sets: {} };
  const table = $("#ev-stats");
  table.textContent = "";
  table.append(el("thead", {}, el("tr", {}, el("th", {}, "Property"), el("th", {}, "Data set"), el("th", { class: "n" }, "N"),
    el("th", { class: "n" }, "RMSD"), el("th", { class: "n" }, "MSE"), el("th", { class: "n" }, "MAE"), el("th", {}, "Unit"),
    el("th", { class: "n" }, "Slope"), el("th", { class: "n" }, "R"))));
  const body = el("tbody");
  for (const [dataset, values] of Object.entries(result.statistics)) {
    for (const [property, v] of Object.entries(values)) {
      body.append(el("tr", {}, el("td", {}, property), el("td", {}, dataset), el("td", { class: "n" }, String(v.N)),
        el("td", { class: "n" }, num(v.RMSD, 4)), el("td", { class: "n" }, num(v.MSE, 4)), el("td", { class: "n" }, num(v.MAE, 4)),
        el("td", {}, v.unit || ""), el("td", { class: "n" }, num(v.slope, 3)), el("td", { class: "n" }, num(v.Rfit, 3))));
    }
  }
  table.append(body);
  $("#ev-ncomp").textContent = `${result.compounds.length} ${result.compounds.length === 1 ? "compound" : "compounds"}`;
  const select = $("#ev-plotsel");
  select.textContent = "";
  for (const name of files) select.append(el("option", { value: name }, PLOT_NAMES[name]));
  $("#ev-pdf").hidden = !session.pdf || !files.length;
  $("#ev-data").hidden = !files.length;
  $("#ev-result").hidden = false;
  if (files.length) comparisonPlot();
  else { $("#evplot").textContent = ""; $("#ev-plotnote").textContent = "The file has no data for the properties that were chosen."; }
}

/**
 * What the comparison needs: the force field, sent to the server if it is
 * a file of the user, the molprop file and the properties.
 * @returns {Promise<object|null>} The request, null if something has to be chosen first.
 */
async function comparison() {
  const observables = $$("#ev-observables input").filter(b => b.checked).map(b => b.dataset.name);
  if (!observables.length) { toast("Choose at least one property to compare."); return null; }
  const choice = $("#ev-ff").value;
  let forcefield = { default: true };
  if (choice === "workspace" && session.forcefield) forcefield = session.forcefield.ref;
  else if (choice === "upload") {
    if (!state.pendingFF) { toast("Choose a force field file first."); return null; }
    if (!state.pendingFF.id) {
      pill("ev-pill", "Sending the force field…");
      state.pendingFF.id = (await upload(state.pendingFF.file, "forcefield")).id;
    }
    forcefield = { file: state.pendingFF.id };
  }
  return { task: "evaluate", forcefield, observables, molprop: { file: state.file.file, described: state.file.described } };
}

/* ---------- training a force field ---------- */

/**
 * Whether the force field is trained, not only compared.
 * @returns {boolean} True if the user chose to train.
 */
const training = () => $('#ev-what button[aria-pressed="true"]').dataset.v === "train";

/**
 * The optimizer that is chosen.
 * @returns {object} Its name and whether it needs more than one process.
 */
const optimizer = () => state.training.optimizers.find(o => o.name === $("#tr-optimizer").value) || { name: "", parallel: false };

/**
 * A whole number from a field of the form.
 * @param {string} id   The id of the field.
 * @param {number} low  The smallest value.
 * @param {number} high The largest value.
 * @returns {number|null} The number, null if the field has no whole number within the limits.
 */
function whole(id, low, high) {
  const value = Number($(id).value);
  return $(id).value.trim() !== "" && Number.isInteger(value) && value >= low && value <= high ? value : null;
}

/**
 * What the training adds to the request for a comparison.
 * @param {object} body The request for the comparison.
 * @returns {object|null} The request, null if something has to be given first.
 */
function trainingRequest(body) {
  const fit = $("#tr-fit").value.trim().split(/[\s,]+/).filter(Boolean);
  if (!fit.length) { toast("Give the kinds of parameters to train, for instance sigma epsilon."); return null; }
  const chosen = optimizer(), limit = (state.limits || {}).train || {};
  const iterations = whole("#tr-iter", 1, (limit.max_iter || [1, 100000])[1]);
  if (iterations === null) { toast("Give the number of Monte Carlo iterations."); return null; }
  const options = { optimizer: chosen.name, max_iter: iterations, random_init: $("#tr-random").checked };
  if (chosen.parallel) {
    options.max_generations = whole("#tr-gen", 1, (limit.max_generations || [1, 10000])[1]);
    if (options.max_generations === null) { toast("Give the number of generations."); return null; }
  }
  if ($("#tr-seed").value.trim() !== "") {
    options.seed = whole("#tr-seed", 1, 2147483647);
    if (options.seed === null) { toast("The random seed is a whole number above zero."); return null; }
  }
  const processes = whole("#tr-procs", chosen.parallel ? 2 : 1, state.maxProcesses);
  if (processes === null) {
    toast(chosen.parallel ? `${chosen.name} needs between 2 and ${state.maxProcesses} processes.` : `Give between 1 and ${state.maxProcesses} processes.`);
    return null;
  }
  const percent = Number($("#tr-test").value || 0);
  if (!(percent >= 0 && percent <= 100 * state.training.max_test)) { toast(`At most ${100 * state.training.max_test} % of the compounds can be kept for testing.`); return null; }
  return { ...body, task: "train", options, processes, training: { fit, test: percent / 100 } };
}

/** Plot the chi squared of a training as far as it is, and tell how far that is. */
function trainingPlot() {
  const points = state.tr.points, start = points.find(p => p.event === "start");
  if (!start) return;
  const generations = points.filter(p => p.event === "generation"), steps = points.filter(p => p.event === "step");
  const last = points[points.length - 1], done = generations.length ? generations[generations.length - 1] : null;
  const series = [];
  let xlabel, xmax, fraction;
  if (start.ngenerations > 1) {
    // The lowest chi squared of all individuals after each generation
    const x = [0, ...generations.map(g => g.generation)];
    series.push({ type: "line", x, y: [start.chi2.train, ...generations.map(g => g.best.train)], endpoint: true });
    if (start.chi2.test !== undefined && generations.every(g => g.best.test !== undefined)) {
      series.push({ type: "line", x, y: [start.chi2.test, ...generations.map(g => g.best.test)], color: "--warn" });
    }
    xlabel = "Generation"; xmax = start.ngenerations;
    fraction = (done ? done.generation : 0) / start.ngenerations;
    $("#tr-plotnote").textContent = "The lowest χ² of all individuals on the compounds for training" + (series.length > 1 ? ", and in the other colour the χ² of that individual on the compounds for testing." : ".");
  } else {
    // The chain of this process, and the lowest chi squared it found
    const x = [0, ...steps.map(s => s.iteration)], best = [start.chi2.train, ...steps.map(s => s.best.train)];
    // The chain is known where a step was reported, the best also at the end
    if (steps.length > 1) series.push({ type: "line", x: x.slice(), y: [start.chi2.train, ...steps.map(s => s.chi2.train)], color: "--muted", width: 1 });
    if (done) { x.push(start.niterations); best.push(done.best.train); }
    series.push({ type: "line", x, y: best, endpoint: true });
    xlabel = "Monte Carlo iteration"; xmax = start.niterations;
    fraction = done ? 1 : (steps.length ? steps[steps.length - 1].iteration / start.niterations : 0);
    $("#tr-plotnote").textContent = "The lowest χ² so far on the compounds for training" + (series.length > 1 ? ", and in grey the χ² of the chain of the first process." : ".");
  }
  $("#tr-bar").style.width = Math.min(100, 100 * fraction) + "%";
  const lowest = done ? done.best.train : (steps.length ? steps[steps.length - 1].best.train : start.chi2.train);
  const where = start.ngenerations > 1 ? `generation ${done ? done.generation : 0} of ${start.ngenerations}` : `${start.niterations} iterations`;
  $("#tr-status").textContent = `${start.optimizer} · ${where} · χ² ${num(lowest, 5)}` + (last.elapsed ? ` · ${num(last.elapsed.value, 3)} s` : "");
  // On a logarithmic axis when the values differ much, as they do from a random start
  const all = series.flatMap(s => s.y), low = Math.min(...all), log = low > 0 && Math.max(...all) / low > 50;
  if (log) for (const s of series) s.y = s.y.map(Math.log10);
  const short = v => Math.abs(v) >= 10000 || (v !== 0 && Math.abs(v) < 0.01) ? v.toExponential(1).replace("+", "") : num(v, 3);
  plot("trplot", { label: "χ² during the training", xlim: [0, Math.max(xmax, 1)], ylim: limits(series.flatMap(s => s.y)), xlabel,
                   ylabel: log ? "χ² (logarithmic)" : "χ²", height: 260, yfmt: v => short(log ? Math.pow(10, v) : v), series });
}

/**
 * Show the parameters of a training.
 * @param {object} job The calculation.
 */
function trained(job) {
  const result = job.result, table = $("#tr-params"), spread = result.parameters.some(p => p.stdev);
  state.tr.job = job.id;
  table.textContent = "";
  const heads = ["Kind", "Name", "Best"].concat(spread ? ["Mean", "Standard deviation", "Lowest", "Highest"] : []);
  table.append(el("thead", {}, el("tr", {}, ...heads.map((h, i) => el("th", i > 1 ? { class: "n" } : {}, h)))));
  const body = el("tbody");
  for (const p of result.parameters) {
    const cells = [el("td", {}, p.class || ""), el("td", { class: "mono" }, p.name || ""), el("td", { class: "n" }, num(p.best, 6))];
    if (spread) for (const key of ["mean", "stdev", "min", "max"]) cells.push(el("td", { class: "n" }, p[key] === null || p[key] === undefined ? "" : num(p[key], 6)));
    body.append(el("tr", {}, ...cells));
  }
  table.append(body);
  $("#tr-count").textContent = `${result.parameters.length} ${result.parameters.length === 1 ? "parameter" : "parameters"}`;
  const s = result.sensitivity || {};
  $("#tr-sens").textContent = s.n_parameters === undefined ? ""
    : `Sensitivity analysis (${s.mode}): ${s.n_parameters_converged} of ${s.n_parameters} parameters are at a minimum of χ²` +
      (s.minimum === "yes" ? ", the force field is in a local minimum." : ", the force field is not in a local minimum: more iterations may improve it.");
  $("#tr-result").hidden = false;
}

/**
 * Train a force field on this computer and show how it goes.
 * @param {object} body The request.
 * @returns {Promise<object>} The calculation when it is done.
 */
async function runTraining(body) {
  state.tr = { points: [], job: null, running: null };
  $("#tr-result").hidden = true;
  $("#tr-run").hidden = false;
  $("#tr-bar").style.width = "0";
  $("#tr-status").textContent = "Starting…";
  $("#trplot").textContent = ""; $("#tr-plotnote").textContent = "";
  $("#tr-stop").hidden = false;
  $("#tr-run").scrollIntoView({ behavior: "smooth", block: "nearest" });
  try {
    return await runTask(body, j => {
      state.tr.running = j.id;
      if (j.progress.length) { state.tr.points.push(...j.progress); trainingPlot(); }
    });
  } catch (e) {
    $("#tr-status").textContent = e.message === "The calculation was stopped" ? "Stopped" : "Failed";
    throw e;
  } finally { $("#tr-stop").hidden = true; state.tr.running = null; }
}

/** Show what belongs to comparing and what to training. */
function what() {
  const train = training(), chosen = optimizer();
  $("#ev-about-compare").hidden = train;
  $("#ev-about-train").hidden = !train;
  $("#tr-opts").hidden = !train;
  $("#ev-obslab").textContent = train ? "Properties to train on" : "Properties to compare";
  $("#tr-genfield").hidden = !chosen.parallel;
  if (chosen.parallel && Number($("#tr-procs").value) < 2) $("#tr-procs").value = Math.min(4, state.maxProcesses);
  $("#tr-note").textContent = chosen.parallel
    ? `${chosen.name} has one set of parameters, an individual, for each process, and needs two at least. Each generation every individual does the Monte Carlo iterations${chosen.name === "GA" ? " (not used by GA)" : ""}.`
    : "With more than one process each has its own Monte Carlo chain, and the best of them is kept.";
  places();
}

/**
 * Whether the comparison goes to the cluster.
 * @returns {boolean} True if the user has a cluster and chose it.
 */
const onCluster = () => !$("#ev-where").hidden && $('#ev-place button[aria-pressed="true"]').dataset.v === "cluster";

/**
 * The size of a file in words.
 * @param {number} bytes The number of bytes.
 * @returns {string} The size in bytes, kB or MB.
 */
const fileSize = bytes => bytes < 1024 ? `${bytes} bytes` : bytes < 1048576 ? `${num(bytes / 1024, 3)} kB` : `${num(bytes / 1048576, 3)} MB`;

/**
 * Show the comparison as a job for the cluster, before anything is sent.
 * @param {object} body The request for the comparison.
 */
async function showJob(body) {
  const name = $("#ev-jobname").value.trim();
  const cluster = { tasks: Number($("#ev-tasks").value), minutes: Math.round(Number($("#ev-hours").value) * 60), gpu: $("#ev-gpu").checked };
  if (name) cluster.name = name;
  pill("ev-pill", "");
  const shown = await (await call("/cluster/jobs", { method: "POST", json: { ...body, cluster, send: false } })).json();
  // The name that was shown is the name that is sent
  state.job = { ...body, cluster: { ...cluster, name: shown.name }, send: true };
  $("#ev-jobwhere").textContent = `${shown.name} · ${shown.server}:${shown.directory}`;
  const table = $("#ev-jobfiles");
  table.textContent = "";
  table.append(el("thead", {}, el("tr", {}, el("th", {}, "Name on the cluster"), el("th", { class: "n" }, "Size"))));
  const rows = el("tbody");
  for (const file of shown.files) rows.append(el("tr", {}, el("td", { class: "mono" }, file.name), el("td", { class: "n" }, fileSize(file.size))));
  table.append(rows);
  $("#ev-jobscript").textContent = shown.script;
  $("#ev-jobtaken").hidden = !shown.taken;
  $("#ev-send").disabled = shown.taken;
  pill("ev-jobpill", "");
  $("#ev-job").hidden = false;
  $("#ev-job").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/** Send the job that is shown to the cluster: one login. */
async function sendJob() {
  if (!state.job) return;
  const button = $("#ev-send");
  button.disabled = true;
  alertBox("");
  pill("ev-jobpill", "Logging in and sending…");
  try {
    const sent = await (await call("/cluster/jobs", { method: "POST", json: state.job })).json();
    state.job = null;
    $("#ev-job").hidden = true;
    $("#ev-jobname").value = "";
    pill("ev-pill", "Sent to the cluster", "good");
    toast(`Sent ${sent.name} to the cluster, job ${sent.slurm} of Slurm`);
    // The tab Cluster follows the job from here
    document.dispatchEvent(new CustomEvent("act-cluster-jobs", { detail: { listing: sent, show: true } }));
  } catch (e) {
    pill("ev-jobpill", "Not sent", "bad");
    alertBox("The job was not sent. " + e.message);
    button.disabled = false;
  }
}

/** Show whether the comparison can go to the cluster, and what is asked for then. */
function places() {
  // A training runs on this computer only, for now
  $("#ev-where").hidden = !session.clusterReady || training();
  const cluster = onCluster();
  $("#ev-clusteropts").hidden = !cluster;
  $("#run-ev").textContent = cluster ? "Show the job…" : training() ? "Train" : "Compare";
  if (!cluster) { $("#ev-job").hidden = true; state.job = null; }
}

/** Compare the force field that is chosen with the data of the file, here or as a job on the cluster. */
async function compare() {
  const button = $("#run-ev");
  button.disabled = true;
  alertBox("");
  $("#ev-job").hidden = true;
  state.job = null;
  const train = training();
  try {
    let body = await comparison();
    if (body && train) body = trainingRequest(body);
    if (!body) { pill("ev-pill", ""); return; }
    if (onCluster()) { await showJob(body); return; }
    // What is on the page belongs to the calculation before this one
    state.ev = null;
    $("#ev-result").hidden = true;
    $("#tr-result").hidden = true;
    if (!train) $("#tr-run").hidden = true;
    pill("ev-pill", train ? "Training…" : "Computing…");
    const job = train ? await runTraining(body) : await runTask(body);
    if (!Object.keys(job.result.statistics).length) {
      pill("ev-pill", "Nothing to compare", "warn");
      alertBox("Nothing could be compared: no compound of the file could be used with this force field, or the file has no data for these properties. The log file tells why.");
    } else pill("ev-pill", "Done", "good");
    if (train) trained(job);
    compared(job, train ? "· the trained force field" : "");
  } catch (e) {
    const stopped = e.message === "The calculation was stopped";
    pill("ev-pill", stopped ? "Stopped" : "Failed", stopped ? "warn" : "bad");
    const none = /No molecules to train on/.test(e.message)
      ? " None of the compounds in the file could be used with this force field, for instance because it does not have their atom types." : "";
    const fit = /No parameters to train/.test(e.message)
      ? " The force field has no parameters of these kinds that can be changed: they do not exist, or they are fixed." : "";
    if (!stopped) alertBox((train ? "The force field could not be trained. " : "The force field could not be compared with the data. ") + e.message + "." + none + fit);
  } finally { button.disabled = false; }
}

/**
 * Show a molprop file.
 * @param {string} label The name of the file.
 * @param {object} data  The file as edit_mp describes it.
 */
function show(label, data) {
  state.data = data;
  state.picked = -1;
  state.ev = null;
  $("#ev-result").hidden = true;
  $("#tr-run").hidden = true;
  $("#tr-result").hidden = true;
  $("#ev-card").hidden = false;
  pill("ev-pill", "");
  observableBoxes();
  const chips = $("#mpw-chips"), s = data.summary;
  chips.textContent = "";
  const add = (label2, value) => chips.append(el("span", { class: "chip" }, label2 ? label2 + " " : "", el("b", {}, value)));
  add("", label);
  add("", `${s.molecules} ${s.molecules === 1 ? "entry" : "entries"}`);
  if (s.monomers) add("monomers", String(s.monomers));
  if (s.dimers) add("dimers", String(s.dimers));
  if (s.larger) add("larger", String(s.larger));
  add("calculations", String(s.experiments));
  countTable($("#mpw-props"), ["Property", "Values"], s.properties);
  $("#mpw-content").hidden = false;
  compoundTable();
  compoundDetail();
  validation();
}

/**
 * Make the workspace work. Called when the server has told who we are.
 * @param {object} told What the server said: the limits.
 */
export function startMolprops(told) {
  state.observables = told.observables || [];
  $('#ev-ff option[value="default"]').textContent = told.default_forcefield;
  $("#ev-ff").onchange = e => { if (e.target.value === "upload") $("#ev-fffile").click(); };
  $("#ev-fffile").onchange = e => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) { if (!state.pendingFF) $("#ev-ff").value = "default"; return; }
    state.pendingFF = { file, id: null };
    $('#ev-ff option[value="upload"]').textContent = file.name;
  };
  // The force field of the force field workspace can be compared as well
  document.addEventListener("act-forcefield", () => {
    const option = $('#ev-ff option[value="workspace"]'), current = session.forcefield;
    option.hidden = option.disabled = !current;
    if (current) option.textContent = "From the force field tab: " + current.label;
    else if ($("#ev-ff").value === "workspace") $("#ev-ff").value = "default";
  });
  $("#run-ev").onclick = compare;
  // Training
  state.training = told.training || state.training;
  state.maxProcesses = told.max_processes || 1;
  state.limits = told.limits || {};
  for (const o of state.training.optimizers) {
    $("#tr-optimizer").append(el("option", { value: o.name, selected: o.name === state.training.default_optimizer },
      o.name === "MCMC" ? "Monte Carlo (MCMC)" : o.name === "GA" ? "Genetic algorithm (GA)" : "Genetic algorithm with Monte Carlo (HYBRID)"));
  }
  $("#tr-procs").max = String(state.maxProcesses);
  $("#tr-test").max = String(100 * state.training.max_test);
  seg($("#ev-what"), what);
  $("#tr-optimizer").onchange = what;
  what();
  $("#tr-stop").onclick = () => { if (state.tr.running) call("/jobs/" + state.tr.running, { method: "DELETE" }).catch(() => {}); };
  $("#tr-save").onclick = e => { if (state.tr.job) saveFile(e.currentTarget, `/jobs/${state.tr.job}/files/o`, {}, "Saving…", "trained.xml"); };
  $("#tr-use").onclick = () => {
    if (state.tr.job) document.dispatchEvent(new CustomEvent("act-open-forcefield", { detail: { ref: { job: state.tr.job }, label: "Trained force field" } }));
  };
  // On the cluster, if the user has one
  seg($("#ev-place"), places);
  document.addEventListener("act-cluster", places);
  $("#ev-send").onclick = sendJob;
  $("#ev-nosend").onclick = () => { $("#ev-job").hidden = true; state.job = null; };
  // The results of a job that ran on the cluster, from the tab Cluster
  document.addEventListener("act-show-result", e => {
    if (e.detail.job.task !== "evaluate") return;
    alertBox("");
    compared(e.detail.job, `· job ${e.detail.name} on the cluster`);
    $('#nav button[data-view="molprop"]').click();
    requestAnimationFrame(() => { comparisonPlot(); $("#ev-result").scrollIntoView({ behavior: "smooth", block: "start" }); });
  });
  $("#ev-plotsel").onchange = comparisonPlot;
  $("#ev-pdf").onclick = e => { if (state.ev) saveFile(e.currentTarget, `/jobs/${state.ev.job}/plots/${$("#ev-plotsel").value}`, {}, "Making PDF…", "comparison.pdf"); };
  $("#ev-data").onclick = e => { if (state.ev) saveFile(e.currentTarget, `/jobs/${state.ev.job}/files/${$("#ev-plotsel").value}`, {}, "Saving…", "comparison.xvg"); };
  $("#ev-log").onclick = e => { if (state.ev) saveFile(e.currentTarget, `/jobs/${state.ev.job}/files/g`, {}, "Saving…", "train_ff.log"); };
  $("#mpw-open").onclick = () => $("#mpw-file").click();
  $("#mpw-file").onchange = async e => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    if (file.size > told.max_molprop_bytes) { alertBox(`The file is larger than ${Math.round(told.max_molprop_bytes / 1048576)} MB.`); return; }
    const button = $("#mpw-open");
    button.disabled = true;
    pill("mpw-pill", "Sending the file…");
    alertBox("");
    try {
      const up = await upload(file, "molprop");
      pill("mpw-pill", "Reading and checking…");
      const job = await runTask({ task: "mp_describe", molprop: { file: up.id } });
      state.file = { file: up.id, described: job.id, label: up.label };
      show(up.label, job.result);
      pill("mpw-pill", "");
      toast("Opened " + up.label);
    } catch (err) {
      pill("mpw-pill", "Failed", "bad");
      alertBox("The molprop file could not be read. " + err.message);
    } finally { button.disabled = false; }
  };
  $("#mpw-search").oninput = e => { state.query = e.target.value; compoundTable(); };
  $("#mpw-table").addEventListener("click", e => {
    const row = e.target.closest("tr.pick");
    if (!row) return;
    state.picked = state.picked === +row.dataset.i ? -1 : +row.dataset.i;
    compoundTable();
    compoundDetail();
  });
}
