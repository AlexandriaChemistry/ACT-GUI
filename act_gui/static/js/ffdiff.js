/*
 * This source file is part of the Alexandria Chemistry Toolkit.
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; either version 2
 * of the License, or (at your option) any later version.
 */
/**
 * @file The comparison of two force fields: the force field of the force
 * field workspace and another one. It lists the parameters with another
 * value, with other bounds or another way of training, and those that only
 * one of the two has.
 *
 * All text that comes from the server or from files is put on the page as
 * text, never as markup.
 */
import { $, addLibrary, withoutDefault, alertBox, el, libraryChoice, num, pill, runTask, upload } from "./common.js";

/** What the comparison knows. */
const state = {
  current: () => null,  // gives the force field of the workspace: { ref, base, data }
  told: null,           // what the server said: the default force field
  pending: null,        // a force field file that is uploaded when the comparison starts: { file, id }
  given: null,          // a force field that another workspace asked to compare with: { ref, label }
  result: null,         // the differences, see differences(), with the names of the two
  all: false,           // whether the parameters that only one of the two has are listed
};

/** The most rows that are put in the table at once. */
const MAX_ROWS = 500;
/** Two values are the same when they differ less than this part of the larger one. */
const TOLERANCE = 1e-9;

/**
 * The parameters of a force field, each with where it is.
 * @param {object} forcefield The force field as edit_ff describes it.
 * @returns {Map} For each parameter, by a key of where it is, its type and its kind: { where, identifier, param }.
 */
export function parameters(forcefield) {
  const found = new Map();
  const add = (where, identifier, list) => {
    for (const param of list || []) found.set([where, identifier, param.type].join("\u0000"), { where, identifier, param });
  };
  for (const type of (forcefield.particletypes || {}).types || []) add("Particle types", type.identifier, type.parameters);
  for (const interaction of forcefield.interactions || []) {
    for (const list of interaction.parameterlists || []) add(interaction.type, list.identifier, list.parameters);
  }
  return found;
}

/**
 * What differs between two force fields.
 * @param {object} a The first force field as edit_ff describes it.
 * @param {object} b The second one.
 * @returns {object} changed: the parameters of both with another value, other bounds or another way of training,
 *                   each { where, identifier, kind, unit, a, b, also }; onlyA and onlyB: those that one of them
 *                   has, each { where, identifier, kind, unit, value }; same: the number that is the same.
 */
export function differences(a, b) {
  const first = parameters(a), second = parameters(b), out = { changed: [], onlyA: [], onlyB: [], same: 0 };
  const differ = (x, y) => Math.abs(x - y) > TOLERANCE * Math.max(Math.abs(x), Math.abs(y), 1e-300);
  for (const [key, { where, identifier, param }] of first) {
    const other = second.get(key);
    if (!other) { out.onlyA.push({ where, identifier, kind: param.type, unit: param.unit, value: param.value }); continue; }
    const also = [];
    if (differ(param.minimum, other.param.minimum) || differ(param.maximum, other.param.maximum)) also.push("bounds");
    if (param.mutability !== other.param.mutability) also.push(`training: ${param.mutability} and ${other.param.mutability}`);
    const value = differ(param.value, other.param.value);
    if (value || also.length) out.changed.push({ where, identifier, kind: param.type, unit: param.unit, a: param.value, b: other.param.value, value, also });
    else out.same += 1;
  }
  for (const [key, { where, identifier, param }] of second) {
    if (!first.has(key)) out.onlyB.push({ where, identifier, kind: param.type, unit: param.unit, value: param.value });
  }
  return out;
}

