/*
 * This source file is part of the Alexandria Chemistry Toolkit.
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; either version 2
 * of the License, or (at your option) any later version.
 */
/**
 * @file The force field workspace: a new force field is made from a few
 * choices, gets its bonded parameters from a molprop file, and its
 * parameters are shown, changed and checked.
 *
 * The workspace has one force field at a time. Every step that changes it
 * is a calculation on the server that writes a new file, which becomes the
 * force field of the workspace; the page only refers to it by the
 * calculation that made it.
 *
 * All text that comes from the server or from files is put on the page as
 * text, never as markup.
 */
import { $, $$, addLibrary, alertBox, call, el, libraryChoice, num, pill, runTask, saveFile, seg, session, showOnly, tabs, toast, upload } from "./common.js";
import { compareWith, startDiff } from "./ffdiff.js";
import { chosenSites, redrawVsites, startVsites } from "./vsites.js";
import { markPoint, plot, resetZoom, zoomPlot } from "./plot.js";

/** What the workspace knows. */
const state = {
  told: null,          // what the server said when the page started
  gen: null,           // what a force field can be made from, see /api/generator
  types: new Map(),    // the atom types by name: { on, shell, vsite }
  qmodel: "",          // the charge model
  current: null,       // the force field: { ref, base, changes, data }, data as edit_ff describes it
  genJob: null,        // the calculation that made a force field, for its files
  geoJob: null,        // the calculation that extracted geometries, for its log
  molprop: null,       // the molprop file: { file, label }
  filter: "",          // the interaction of which the parameters are listed
  query: "",           // the text that identifiers must contain
  picked: null,        // the parameter that is being changed: { interaction, identifier, param }
  hist: null,          // the histograms of geometry_ff: { job, summary, analysis, data, marked }
  fixed: [],           // the files with fixed parameters: { name, entries }
};

/** The largest file with fixed parameters that is read. */
const MAX_FIXED_FILE = 1048576;

/**
 * The parameters of a file with fixed parameters, as actgui read them: a
 * line interaction|potential|atom type|parameter|value|unit for each, and
 * lines that start with # are comments. The potential is not used.
 * @param {string} text The content of the file.
 * @returns {object[]} For each parameter its interaction, type, parameter, value and unit.
 * @throws {Error} With what is wrong, for the user.
 */
export function fixedOfFile(text) {
  const entries = [];
  text.split(/\r?\n/).forEach((line, i) => {
    if (!line.trim() || line.startsWith("#")) return;
    const words = line.split("|").map(w => w.trim());
    if (words.length !== 6) throw new Error(`line ${i + 1} does not have six fields separated by |`);
    const [interaction, , type, parameter, number, unit] = words;
    const value = Number(number);
    if (!number || !isFinite(value)) throw new Error(`the value on line ${i + 1} is not a number`);
    if (!type || !parameter) throw new Error(`line ${i + 1} has no atom type or parameter`);
    entries.push({ interaction, type, parameter, value, unit });
  });
  if (!entries.length) throw new Error("it has no parameters");
  return entries;
}

/** Show the files with fixed parameters. */
function fixedList() {
  const list = $("#g-fixed");
  list.replaceChildren(...state.fixed.map((file, i) => el("li", {},
    el("span", {}, `${file.name}: ${file.entries.length} ${file.entries.length === 1 ? "parameter" : "parameters"}`),
    el("button", { class: "btn small", type: "button", data: { remove: i }, title: "Do not use this file" }, "Remove"))));
}

/**
 * Add the parameters of a file to those that are fixed.
 * @param {File} file The file.
 */
async function openFixed(file) {
  alertBox("");
  try {
    if (file.size > MAX_FIXED_FILE) throw new Error("it is larger than 1 MB");
    state.fixed.push({ name: file.name, entries: fixedOfFile(await file.text()) });
    fixedList();
  } catch (e) {
    alertBox(`The parameters of ${file.name} could not be read: ${e.message}.`);
  }
}

/** The most lists of parameters that are put in the table at once. */
const MAX_LISTS = 300;
/** The most problems that are listed. */
const MAX_ISSUES = 40;
/** What the page calls the charge models. */
const MODEL_NAMES = { POINT: "Point", GAUSSIAN: "Gaussian", SLATER: "Slater", "P+G": "Point + Gaussian" };
/** The elements of which an atom makes one bond. */
const MONOVALENT = ["H", "F", "Cl", "Br", "I"];

/**
 * Fill a list of choices.
 * @param {Element} select  The list.
 * @param {string[]} values The values.
 * @param {string} chosen   The value that is selected.
 * @param {function} label  Makes the text of a value, the value itself if absent.
 */
function fill(select, values, chosen, label = v => v) {
  select.textContent = "";
  for (const value of values) select.append(el("option", { value, selected: value === chosen }, label(value)));
}

/* ---------- the force field of the workspace ---------- */

/**
 * What the page calls the force field.
 * @returns {string} The name with the number of changes.
 */
function currentLabel() {
  const c = state.current;
  return c.base + (c.changes ? ` (${c.changes} ${c.changes === 1 ? "change" : "changes"})` : "");
}

/**
 * The name that is proposed when the force field of the workspace is saved:
 * that of the file it was opened from, marked when it was changed.
 * @returns {string} The name of the file.
 */
function saveName() {
  const c = state.current, stem = /\.xml$/i.test(c.base) ? c.base.replace(/\.xml$/i, "") : "forcefield";
  return stem + (c.changes && stem !== "forcefield" ? "-changed" : "") + ".xml";
}

/**
 * Make a force field the one of the workspace, and show it.
 * @param {object} ref     How the server finds it: { job }, { file }, { library } or { default: true }.
 * @param {string} base    Its name.
 * @param {object} data    The result of the calculation that described it.
 * @param {number} changes The number of changes since it was made or opened.
 */
function setCurrent(ref, base, data, changes = 0) {
  state.current = { ref, base, changes, data };
  state.picked = null;
  session.forcefield = { ref, label: currentLabel() };
  document.dispatchEvent(new CustomEvent("act-forcefield"));
  const ff = data.forcefield, host = $("#ffchips");
  host.textContent = "";
  const add = (label, value) => host.append(el("span", { class: "chip" }, label ? label + " " : "", el("b", {}, value)));
  add("", currentLabel());
  add("", `${ff.particletypes.types.length} particle types`);
  add("", ff.polarizable ? "polarizable" : "not polarizable");
  const used = ff.interactions.filter(i => i.parameterlists.length > 0).length;
  add("", `${used} interactions with parameters`);
  $("#ff-save").hidden = !ref.job;
  $("#run-geo").disabled = !state.molprop;
  parameterFilter();
  parameterTable();
  validation();
  editCard();
}

