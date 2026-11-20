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
import { $, $$, alertBox, call, el, num, pill, runTask, saveFile, session, toast, upload } from "./common.js";
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
 * @param {object} job The calculation.
 */
function compared(job) {
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

/** Compare the force field that is chosen with the data of the file. */
async function compare() {
  const observables = $$("#ev-observables input").filter(b => b.checked).map(b => b.dataset.name);
  if (!observables.length) { toast("Choose at least one property to compare."); return; }
  const button = $("#run-ev"), choice = $("#ev-ff").value;
  button.disabled = true;
  alertBox("");
  // What is on the page belongs to the comparison before this one
  state.ev = null;
  $("#ev-result").hidden = true;
  try {
    let forcefield = { default: true };
    if (choice === "workspace" && session.forcefield) forcefield = session.forcefield.ref;
    else if (choice === "upload") {
      if (!state.pendingFF) { toast("Choose a force field file first."); return; }
      if (!state.pendingFF.id) {
        pill("ev-pill", "Sending the force field…");
        state.pendingFF.id = (await upload(state.pendingFF.file, "forcefield")).id;
      }
      forcefield = { file: state.pendingFF.id };
    }
    pill("ev-pill", "Computing…");
    const job = await runTask({ task: "evaluate", forcefield, observables,
                                molprop: { file: state.file.file, described: state.file.described } });
    if (!Object.keys(job.result.statistics).length) {
      pill("ev-pill", "Nothing to compare", "warn");
      alertBox("Nothing could be compared: no compound of the file could be used with this force field, or the file has no data for these properties. The log file tells why.");
    } else pill("ev-pill", "Done", "good");
    compared(job);
  } catch (e) {
    pill("ev-pill", "Failed", "bad");
    const none = /No molecules to train on/.test(e.message)
      ? " None of the compounds in the file could be used with this force field, for instance because it does not have their atom types." : "";
    alertBox("The force field could not be compared with the data. " + e.message + "." + none);
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