/** Show the differences that were found. */
function table() {
  const result = state.result, host = $("#fd-table"), chips = $("#fd-chips");
  host.textContent = ""; chips.textContent = "";
  $("#fd-result").hidden = !result;
  if (!result) return;
  const add = (label, value) => chips.append(el("span", { class: "chip" }, label + " ", el("b", {}, String(value))));
  const values = result.changed.filter(row => row.value).length;
  add("another value", values);
  add("only other bounds or training", result.changed.length - values);
  add("only in " + result.nameA, result.onlyA.length);
  add("only in " + result.nameB, result.onlyB.length);
  add("the same", result.same);
  host.append(el("thead", {}, el("tr", {}, el("th", {}, "Where"), el("th", {}, "Type"), el("th", {}, "Parameter"),
    el("th", { class: "n" }, result.nameA), el("th", { class: "n" }, result.nameB), el("th", { class: "n" }, "Difference"),
    el("th", {}, "Unit"), el("th", {}, "Also different"))));
  const rows = result.changed.map(row => [row.where, row.identifier, row.kind, num(row.a), num(row.b), row.value ? num(row.b - row.a, 4) : "", row.unit, row.also.join(", ")]);
  if (state.all) {
    for (const row of result.onlyA) rows.push([row.where, row.identifier, row.kind, num(row.value), "", "", row.unit, "not in " + result.nameB]);
    for (const row of result.onlyB) rows.push([row.where, row.identifier, row.kind, "", num(row.value), "", row.unit, "not in " + result.nameA]);
  }
  const body = el("tbody");
  for (const row of rows.slice(0, MAX_ROWS)) {
    body.append(el("tr", {}, ...row.map((text, i) => el("td", { class: i >= 3 && i <= 5 ? "n" : i === 1 ? "num" : "" }, text))));
  }
  host.append(body);
  $("#fd-note").textContent = !rows.length ? "Nothing to list: the parameters that both force fields have are the same."
    : rows.length > MAX_ROWS ? `The first ${MAX_ROWS} of ${rows.length} rows.` : "The difference is the second value minus the first.";
}

/**
 * The other force field, sent to the server if it is a file of the user.
 * @returns {Promise<object|null>} How the server finds it and its name, null if a file has to be chosen first.
 */
async function other() {
  const choice = $("#fd-other").value;
  if (choice === "given" && state.given) return state.given;
  if (choice === "default") return { ref: { default: true }, label: state.told.default_forcefield };
  const installed = libraryChoice(choice);
  if (installed) return installed;
  if (!state.pending) return null;
  if (!state.pending.id) state.pending.id = (await upload(state.pending.file, "forcefield")).id;
  return { ref: { file: state.pending.id }, label: state.pending.file.name };
}

/** Compare the force field of the workspace with the other one. */
async function compare() {
  const current = state.current(), button = $("#run-diff");
  if (!current) { alertBox("Open or generate a force field first: it is compared with the other one."); return; }
  button.disabled = true;
  alertBox("");
  state.result = null;
  table();
  try {
    const second = await other();
    if (!second) { pill("fd-pill", "Choose a force field file", "warn"); return; }
    pill("fd-pill", "Reading the other force field…");
    const job = await runTask({ task: "ff_describe", forcefield: second.ref });
    // The first is the one that was there first: the other one, when the workspace has what was made from it
    const first = state.current();
    state.result = { ...differences(job.result.forcefield, first.data.forcefield), nameA: second.label, nameB: first.base };
    pill("fd-pill", "Done", "good");
    table();
  } catch (e) {
    pill("fd-pill", "Failed", "bad");
    alertBox("The force fields could not be compared. " + e.message);
  } finally { button.disabled = false; }
}

/**
 * Compare the force field of the workspace with one that another workspace names.
 * @param {object} ref   How the server finds the other force field.
 * @param {string} label Its name.
 */
export function compareWith(ref, label) {
  state.given = { ref, label };
  const option = $('#fd-other option[value="given"]');
  option.hidden = option.disabled = false;
  option.textContent = label;
  $("#fd-other").value = "given";
  compare();
}

/**
 * Make the comparison work.
 * @param {function} current Gives the force field of the workspace: { ref, base, data }, or null.
 * @param {object} told      What the server said: the default force field.
 */
export function startDiff(current, told) {
  state.current = current;
  state.told = told;
  $('#fd-other option[value="default"]').textContent = told.default_forcefield;
  addLibrary($("#fd-other"), told.forcefields);
  withoutDefault($("#fd-other"), told);
  // A file of the user, chosen with a button: a choice in the list cannot
  // open the file chooser in every browser
  $("#fd-open").onclick = () => $("#fd-file").click();
  $("#fd-file").onchange = e => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    state.pending = { file, id: null };
    const option = $('#fd-other option[value="upload"]');
    option.textContent = file.name;
    option.hidden = option.disabled = false;
    $("#fd-other").value = "upload";
  };
  $("#run-diff").onclick = compare;
  $("#fd-all").onchange = e => { state.all = e.target.checked; table(); };
  // Another force field in the workspace: what is shown is about the one before
  document.addEventListener("act-forcefield", () => { state.result = null; table(); pill("fd-pill", ""); });
}