/**
 * Read a force field on the server and make it the one of the workspace.
 * @param {object} ref    How the server finds it.
 * @param {string} base   Its name.
 * @param {Element} button The button that was pressed.
 */
async function openForceField(ref, base, button) {
  const label = button.textContent;
  button.disabled = true; button.textContent = "Reading…";
  alertBox("");
  try {
    const job = await runTask({ task: "ff_describe", forcefield: ref });
    setCurrent(ref, base, job.result);
    toast("Opened " + base);
  } catch (e) {
    alertBox("The force field could not be read. " + e.message);
  } finally { button.disabled = false; button.textContent = label; }
}

/* ---------- generate ---------- */

/** Show the explanation of the charge model and what belongs to it. */
function chargeModel() {
  const model = state.gen.charge.models.find(m => m.name === state.qmodel);
  $("#g-qhelp").textContent = model ? model.comment : "";
  $("#g-rowfield").hidden = state.qmodel !== "SLATER";
}

/**
 * Fill the lists with the combination rules of a potential.
 * @param {Element} host     Where the lists go.
 * @param {object} potential The potential, with the rules it proposes.
 */
function ruleLists(host, potential) {
  host.textContent = "";
  for (const [param, rule] of Object.entries(potential ? potential.rules : {})) {
    const select = el("select", { data: { param }, "aria-label": "Combination rule of " + param });
    fill(select, state.gen.rules, rule);
    host.append(el("div", { class: "field" }, el("span", { class: "lab" }, param), select));
  }
}

/**
 * The combination rules that are chosen in a set of lists.
 * @param {Element} host Where the lists are.
 * @returns {object} The rule of each parameter.
 */
function chosenRules(host) {
  const rules = {};
  for (const select of $$("select", host)) rules[select.dataset.param] = select.value;
  return rules;
}

/** Show what belongs to the Van der Waals potential that is chosen. */
function vdwPotential() {
  const potential = state.gen.vdw.potentials.find(p => p.name === $("#g-vdw").value);
  $("#g-vdwhelp").textContent = potential.comment;
  ruleLists($("#g-vdwrules"), potential);
}

/** Show what belongs to the induction correction that is chosen. */
function inductionPotential() {
  const potential = state.gen.induction.potentials.find(p => p.name === $("#g-ic").value);
  $("#g-icmore").hidden = potential.name === "NONE";
  $("#g-ichelp").textContent = potential.comment;
  ruleLists($("#g-icrules"), potential);
}

/** Fill the table of atom types. */
function typeTable() {
  const table = $("#g-types");
  const all = key => {
    const chosen = [...state.types.values()].filter(t => t.on);
    return el("input", { type: "checkbox", data: { all: key }, checked: chosen.length > 0 && chosen.every(t => t[key]),
                         "aria-label": "For all selected atom types" });
  };
  const head = el("tr", {},
    el("th", { class: "c" }, ""), el("th", {}, "Type"), el("th", {}, "Element"), el("th", {}, "What it is"),
    el("th", { class: "c", title: "A shell makes the atom polarizable" }, "Shell ", all("shell")),
    el("th", { class: "c", title: "A distributed charge on top of the point charge of the atom" }, "Second charge ", all("vsite")));
  const body = el("tbody");
  let count = 0;
  for (const type of state.gen.atomtypes) {
    const choice = state.types.get(type.name);
    count += choice.on ? 1 : 0;
    // What is chosen for a type that is not selected is kept, not shown
    const box = key => el("input", { type: "checkbox", checked: choice[key] && (key === "on" || choice.on), disabled: key !== "on" && !choice.on,
                                     data: { name: type.name, key }, "aria-label": `${key} of ${type.name}` });
    body.append(el("tr", { class: choice.on ? "" : "off" },
      el("td", { class: "c" }, box("on")), el("td", { class: "num" }, type.name), el("td", {}, type.element),
      el("td", { class: "wrap" }, type.comment), el("td", { class: "c" }, box("shell")), el("td", { class: "c" }, box("vsite"))));
  }
  table.textContent = "";
  table.append(el("thead", {}, head), body);
  $("#g-ntypes").textContent = `${count} of ${state.gen.atomtypes.length} selected`;
  $("#run-gen").disabled = count === 0;
}

/**
 * Select or clear all atom types.
 * @param {boolean} on Whether they are selected.
 */
function allTypes(on) {
  for (const choice of state.types.values()) choice.on = on;
  typeTable();
}

/** Build the form with the choices for a new force field. */
function generatorForm() {
  const gen = state.gen;
  for (const type of gen.atomtypes) state.types.set(type.name, { on: type.default, shell: type.shell, vsite: type.vsite });
  // The charge model
  state.qmodel = gen.charge.default;
  const models = $("#g-qmodel");
  for (const model of gen.charge.models) {
    models.append(el("button", { type: "button", data: { v: model.name }, title: model.comment,
                                 "aria-pressed": String(model.name === state.qmodel) }, MODEL_NAMES[model.name] || model.name));
  }
  seg(models, name => {
    state.qmodel = name;
    if (name === gen.charge.second_charge) {
      // This model is a second charge on every atom
      for (const choice of state.types.values()) choice.vsite = true;
      typeTable();
    }
    chargeModel();
  });
  chargeModel();
  fill($("#g-vdw"), gen.vdw.potentials.map(p => p.name), gen.vdw.default);
  $("#g-vdw").onchange = vdwPotential;
  vdwPotential();
  fill($("#g-bonds"), gen.bonds, gen.bonds[0]);
  fill($("#g-angles"), gen.angles, gen.angles[0]);
  fill($("#g-dihedrals"), gen.dihedrals, gen.dihedrals[0]);
  fill($("#g-ic"), gen.induction.potentials.map(p => p.name), gen.induction.default, v => v === "NONE" ? "None" : v);
  $("#g-ic").onchange = inductionPotential;
  inductionPotential();
  const sites = $("#g-vsites");
  for (const site of gen.vsites) {
    sites.append(el("label", { class: "check" }, el("input", { type: "checkbox", data: { name: site.name } }), " " + site.label));
  }
  typeTable();
  $("#g-types").addEventListener("change", e => {
    const box = e.target;
    if (box.dataset.all) {
      for (const choice of state.types.values()) if (choice.on) choice[box.dataset.all] = box.checked;
    } else if (box.dataset.name) {
      state.types.get(box.dataset.name)[box.dataset.key] = box.checked;
    }
    typeTable();
  });
  $("#g-all").onclick = () => allTypes(true);
  $("#g-none").onclick = () => allTypes(false);
}

