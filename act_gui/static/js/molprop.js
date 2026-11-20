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
 * properties, and whether the file is consistent.
 *
 * All text that comes from the server or from files is put on the page as
 * text, never as markup.
 */
import { $, alertBox, el, pill, runTask, toast, upload } from "./common.js";

/** What the workspace knows. */
const state = {
  data: null,     // the file as edit_mp describes it: summary, molecules and validation
  query: "",      // the text that the name or the formula of a compound must contain
  picked: -1,     // the compound of which the details are shown
};

/** The most compounds that are put in the table at once. */
const MAX_ROWS = 300;
/** The most messages of the validation that are listed. */
const MAX_MESSAGES = 40;

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

/**
 * Show a molprop file.
 * @param {string} label The name of the file.
 * @param {object} data  The file as edit_mp describes it.
 */
function show(label, data) {
  state.data = data;
  state.picked = -1;
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