/**
 * The value of a field with a number.
 * @param {string} id The id of the field.
 * @returns {number|null} The number, null if the field is empty.
 */
function numberOf(id) {
  const value = $("#" + id).valueAsNumber;
  return Number.isFinite(value) ? value : null;
}

/**
 * The choices of the form, as the server takes them.
 * @returns {object} The object "generator" of the request.
 */
function generatorChoices() {
  const choices = {
    atomtypes: [...state.types].filter(([, c]) => c.on).map(([name, c]) => ({ name, shell: c.shell, vsite: c.vsite })),
    vdw: { potential: $("#g-vdw").value, nexcl: numberOf("g-nexcl"),
           particles: { heavy: $("#g-vdw-heavy").checked, hydrogen: $("#g-vdw-h").checked,
                        vsite: $("#g-vdw-vs").checked, shell: $("#g-vdw-shell").checked } },
    charge: { model: state.qmodel, epsilonr: numberOf("g-epsr"), maxrow: numberOf("g-row"), symmetric: $("#g-qsymm").checked },
    bonds: $("#g-bonds").value, angles: $("#g-angles").value, dihedrals: $("#g-dihedrals").value,
    induction: { potential: $("#g-ic").value, particles: { atom: $("#g-ic-atom").checked, shell: $("#g-ic-shell").checked } },
    vsites: { files: $$("#g-vsites input").filter(b => b.checked).map(b => b.dataset.name), exchange: $("#g-exchange").checked,
              sites: chosenSites() },
    rules: { VANDERWAALS: chosenRules($("#g-vdwrules")) },
    fixed: state.fixed.flatMap(file => file.entries),
  };
  if (choices.induction.potential !== "NONE") choices.rules.INDUCTIONCORRECTION = chosenRules($("#g-icrules"));
  return choices;
}

/**
 * Show the force field that was made.
 * @param {object} job The calculation.
 */
function generated(job) {
  const ff = job.result.forcefield, facts = $("#g-facts");
  facts.textContent = "";
  const add = (label, value) => facts.append(el("span", { class: "chip" }, label + " ", el("b", {}, String(value))));
  const kinds = {};
  for (const type of ff.particletypes.types) kinds[type.type] = (kinds[type.type] || 0) + 1;
  for (const [kind, n] of Object.entries(kinds)) add(kind === "Atom" ? "atom types" : kind.toLowerCase() + " types", n);
  add("charges", ff.particletypes.options.chargedistribution || "–");
  add("excluded bonds", ff.nexcl);
  add("relative dielectric constant", ff.epsilonr);
  const table = $("#g-inter");
  table.textContent = "";
  table.append(el("thead", {}, el("tr", {}, el("th", {}, "Interaction"), el("th", {}, "Function"),
                                  el("th", { class: "n" }, "Types with parameters"), el("th", {}, "Combination rules"))));
  const body = el("tbody");
  for (const inter of ff.interactions) {
    const rules = Object.entries(inter.combination_rules).map(([param, rule]) => `${param}: ${rule.rule}`).join(", ");
    body.append(el("tr", {}, el("td", {}, inter.type), el("td", {}, inter.function || "–"),
                   el("td", { class: "n" }, String(inter.parameterlists.length)), el("td", { class: "wrap" }, rules)));
  }
  table.append(body);
  $("#g-save-vs").hidden = !job.files.includes("vs");
  $("#g-result").hidden = false;
}

/** Make a force field of the choices in the form. */
async function generate() {
  const button = $("#run-gen");
  button.disabled = true;
  pill("gen-pill", "Generating…");
  alertBox("");
  try {
    const job = await runTask({ task: "gen_ff", generator: generatorChoices() });
    state.genJob = job.id;
    generated(job);
    setCurrent({ job: job.id }, "New force field", job.result);
    const ok = job.result.validation.ok;
    pill("gen-pill", ok ? "Done" : "Done, with problems", ok ? "good" : "warn");
  } catch (e) {
    pill("gen-pill", "Failed", "bad");
    alertBox("The force field could not be made. " + e.message);
  } finally { button.disabled = false; }
}

/* ---------- geometry ---------- */

/**
 * The types for the electronegativity of the atoms that make one bond.
 * @param {object} ff The description of a force field.
 * @returns {string[]} The identifiers, each once.
 */
function monovalentTypes(ff) {
  const found = [];
  for (const type of ff.particletypes.types) {
    const options = type.options || {};
    // An atom that makes bonds has a bond type of its own; an ion has none
    const bonded = options.bondtype && options.bondtype !== type.identifier;
    if (type.type === "Atom" && bonded && MONOVALENT.includes(options.element) &&
        options.acmtype && !found.includes(options.acmtype)) found.push(options.acmtype);
  }
  return found;
}

/**
 * Show what was taken from the compounds.
 * @param {object} job The calculation.
 */
function extracted(job) {
  $("#geo-empty").hidden = true;
  $("#geo-stats").hidden = false;
  $("#geo-ncomp").textContent = String(job.result.compounds);
  const table = $("#geo-table");
  table.textContent = "";
  table.append(el("thead", {}, el("tr", {}, el("th", {}, "Interaction"), el("th", { class: "n" }, "Different types found"))));
  const body = el("tbody");
  for (const [type, n] of Object.entries(job.result.extracted)) {
    body.append(el("tr", {}, el("td", {}, type), el("td", { class: "n" }, String(n))));
  }
  table.append(body);
  $("#geo-log").hidden = !job.files.includes("g");
  analysed(job);
}

/** The kinds of geometry of which the distributions are analysed, in the order of the page. */
const ANALYSED_KINDS = ["BONDS", "ANGLES", "LINEAR_ANGLES"];

/**
 * A p-value as text.
 * @param {number} p The p-value; zero stands for less than 1e-30.
 * @returns {string} The text.
 */
const pText = p => (p > 0 ? (p < 1e-4 ? p.toExponential(1) : num(p, 2)) : "< 1e-30");

/**
 * A skewness or kurtosis as text, with two decimals.
 * @param {number} value The number, or nothing.
 * @returns {string} The text, a dash without a number.
 */
const shapeText = value => (typeof value === "number" ? value.toFixed(2).replace("-", "−") : "–");

/**
 * What the analysis found for one type, in words.
 * @param {object} found  What geometry_ff -analyse says about the type.
 * @param {object} told   The analysis as a whole: the test and its settings.
 * @returns {string} A sentence, empty without an analysis.
 */
function verdict(found, told) {
  if (!found || !told) return "";
  const shape = `skewness ${shapeText(found.skewness)}, kurtosis ${shapeText(found.kurtosis)}`;
  const normal = !found.tested ? "Not tested for a normal distribution" + (found.sigma > 0 ? "" : ": its values are all the same")
    : found.normal ? `A normal distribution is not rejected (p ${pText(found.pvalue)}, ${shape})`
    : `Not a normal distribution (p ${pText(found.pvalue)}, ${shape})`;
  return normal + (found.wide ? ", and sigma is above the tolerance." : ".");
}

/**
 * Show what geometry_ff -analyse found: how many types of each kind are not
 * normally distributed or wide, and the types with a warning.
 * @param {object} job The calculation.
 */
function analysed(job) {
  const told = job.result.analysis, histograms = job.result.histograms || {};
  $("#geo-analysis").hidden = false;
  $("#geo-anatable").textContent = ""; $("#geo-warnings").textContent = ""; $("#geo-warnnote").textContent = "";
  $("#geo-nnotnormal-stat").hidden = $("#geo-nwide-stat").hidden = !told;
  if (!told) {
    $("#geo-ananote").textContent = "This alexandria does not test whether the bond lengths and angles are normally distributed (geometry_ff -analyse): install a newer version of ACT.";
    return;
  }
  const kinds = ANALYSED_KINDS.filter(kind => told.kinds[kind]);
  const total = key => kinds.reduce((sum, kind) => sum + (told.kinds[kind][key] || 0), 0);
  $("#geo-nnotnormal").textContent = String(total("not_normal"));
  $("#geo-nwide").textContent = String(total("wide"));
  $("#geo-ananote").textContent = `Each bond and angle type with at least ${told.minimum} values was tested for a normal distribution (${told.test} test, significance level ${told.alpha}). `
    + "Values that are not normally distributed can mean that a type covers different kinds of bonds or angles, or that structures are off. "
    + "With thousands of values the test finds small deviations as well: the skewness and the kurtosis tell how large they are. A distribution is wide if its sigma is above the tolerance.";
  $("#geo-anatable").append(el("thead", {}, el("tr", {}, el("th", {}, "Kind"), el("th", { class: "n" }, "Types"), el("th", { class: "n" }, "Tested"),
      el("th", { class: "n" }, "Not normal"), el("th", { class: "n" }, "Wide"), el("th", { class: "n" }, "Tolerance for sigma"))),
    el("tbody", {}, ...kinds.map(kind => {
      const k = told.kinds[kind], unit = k.unit === "degree" ? "°" : ` ${k.unit}`;
      return el("tr", {}, el("td", {}, GEOMETRY_KINDS[kind] || kind), el("td", { class: "n" }, String(k.distributions)), el("td", { class: "n" }, String(k.tested)),
        el("td", { class: "n" }, String(k.not_normal)), el("td", { class: "n" }, String(k.wide)), el("td", { class: "n" }, `${k.tolerance}${unit}`));
    })));
  // The types with a warning: those that are not normal first, the lowest p-value first
  const warned = [];
  for (const kind of kinds) {
    for (const h of histograms[kind] || []) {
      const a = h.analysis;
      if (a && (a.wide || (a.tested && !a.normal))) warned.push({ kind, h, a, failed: a.tested && !a.normal });
    }
  }
  warned.sort((p, q) => (q.failed - p.failed) || (p.failed ? p.a.pvalue - q.a.pvalue : q.a.sigma - p.a.sigma) || (q.h.count - p.h.count));
  if (!warned.length) {
    $("#geo-warnnote").textContent = `No warnings: none of the ${total("tested")} types that were tested differs from a normal distribution, and no sigma is above the tolerance.`;
    return;
  }
  const number = (value, digits) => (typeof value === "number" ? num(value, digits) : "–");
  $("#geo-warnings").append(el("thead", {}, el("tr", {}, el("th", {}, "Type"), el("th", {}, "Kind"), el("th", { class: "n" }, "Values"),
      el("th", { class: "n" }, "Average"), el("th", { class: "n" }, "Sigma"), el("th", { class: "n" }, "p"),
      el("th", { class: "n" }, "Skewness"), el("th", { class: "n" }, "Kurtosis"), el("th", {}, "Warning"))),
    el("tbody", {}, ...warned.map(({ kind, h, a, failed }) => el("tr", { class: "pick", title: "Show the histogram", data: { kind, id: h.identifier } },
      el("td", { class: "mono" }, h.identifier), el("td", {}, GEOMETRY_KINDS[kind] || kind), el("td", { class: "n" }, String(h.count)),
      el("td", { class: "n" }, number(a.average, 5)), el("td", { class: "n" }, number(a.sigma, 3)),
      el("td", { class: "n" }, a.tested ? pText(a.pvalue) : "–"), el("td", { class: "n" }, a.tested ? shapeText(a.skewness) : "–"),
      el("td", { class: "n" }, a.tested ? shapeText(a.kurtosis) : "–"),
      el("td", {}, failed ? el("span", { class: "pill warn" }, "not normal") : "", failed && a.wide ? " " : "", a.wide ? el("span", { class: "pill warn" }, "wide") : "")))));
  $("#geo-warnnote").textContent = `${warned.length} ${warned.length === 1 ? "type" : "types"} with a warning. Click one to see its histogram. `
    + "A negative kurtosis points to two peaks, a positive one to outliers, a skewness to a tail on one side.";
}

/**
 * Show the histogram of a type, from the list of warnings.
 * @param {string} kind       The kind of geometry.
 * @param {string} identifier The type.
 */
function showHistogram(kind, identifier) {
  if (!state.hist || !(state.hist.summary[kind] || []).some(h => h.identifier === identifier)) return;
  $("#geo-htype").value = kind;
  histogramKind();
  $("#geo-hid").value = identifier;
  histogramShown();
  $("#geo-hist").scrollIntoView({ behavior: "smooth", block: "start" });
}

/**
 * The molprop file that the geometries are taken from, as the server finds it: a file of
 * its own, or that of the molprop workspace with the compounds that are ticked there.
 * @returns {object} The reference to the file, with the compounds that are not used if there are any.
 */
function geometrySource() {
  const shared = state.molprop.shared ? session.molprop : null;
  if (!shared) return { file: state.molprop.file };
  // Without compounds that are left out, the program takes all compounds of the file itself
  return shared.sets.ignore.length ? { file: shared.file, described: shared.described, sets: { test: [], ignore: shared.sets.ignore } } : { file: shared.file };
}

/** Tell which molprop file the geometries are taken from. */
function geometryLabel() {
  const shared = state.molprop && state.molprop.shared ? session.molprop : null;
  if (state.molprop && state.molprop.shared && !shared) state.molprop = null;
  $("#mp-name").textContent = shared
    ? shared.label + (shared.used < shared.total ? ` · the ${shared.used} of ${shared.total} compounds that are ticked in Molprop data` : ` · all ${shared.total} compounds`)
    : state.molprop ? state.molprop.label : "";
  $("#mp-shared").hidden = !session.molprop || Boolean(shared);
  $("#run-geo").disabled = !(state.current && state.molprop);
}

/** Take the bonded parameters of the force field from a molprop file. */
async function geometry() {
  if (!state.current || !state.molprop) return;
  if (state.molprop.shared && session.molprop.used < 1) { toast("No compound is ticked in the tab Molprop data."); return; }
  const button = $("#run-geo"), sqe = $('#geo-qalg button[aria-pressed="true"]').dataset.v === "SQE";
  button.disabled = true;
  pill("geo-pill", "Reading the compounds…");
  alertBox("");
  try {
    const options = { gen_bcc: sqe, bfactor: numberOf("geo-bfactor"), afactor: numberOf("geo-afactor"), factor: numberOf("geo-factor") };
    let job = await runTask({ task: "geometry_ff", forcefield: state.current.ref, molprop: geometrySource(), options });
    state.geoJob = job.id;
    extracted(job);
    histogramsCard(job);
    if (!job.result.compounds) {
      pill("geo-pill", "No compound could be used", "warn");
      alertBox("None of the compounds in the file could be used with this force field, so it was not changed. " +
               "The log file tells why, for instance atom types that the force field does not have.");
      return;
    }
    const fixed = sqe ? monovalentTypes(job.result.forcefield) : [];
    if (fixed.length) {
      pill("geo-pill", "Fixing electronegativities…");
      const edit = await runTask({ task: "ff_edit", forcefield: { job: job.id }, options: { p: "chi", a: fixed, mut: "Fixed" } });
      if (edit.result.changed && edit.files.includes("o")) job = edit;
    }
    setCurrent({ job: job.id }, state.current.base, job.result, state.current.changes + 1);
    pill("geo-pill", "Done", "good");
  } catch (e) {
    pill("geo-pill", "Failed", "bad");
    alertBox("The geometries could not be extracted. " + e.message);
  } finally { button.disabled = !(state.current && state.molprop); }
}

/* ---------- histograms of the geometries ---------- */

/** What the page calls the kinds of geometry. */
const GEOMETRY_KINDS = { BONDS: "Bond lengths", ANGLES: "Angles", LINEAR_ANGLES: "Linear angles",
                         PROPER_DIHEDRALS: "Dihedral angles", IMPROPER_DIHEDRALS: "Improper dihedrals" };

/**
 * Offer the histograms of the geometries that were extracted.
 * @param {object} job The calculation of geometry_ff; its result lists the histograms.
 */
function histogramsCard(job) {
  const histograms = job.result && job.result.histograms;
  const kinds = histograms ? Object.keys(histograms).filter(kind => histograms[kind].length) : [];
  state.hist = { job: job.id, summary: histograms || {}, analysis: (job.result && job.result.analysis) || null, data: null, marked: null };
  $("#geo-hist").hidden = !kinds.length;
  if (!kinds.length) return;
  const select = $("#geo-htype");
  select.textContent = "";
  for (const kind of kinds) select.append(el("option", { value: kind }, `${GEOMETRY_KINDS[kind] || kind} (${histograms[kind].length})`));
  histogramKind();
}

/** Offer the types of the kind of geometry that is chosen, the most common first. */
function histogramKind() {
  const listed = state.hist.summary[$("#geo-htype").value] || [], select = $("#geo-hid");
  select.textContent = "";
  // A type with a warning says so
  const warning = a => (!a ? "" : (a.tested && !a.normal ? " · not normal" : "") + (a.wide ? " · wide" : ""));
  for (const h of listed) select.append(el("option", { value: h.identifier }, `${h.identifier} · ${h.count}${warning(h.analysis)}`));
  histogramShown();
}

/** Fetch and draw the histogram that is chosen. */
async function histogramShown() {
  const hist = state.hist, kind = $("#geo-htype").value, identifier = $("#geo-hid").value;
  if (!hist || !identifier) return;
  try {
    const data = await (await call(`/jobs/${hist.job}/histograms`, { params: { type: kind, identifier } })).json();
    if (state.hist !== hist || $("#geo-hid").value !== identifier) return;
    hist.data = data;
    hist.marked = null;
  } catch (e) { alertBox("The histogram could not be fetched. " + e.message); return; }
  histogramPlot();
  binSamples(-1);
}

/** Draw the histogram that was fetched: bars, and a point on top of each to click. */
function histogramPlot() {
  const data = state.hist.data, w = data.binwidth || 1, bins = data.bins || [];
  if (!bins.length) { $("#geoplot").textContent = ""; return; }
  // The outline of the bars, with the empty bins in between
  const x = [], y = [];
  const first = bins[0].value - w / 2, last = bins[bins.length - 1].value + w / 2;
  let k = 0;
  x.push(first); y.push(0);
  for (let left = first; left < last - w / 2; left += w) {
    const bin = bins[k] && Math.abs(bins[k].value - (left + w / 2)) < w / 4 ? bins[k++] : null;
    const count = bin ? bin.count : 0;
    x.push(left, left + w); y.push(count, count);
  }
  x.push(last); y.push(0);
  const top = Math.max(...bins.map(b => b.count));
  const unit = data.unit === "degree" ? "degrees" : data.unit;
  plot("geoplot", { label: `Histogram of ${data.identifier}`, xlim: [first - w, last + w], ylim: [0, top * 1.12 || 1], height: 340,
                    xlabel: `${data.identifier} (${unit})`, ylabel: "Values", zoom: true, marked: state.hist.marked,
                    series: [{ type: "line", x, y, fill: true },
                             { type: "points", x: bins.map(b => b.value), y: bins.map(b => b.count), size: 7, pickable: true }],
                    onPoint: (series, index) => { state.hist.marked = { series, id: index }; markPoint("geoplot", state.hist.marked); binSamples(index); } });
  const listed = (state.hist.summary[$("#geo-htype").value] || []).find(h => h.identifier === data.identifier);
  const found = verdict(listed && listed.analysis, state.hist.analysis);
  $("#geo-histnote").textContent = `${data.count} values in bins of ${num(w, 4)} ${unit}. ${found ? found + " " : ""}Zoom with the wheel or with + and −, drag to move, double-click or All to see all of it.`;
}

/**
 * List where the values of a bin come from.
 * @param {number} index The bin, -1 for none.
 */
function binSamples(index) {
  const data = state.hist.data, bin = data && index >= 0 ? data.bins[index] : null, table = $("#geo-samples");
  table.textContent = "";
  if (!bin) {
    $("#geo-bintitle").textContent = "Where the values come from";
    $("#geo-binnote").textContent = "Click a bar to see from which compounds, calculations and atoms its values come.";
    return;
  }
  const unit = data.unit === "degree" ? "°" : ` ${data.unit}`;
  $("#geo-bintitle").textContent = `${bin.count} ${bin.count === 1 ? "value" : "values"} around ${num(bin.value, 4)}${unit}`;
  if (!bin.samples) {
    $("#geo-binnote").textContent = "This alexandria does not tell where the values come from (geometry_ff -samples).";
    return;
  }
  table.append(el("thead", {}, el("tr", {}, el("th", {}, "Compound"), el("th", {}, "Calculation"), el("th", {}, "Atoms"), el("th", { class: "n" }, "Value"))));
  const body = el("tbody");
  for (const s of bin.samples) {
    // The data file of the calculation without its directory, which can be long
    body.append(el("tr", {}, el("td", {}, s.molecule), el("td", { class: "mono", title: s.calculation }, s.calculation.split("/").pop()),
      el("td", { class: "mono" }, s.names),
      el("td", { class: "n" }, num(s.value, 4) + unit)));
  }
  table.append(body);
  $("#geo-binnote").textContent = (bin.more ? `And ${bin.more} more values in this bin. ` : "") +
    "The atoms are numbered from one, in the order of the molprop file, with their names.";
}

/* ---------- parameters ---------- */

/** Fill the list with what the table of parameters can show. */
function parameterFilter() {
  const ff = state.current.data.forcefield, select = $("#p-filter");
  const options = [["@particles", `Particle types (${ff.particletypes.types.length})`]];
  for (const inter of ff.interactions) {
    if (inter.parameterlists.length) {
      const fn = inter.function && inter.function !== inter.type ? ` · ${inter.function}` : "";
      options.push([inter.type, `${inter.type}${fn} (${inter.parameterlists.length})`]);
    }
  }
  if (!options.some(([value]) => value === state.filter)) state.filter = options[options.length > 1 ? 1 : 0][0];
  select.textContent = "";
  for (const [value, label] of options) select.append(el("option", { value, selected: value === state.filter }, label));
}

/**
 * The lists of parameters that the table shows.
 * @returns {object[]} For each list its identifier and its parameters.
 */
function listsShown() {
  const ff = state.current.data.forcefield;
  const lists = state.filter === "@particles" ? ff.particletypes.types
    : (ff.interactions.find(i => i.type === state.filter) || { parameterlists: [] }).parameterlists;
  const query = state.query.trim().toLowerCase();
  return query ? lists.filter(l => l.identifier.toLowerCase().includes(query)) : lists;
}

/**
 * Where a value is between its bounds, as a mark on a bar.
 * @param {object} param The parameter.
 * @returns {Element} The bar.
 */
function rangeBar(param) {
  const span = param.maximum - param.minimum, out = param.value < param.minimum || param.value > param.maximum;
  const where = span > 0 ? Math.min(1, Math.max(0, (param.value - param.minimum) / span)) : 0.5;
  const mark = el("i");
  mark.style.left = `calc(${(100 * where).toFixed(1)}% - 1px)`;
  return el("span", { class: "range" + (out ? " out" : param.mutability === "Bounded" ? "" : " fixed") }, mark);
}

/**
 * The parameters of a particle type that edit_ff changes: those of its charge.
 * The mass and the reference enthalpy are properties of the element that it leaves as they are.
 */
const PARTICLE_PARAMETERS = ["charge", "zeta", "chargeVS", "zetaVS"];

/** Fill the table of parameters. */
function parameterTable() {
  const table = $("#p-table"), empty = $("#p-empty");
  table.textContent = "";
  empty.hidden = !!state.current;
  if (!state.current) { $("#p-count").textContent = ""; return; }
  const lists = listsShown(), shown = lists.slice(0, MAX_LISTS), particles = state.filter === "@particles";
  $("#p-count").textContent = lists.length > shown.length
    ? `the first ${shown.length} of ${lists.length} types, find a type to see others`
    : `${lists.length} ${lists.length === 1 ? "type" : "types"}`;
  table.append(el("thead", {}, el("tr", {}, el("th", {}, "Type"), el("th", {}, "Parameter"), el("th", { class: "n" }, "Value"),
    el("th", {}, "Unit"), el("th", { class: "n" }, "Minimum"), el("th", { class: "n" }, "Maximum"), el("th", {}, "Within bounds"),
    el("th", {}, "Training"), el("th", { class: "n", title: "The number of data points it was trained on" }, "N"))));
  const body = el("tbody");
  shown.forEach((list, i) => {
    list.parameters.forEach((param, j) => {
      const picked = state.picked && state.picked.identifier === list.identifier && state.picked.param.type === param.type;
      const editable = !particles || PARTICLE_PARAMETERS.includes(param.type);
      const row = el("tr", { class: (editable ? "pick" : "") + (picked ? " sel" : ""), data: { list: String(i), param: String(j) },
                             title: editable ? "" : "This parameter of a particle type cannot be changed here" },
        el("td", { class: "num" }, j === 0 ? list.identifier : ""), el("td", {}, param.type), el("td", { class: "n" }, num(param.value)),
        el("td", {}, param.unit), el("td", { class: "n" }, num(param.minimum)), el("td", { class: "n" }, num(param.maximum)),
        el("td", {}, rangeBar(param)), el("td", {}, param.mutability), el("td", { class: "n" }, String(param.ntrain)));
      body.append(row);
    });
  });
  table.append(body);
  if (!shown.length) { empty.hidden = false; empty.textContent = "No type with this text."; }
}

/** Show the form for the parameter that was picked, or hide it. */
function editCard() {
  const card = $("#p-edit"), picked = state.picked;
  card.hidden = !picked;
  pill("p-pill", "");
  if (!picked) return;
  const what = $("#p-what"), param = picked.param;
  what.textContent = "";
  for (const text of [picked.interaction === "@particles" ? "Particle type" : picked.interaction, picked.identifier, param.type + (param.unit ? ` (${param.unit})` : "")]) {
    what.append(el("span", { class: "chip" }, el("b", {}, text)));
  }
  $("#p-val").value = param.value; $("#p-min").value = param.minimum; $("#p-max").value = param.maximum;
  const select = $("#p-mut"), other = $('option[value=""]', select);
  if (other) other.remove();
  if (["Bounded", "Fixed"].includes(param.mutability)) select.value = param.mutability;
  else { select.prepend(el("option", { value: "" }, `Leave as it is: ${param.mutability}`)); select.value = ""; }
  $("#p-note").textContent = param.mutability === "Bounded"
    ? "A value outside the bounds is set to the nearest bound."
    : param.mutability === "ACM"
      ? "This charge comes from the charge model for each molecule, so a value that is set here is not used."
      : `Training does not change this parameter (${param.mutability}). A new value or new bounds are set all the same.`;
}

/**
 * Whether two numbers differ by more than rounding.
 * @param {number} a The one.
 * @param {number} b The other.
 * @returns {boolean} Whether they differ.
 */
const differ = (a, b) => Math.abs(a - b) > 1e-9 * Math.max(1, Math.abs(a), Math.abs(b));

/** Change the parameter that was picked to what the form says. */
async function applyChange() {
  const picked = state.picked;
  if (!picked) return;
  const param = picked.param, options = { p: param.type, a: picked.identifier };
  for (const [id, key, was] of [["p-val", "val", param.value], ["p-min", "min", param.minimum], ["p-max", "max", param.maximum]]) {
    const value = numberOf(id);
    if (value === null) { toast("Give a number for the value and for both bounds."); return; }
    if (differ(value, was)) options[key] = value;
  }
  const mut = $("#p-mut").value;
  if (mut && mut !== param.mutability) options.mut = mut;
  if (Object.keys(options).length === 2) { toast("Nothing was changed in the form."); return; }
  // A parameter that training does not change is changed only when forced
  if (param.mutability !== "Bounded" && ("val" in options || "min" in options || "max" in options)) options.force = true;
  const button = $("#p-apply");
  button.disabled = true;
  pill("p-pill", "Changing…");
  alertBox("");
  try {
    const job = await runTask({ task: "ff_edit", forcefield: state.current.ref, options });
    if (!job.result.changed || !job.files.includes("o")) {
      pill("p-pill", "Nothing changed", "warn");
      toast("The force field did not change: the new values may be the same as the old ones after rounding.");
      return;
    }
    const what = `${param.type} of ${picked.identifier}`;
    setCurrent({ job: job.id }, state.current.base, job.result, state.current.changes + 1);
    toast("Changed " + what);
  } catch (e) {
    pill("p-pill", "Failed", "bad");
    alertBox("The parameter could not be changed. " + e.message);
  } finally { button.disabled = false; }
}

/* ---------- validation ---------- */

/** Show the problems that were found in the force field. */
function validation() {
  const result = state.current.data.validation, list = $("#v-list");
  list.textContent = "";
  const item = (mark, kind, ...content) => list.append(el("li", {}, el("span", { class: kind }, mark), el("span", {}, ...content)));
  if (result.ok) {
    item("✓", "ok", "No problems found.");
    pill("v-pill", "Valid", "good");
  } else {
    for (const issue of result.issues.slice(0, MAX_ISSUES)) {
      item(issue.repaired ? "!" : "✕", issue.repaired ? "wr" : "er",
           el("span", { class: "what" }, `${issue.where} ${issue.identifier} ${issue.parameter}`), ": " + issue.text);
    }
    if (result.issues.length > MAX_ISSUES) list.append(el("li", { class: "more" }, `and ${result.issues.length - MAX_ISSUES} more`));
    const left = result.issues.length - result.repaired;
    pill("v-pill", `${result.issues.length} ${result.issues.length === 1 ? "problem" : "problems"}`, left ? "bad" : "warn");
  }
  $("#v-repair").hidden = !(result.repaired > 0);
  $("#v-note").textContent = result.ok
    ? "The parameters are checked each time the force field is read: values within their bounds, a minimum that is not larger than the maximum, no negative value where that is not allowed, and each parameter defined once."
    : `${result.repaired} of ${result.issues.length} can be corrected by writing the force field again (marked !): values outside their bounds go to the nearest bound and of a parameter that is defined twice the first definition is kept. The others (marked ✕) have to be changed by hand in the table.`;
}

/** Write the force field without the problems that writing removes. */
async function repair() {
  const button = $("#v-repair");
  button.disabled = true;
  alertBox("");
  try {
    const job = await runTask({ task: "ff_repair", forcefield: state.current.ref });
    if (!job.files.includes("o")) { toast("There was nothing to correct."); return; }
    setCurrent({ job: job.id }, state.current.base, job.result, state.current.changes + 1);
    toast("Wrote a corrected force field");
  } catch (e) {
    alertBox("The force field could not be corrected. " + e.message);
  } finally { button.disabled = false; }
}

/* ---------- start ---------- */

const TABS = { gen: "fftab-gen", vsite: "fftab-vsite", geom: "fftab-geom", edit: "fftab-edit", diff: "fftab-diff" };

/**
 * Make the workspace work. Called when the server has told who we are.
 * @param {object} told What the server said: the default force field and the limits.
 */
export async function startForceFields(told) {
  state.told = told;
  tabs($("#fftabs"), name => {
    showOnly(Object.values(TABS), TABS[name]);
    // The viewer can only be drawn when it is on the page
    if (name === "vsite") requestAnimationFrame(redrawVsites);
  });
  $("#ff-open").onclick = () => $("#ff-openfile").click();
  $("#ff-openfile").onchange = async e => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    try {
      const up = await upload(file, "forcefield");
      await openForceField({ file: up.id }, up.label, $("#ff-open"));
    } catch (err) { alertBox("The file was not accepted. " + err.message); }
  };
  $("#ff-default").onclick = e => openForceField({ default: true }, told.default_forcefield, e.currentTarget);
  $("#ff-default").disabled = told.has_default_forcefield === false;
  // An installed force field, chosen in the list and opened with the button
  addLibrary($("#ff-lib"), told.forcefields);
  $("#ff-lib").hidden = $("#ff-libopen").hidden = !(told.forcefields || []).length;
  $("#ff-lib").onchange = () => { $("#ff-libopen").disabled = !libraryChoice($("#ff-lib").value); };
  $("#ff-libopen").onclick = e => {
    const installed = libraryChoice($("#ff-lib").value);
    // Its name as that of a file, from which the name to save it under is made
    if (installed) openForceField(installed.ref, installed.label.split("/").pop() + ".xml", e.currentTarget);
  };
  // A force field that another workspace made, such as a trained one
  // With "other" it is compared with that one, such as the force field that was trained
  document.addEventListener("act-open-forcefield", async e => {
    await openForceField(e.detail.ref, e.detail.label, $("#ff-open"));
    if (!state.current || state.current.ref !== e.detail.ref) return;
    $('#nav button[data-view="force-field"]').click();
    if (e.detail.other) {
      $('#fftabs button[data-tab="diff"]').click();
      compareWith(e.detail.other.ref, e.detail.other.label);
    }
  });
  startDiff(() => state.current, told);
  $("#ff-save").onclick = e => {
    const ref = state.current && state.current.ref;
    if (ref && ref.job) saveFile(e.currentTarget, `/jobs/${ref.job}/files/o`, {}, "Saving…", saveName());
  };
  $("#run-gen").onclick = generate;
  $("#g-fixed-open").onclick = () => $("#g-fixed-file").click();
  $("#g-fixed-file").onchange = e => {
    const file = e.target.files[0];
    e.target.value = "";
    if (file) openFixed(file);
  };
  $("#g-fixed").addEventListener("click", e => {
    const button = e.target.closest("button[data-remove]");
    if (!button) return;
    state.fixed.splice(+button.dataset.remove, 1);
    fixedList();
  });
  $("#g-save-f").onclick = e => saveFile(e.currentTarget, `/jobs/${state.genJob}/files/f`, {}, "Saving…", "atomtypes.csv");
  $("#g-save-vs").onclick = e => saveFile(e.currentTarget, `/jobs/${state.genJob}/files/vs`, {}, "Saving…", "vsites.json");
  seg($("#geo-qalg"), () => {});
  $("#mp-open").onclick = () => $("#mp-file").click();
  $("#mp-file").onchange = async e => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    if (file.size > told.max_molprop_bytes) { alertBox(`The file is larger than ${Math.round(told.max_molprop_bytes / 1048576)} MB.`); return; }
    $("#mp-name").textContent = "Sending " + file.name + "…";
    try {
      const up = await upload(file, "molprop");
      state.molprop = { file: up.id, label: up.label };
      geometryLabel();
      alertBox("");
    } catch (err) {
      geometryLabel();
      alertBox("The file was not accepted. " + err.message);
    }
  };
  // The file of the molprop workspace, with the compounds that are ticked there
  $("#mp-shared").onclick = () => { state.molprop = { shared: true }; geometryLabel(); };
  document.addEventListener("act-molprop", geometryLabel);
  $("#run-geo").onclick = geometry;
  // The histograms of the geometries
  $("#geo-htype").onchange = histogramKind;
  $("#geo-hid").onchange = histogramShown;
  $("#geo-warnings").onclick = e => { const row = e.target.closest("tr[data-kind]"); if (row) showHistogram(row.dataset.kind, row.dataset.id); };
  $("#geo-zoomin").onclick = () => zoomPlot("geoplot", 0.8);
  $("#geo-zoomout").onclick = () => zoomPlot("geoplot", 1.25);
  $("#geo-zoomall").onclick = () => resetZoom("geoplot");
  $("#geo-log").onclick = e => saveFile(e.currentTarget, `/jobs/${state.geoJob}/files/g`, {}, "Saving…", "geometry_ff.log");
  $("#p-filter").onchange = e => { state.filter = e.target.value; state.picked = null; parameterTable(); editCard(); };
  $("#p-search").oninput = e => { state.query = e.target.value; parameterTable(); };
  $("#p-table").addEventListener("click", e => {
    const row = e.target.closest("tr.pick");
    if (!row) return;
    const list = listsShown()[+row.dataset.list], param = list.parameters[+row.dataset.param];
    state.picked = { interaction: state.filter, identifier: list.identifier, param };
    parameterTable();
    editCard();
  });
  $("#p-apply").onclick = applyChange;
  $("#p-cancel").onclick = () => { state.picked = null; parameterTable(); editCard(); };
  $("#v-repair").onclick = repair;
  try {
    state.gen = await (await call("/generator")).json();
    generatorForm();
    startVsites(state.gen.site_kinds, state.gen.atomtypes, () => {
      const n = chosenSites().length;
      $("#g-ownsites").textContent = n ? `and ${n} of your own, see the tab Virtual sites` : "";
    });
  } catch (e) {
    alertBox("The choices for a new force field could not be fetched. " + e.message);
  }
}
