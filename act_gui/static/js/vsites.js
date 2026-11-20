/*
 * This source file is part of the Alexandria Chemistry Toolkit.
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; either version 2
 * of the License, or (at your option) any later version.
 */
/**
 * @file Virtual sites by point and click: the user picks two, three or
 * four atoms of a molecule, chooses the kind of site and the bounds of its
 * parameters, and sees where the site is. The definition is in the form
 * that alexandria gen_ff -vs reads, on the types of the atoms, and is used
 * when a force field is made.
 *
 * The positions are computed here as ACT computes them (vsitehandler.cpp),
 * for the preview only: the force field has the bounds, not the position.
 *
 * All text that comes from the server is put on the page as text.
 */
import { $, alertBox, el, num, runTask, session, toast } from "./common.js";
import { css } from "./plot.js";

/* global $3Dmol */

/** What this part of the page knows. */
const state = {
  kinds: [],        // the kinds of sites: { name, atoms, parameters }
  atomtypes: [],    // the atom types that a new force field can have: { name, element, bonds }
  types: new Map(), // the atom type that the user chose for an atom, where it is not the proposed one
  mol: null,        // the molecule: { atoms: [{ elem, type }], bonds, xyz (nm) }
  picked: [],       // the atoms that were picked, in order
  values: {},       // for each parameter of the kind: { value, min, max }
  sites: [],        // the sites that were added, as gen_ff reads them
  onChange: null,   // called when the list of sites changes
};

/** The viewer, made when it is first needed. */
let V = null, triedV = false;

/** What each kind of site is, in words. The atoms are numbered as they are picked. */
const KIND_HELP = {
  VSITE2: "On the line through the two atoms: at atom 1 plus a times the vector to atom 2. A negative a is on the far side of atom 1.",
  VSITE2FD: "On the line from atom 1 to atom 2, at the distance a (nm) from atom 1, whatever the bond length.",
  VSITE3S: "In the plane of the three atoms, atom 2 in the middle: atom 2 plus a times the vectors to atoms 1 and 3, the same for both.",
  VSITE3: "In the plane of the three atoms, atom 2 in the middle: atom 2 plus a times the vector to atom 1 and b times that to atom 3.",
  VSITE3FD: "In the plane of the three atoms, at the distance b (nm) from atom 2, towards the point at a fraction a between atoms 1 and 3.",
  VSITE3OUTS: "Two sites, one on either side of the plane of the three atoms, atom 2 in the middle: a for both vectors in the plane and c (1/nm) times their normal.",
  VSITE3OUT: "Two sites, one on either side of the plane of the three atoms, atom 2 in the middle: a and b for the vectors to atoms 1 and 3 and c (1/nm) times their normal.",
  VSITE4S3: "Atom 1 in the middle of three equal atoms, as the nitrogen of an amine: atom 1 plus a times the vector to each of the others.",
  VSITE4S: "Atom 2 in the middle: a for the vector to atom 1 and b for those to atoms 3 and 4.",
  VSITE4: "Atom 2 in the middle: a, b and c for the vectors to atoms 1, 3 and 4.",
};

/**
 * The bounds that are proposed for a parameter.
 * @param {string} name The name of the parameter.
 * @returns {number[]} The minimum and the maximum.
 */
function proposed(name) {
  if (name === "vs2a") return [-0.5, 0.5];
  if (name.startsWith("vs2fd") || name === "vs3fd_b") return [0, 0.1];     // a distance in nm
  if (name.endsWith("_c")) return [-12, 12];                                // out of the plane, 1/nm
  return [0, 0.4];
}

/* ---------- where a site is ---------- */

const add = (a, b, s = 1) => a.map((v, i) => v + s * b[i]);
const sub = (a, b) => add(a, b, -1);
const scale = (a, s) => a.map(v => v * s);
const norm = a => Math.hypot(...a);
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];

/**
 * The positions of the sites of one definition.
 * @param {string} kind The kind of site.
 * @param {object} p    The value of each parameter, by name.
 * @param {number[][]} x The positions of the atoms in nm, in the order of the definition.
 * @returns {number[][]} One position, or two for sites out of a plane.
 */
export function sitePositions(kind, p, x) {
  const out = (a, b, c) => {
    const d1 = sub(x[0], x[1]), d3 = sub(x[2], x[1]), n = cross(d1, d3);
    const base = add(add(x[1], scale(d1, a)), scale(d3, b));
    return [add(base, scale(n, c)), add(base, scale(n, -c))];
  };
  const four = (centre, others, w) => {
    let pos = scale(centre, 1 - w.reduce((s, v) => s + v, 0));
    others.forEach((o, i) => { pos = add(pos, scale(o, w[i])); });
    return [pos];
  };
  switch (kind) {
    case "VSITE2": return [add(x[0], scale(sub(x[1], x[0]), p.vs2a))];
    case "VSITE2FD": { const d = sub(x[1], x[0]); return [add(x[0], scale(d, p.vs2fd_a / (norm(d) || 1)))]; }
    case "VSITE3S": return four(x[1], [x[0], x[2]], [p.vs3sa, p.vs3sa]);
    case "VSITE3": return four(x[1], [x[0], x[2]], [p.vs3a, p.vs3b]);
    case "VSITE3FD": {
      const t = add(sub(x[0], x[1]), scale(sub(x[2], x[1]), p.vs3fd_a));
      return [add(x[1], scale(t, p.vs3fd_b / (norm(t) || 1)))];
    }
    case "VSITE3OUTS": return out(p.vs3outs_a, p.vs3outs_a, p.vs3outs_c);
    case "VSITE3OUT": return out(p.vs3out_a, p.vs3out_b, p.vs3out_c);
    case "VSITE4S3": return four(x[0], [x[1], x[2], x[3]], [p.vs4s3a, p.vs4s3a, p.vs4s3a]);
    case "VSITE4S": return four(x[1], [x[0], x[2], x[3]], [p.vs4sa, p.vs4sb, p.vs4sb]);
    case "VSITE4": return four(x[1], [x[0], x[2], x[3]], [p.vs4a, p.vs4b, p.vs4c]);
    default: return [];
  }
}

/* ---------- the viewer ---------- */

/**
 * Make the viewer, once.
 * @returns {object|null} The viewer, null if the browser has no WebGL.
 */
function viewer() {
  if (V || triedV) return V;
  triedV = true;
  const host = $("#gl2");
  try {
    if (typeof $3Dmol === "undefined") throw new Error("no viewer");
    V = $3Dmol.createViewer(host, { backgroundColor: css("--stage"), antialias: true });
    if (!V) throw new Error("no viewer");
  } catch (e) {
    V = null;
    host.parentNode.appendChild(el("div", { class: "nogl" }, "The 3D view needs WebGL, which this browser does not offer here. The atoms can be picked in the table."));
  }
  return V;
}

/** The value of each parameter of the kind that is chosen. */
const currentValues = () => Object.fromEntries(Object.entries(state.values).map(([name, v]) => [name, v.value]));

/** The positions of the sites as they would be now, in nm; none if the atoms do not fit the kind. */
function previewPositions() {
  const kind = state.kinds.find(k => k.name === $("#vs-kind").value);
  if (!state.mol || !kind || state.picked.length !== kind.atoms) return [];
  return sitePositions(kind.name, currentValues(), state.picked.map(i => state.mol.xyz[i]));
}

/**
 * Draw the molecule with the atoms that were picked and the site.
 * @param {boolean} zoom Whether to fit the molecule in the view.
 */
function scene(zoom = false) {
  const v = viewer();
  if (!v || !state.mol) return;
  v.removeAllModels(); v.removeAllShapes(); v.removeAllLabels();
  v.setBackgroundColor(css("--stage"));
  const A = 10;   // nm to Å, in which the viewer works
  const atoms = state.mol.atoms.map((at, i) => ({ elem: at.elem, atom: at.elem + (i + 1), serial: i + 1,
    x: state.mol.xyz[i][0] * A, y: state.mol.xyz[i][1] * A, z: state.mol.xyz[i][2] * A, bonds: [], bondOrder: [], hetflag: true }));
  state.mol.bonds.forEach(([i, j, o]) => {
    const order = Math.max(1, Math.round(o));
    atoms[i].bonds.push(j); atoms[i].bondOrder.push(order); atoms[j].bonds.push(i); atoms[j].bondOrder.push(order);
  });
  const model = v.addModel();
  model.addAtoms(atoms);
  model.setStyle({}, { stick: { radius: 0.13 }, sphere: { scale: 0.26 } });
  // A click on an atom picks it
  model.setClickable({}, true, atom => pick(atom.serial - 1));
  state.picked.forEach((i, order) => {
    const c = { x: atoms[i].x, y: atoms[i].y, z: atoms[i].z };
    v.addSphere({ center: c, radius: 0.6, color: css("--accent"), opacity: 0.35 });
    v.addLabel(String(order + 1), { position: c, fontSize: 13, fontColor: css("--ink"), backgroundOpacity: 0, inFront: true });
  });
  for (const pos of previewPositions()) {
    v.addSphere({ center: { x: pos[0] * A, y: pos[1] * A, z: pos[2] * A }, radius: 0.22, color: css("--warn") });
  }
  if (zoom) v.zoomTo();
  v.render();
}

/* ---------- the form ---------- */

/**
 * The atom types of a new force field that an atom can have: those of its
 * element that make bonds.
 * @param {number} i The atom.
 * @returns {string[]} The names.
 */
const possibleTypes = i => state.atomtypes.filter(t => t.element === state.mol.atoms[i].elem && t.bonds > 0).map(t => t.name);

/**
 * The atom type of an atom in the definition of a site. The force field
 * that the molecule was loaded with may have other atom types than a new
 * force field; then the first type of the element is proposed, and the
 * user chooses.
 * @param {number} i The atom.
 * @returns {string} The name of the atom type.
 */
function typeOf(i) {
  if (state.types.has(i)) return state.types.get(i);
  const possible = possibleTypes(i), given = state.mol.atoms[i].type;
  return possible.includes(given) || !possible.length ? given : possible[0];
}

/** Fill the table of atoms, with the order in which they were picked. */
function atomTable() {
  const table = $("#vs-atoms");
  table.textContent = "";
  $("#vs-empty").hidden = !!state.mol;
  if (!state.mol) return;
  table.append(el("thead", {}, el("tr", {}, el("th", { class: "n" }, "Picked"), el("th", {}, "Atom"), el("th", {}, "Type"), el("th", {}, "Bonded to"))));
  const body = el("tbody");
  state.mol.atoms.forEach((at, i) => {
    const order = state.picked.indexOf(i);
    const bonded = state.mol.bonds.filter(b => b[0] === i || b[1] === i).map(b => { const j = b[0] === i ? b[1] : b[0]; return state.mol.atoms[j].elem + (j + 1); });
    body.append(el("tr", { class: "pick" + (order >= 0 ? " sel" : ""), data: { i: String(i) } },
      el("td", { class: "n order" }, order >= 0 ? String(order + 1) : ""), el("td", {}, at.elem + (i + 1)),
      el("td", { class: "num" }, order >= 0 ? typeChoice(i) : typeOf(i)), el("td", {}, bonded.join(", "))));
  });
  table.append(body);
}

/**
 * The list from which the atom type of a picked atom is chosen.
 * @param {number} i The atom.
 * @returns {Element|string} The list, or the name if there is no choice.
 */
function typeChoice(i) {
  const possible = possibleTypes(i), current = typeOf(i);
  if (possible.length < 2) return current;
  const select = el("select", { class: "small", data: { atom: String(i) }, "aria-label": `Atom type of ${state.mol.atoms[i].elem}${i + 1}` });
  for (const name of possible) select.append(el("option", { value: name, selected: name === current }, name));
  return select;
}

/** Offer the kinds of site that the number of picked atoms allows. */
function kindList() {
  const select = $("#vs-kind"), before = select.value;
  const fitting = state.kinds.filter(k => k.atoms === state.picked.length);
  select.textContent = "";
  for (const kind of fitting) select.append(el("option", { value: kind.name }, kind.name));
  select.disabled = fitting.length === 0;
  if (fitting.some(k => k.name === before)) select.value = before;
  kindChosen(select.value !== before);
}

/**
 * Show the parameters of the kind that is chosen.
 * @param {boolean} fresh Whether to start from the proposed bounds.
 */
function kindChosen(fresh = true) {
  const kind = state.kinds.find(k => k.name === $("#vs-kind").value), host = $("#vs-params");
  $("#vs-help").textContent = kind ? KIND_HELP[kind.name]
    : "Pick two atoms for a site on their line, three for a site in or out of their plane, four for a site inside a group such as an amine.";
  $("#vs-add").disabled = !kind;
  host.textContent = "";
  if (!kind) { state.values = {}; return; }
  if (fresh || kind.parameters.some(name => !(name in state.values))) {
    state.values = {};
    for (const name of kind.parameters) {
      const [min, max] = proposed(name);
      // Out of the plane the preview starts away from it, where the two sites can be told apart
      state.values[name] = { min, max, value: min + (name.endsWith("_c") ? 0.75 : 0.5) * (max - min) };
    }
  }
  for (const name of kind.parameters) {
    const v = state.values[name];
    const slider = el("input", { type: "range", min: String(v.min), max: String(v.max), step: "any", value: String(v.value),
                                 data: { name, part: "value" }, "aria-label": "Preview of " + name });
    const shown = el("span", { class: "lab", data: { shown: name } }, "preview at " + num(v.value, 3));
    host.append(el("div", { class: "vsparam" }, el("span", { class: "name" }, name),
      el("div", { class: "slide" }, shown, slider),
      el("div", { class: "field" }, el("span", { class: "lab" }, "Minimum"), el("input", { type: "number", step: "any", value: String(v.min), data: { name, part: "min" }, "aria-label": "Minimum of " + name })),
      el("div", { class: "field" }, el("span", { class: "lab" }, "Maximum"), el("input", { type: "number", step: "any", value: String(v.max), data: { name, part: "max" }, "aria-label": "Maximum of " + name }))));
  }
}

/**
 * Take a change in the form of the parameters.
 * @param {Element} input The field that changed.
 */
function parameterChanged(input) {
  const v = state.values[input.dataset.name];
  if (!v) return;
  const number = input.valueAsNumber;
  if (!Number.isFinite(number)) return;
  v[input.dataset.part] = number;
  if (input.dataset.part !== "value") {
    // The slider runs between the bounds
    const slider = $(`#vs-params input[type="range"][data-name="${input.dataset.name}"]`);
    if (v.min <= v.max) {
      slider.min = String(v.min); slider.max = String(v.max);
      v.value = Math.min(v.max, Math.max(v.min, v.value));
      slider.value = String(v.value);
    }
  }
  const shown = $(`#vs-params [data-shown="${input.dataset.name}"]`);
  if (shown) shown.textContent = "preview at " + num(v.value, 3);
  scene();
}

/**
 * Pick an atom, or let go of it.
 * @param {number} i The atom.
 */
function pick(i) {
  const at = state.picked.indexOf(i);
  if (at >= 0) state.picked.splice(at, 1);
  else if (state.picked.length < 4) state.picked.push(i);
  else { toast("A site is made from at most four atoms."); return; }
  atomTable();
  kindList();
  scene();
}

/** Show the sites that were added. */
function siteList() {
  const list = $("#vs-list");
  list.textContent = "";
  if (!state.sites.length) list.append(el("li", { class: "none" }, "None yet."));
  state.sites.forEach((site, i) => {
    const bounds = Object.entries(site.parameters).map(([name, b]) => `${name} ${num(b.min)} to ${num(b.max)}`).join(", ");
    list.append(el("li", {}, el("span", {}, `${site.type} · ${site.interaction} · ${site.atoms.join(", ")} · ${bounds}`),
      el("button", { class: "btn small", type: "button", data: { remove: String(i) }, "aria-label": "Remove " + site.type }, "Remove")));
  });
  $("#vs-count").textContent = state.sites.length ? String(state.sites.length) : "";
  $("#vs-save").hidden = !state.sites.length;
  if (state.onChange) state.onChange();
}

/** Add the site of the form to the sites for the new force field. */
function addSite() {
  const kind = state.kinds.find(k => k.name === $("#vs-kind").value), name = $("#vs-type").value.trim();
  if (!kind || state.picked.length !== kind.atoms) return;
  if (!/^v[A-Za-z0-9]{1,14}$/.test(name)) { toast("The name of the site type is a v and up to 14 letters and digits, such as v2o1."); return; }
  const parameters = {};
  for (const param of kind.parameters) {
    const v = state.values[param];
    if (!(v.min <= v.max)) { toast(`The minimum of ${param} is larger than its maximum.`); return; }
    parameters[param] = { min: v.min, max: v.max };
  }
  // On the bond types of the atoms, so that it holds for every molecule
  const atoms = state.picked.map(i => typeOf(i) + "_b");
  state.sites.push({ type: name, interaction: kind.name, atoms, parameters });
  state.picked = [];
  atomTable(); kindList(); scene(); siteList();
  toast(`Added ${name}. It is used when a force field is generated.`);
}

/** Load the molecule of the input line with the force field of the workspace. */
async function loadMolecule() {
  const smiles = $("#vs-smiles").value.trim();
  if (!smiles) { toast("Give a SMILES string."); return; }
  const button = $("#vs-load"), current = session.forcefield;
  button.disabled = true; button.textContent = "Loading…";
  alertBox("");
  try {
    const job = await runTask({ task: "charges", molecule: { smiles }, forcefield: current ? current.ref : {} });
    const compound = job.result.molecules[0], particles = compound.molecule.particles;
    const index = [], local = new Map();
    particles.forEach((p, i) => { if (p.particletype === "Atom") { local.set(i, index.length); index.push(i); } });
    state.mol = {
      atoms: index.map(i => ({ elem: particles[i].element, type: particles[i].type })),
      bonds: compound.molecule.bonds.filter(b => local.has(b.atoms[0]) && local.has(b.atoms[1]))
        .map(b => [local.get(b.atoms[0]), local.get(b.atoms[1]), b.order]),
      xyz: index.map(i => compound.coordinates.positions[i]),
    };
    state.picked = [];
    state.types = new Map();
    $("#vs-ffnote").textContent = "A site is defined on the types of the atoms, so it is made in every molecule that has these atoms bonded in this way. " +
      `The types that are proposed are those that ${current ? current.label : "the default force field"} gives this molecule where a new force field can have them; ` +
      "choose another type for a picked atom in the table if needed, for instance ow and hw for water. The types have to be selected under Generate.";
    atomTable(); kindList(); scene(true);
  } catch (e) {
    alertBox("The molecule could not be loaded. " + e.message);
  } finally { button.disabled = false; button.textContent = "Load"; }
}

/**
 * The sites that were added.
 * @returns {object[]} The definitions, as the server takes them.
 */
export const chosenSites = () => state.sites.map(site => ({ ...site }));

/** Draw the molecule again, after the tab was shown or the size changed. */
export function redrawVsites() {
  if (V) { V.resize(); scene(); }
  else if (state.mol) scene(true);
}

/**
 * Make this part of the page work.
 * @param {object[]} kinds     The kinds of sites that can be defined.
 * @param {function} onChange  Called when the list of sites changes.
 */
export function startVsites(kinds, atomtypes, onChange = null) {
  state.kinds = kinds || [];
  state.atomtypes = atomtypes || [];
  state.onChange = onChange;
  $("#vs-load").onclick = loadMolecule;
  $("#vs-smiles").addEventListener("keydown", e => { if (e.key === "Enter") loadMolecule(); });
  $("#vs-clear").onclick = () => { state.picked = []; atomTable(); kindList(); scene(); };
  $("#vs-atoms").addEventListener("click", e => {
    // A click on the list of atom types chooses a type, not the atom
    if (e.target.closest("select")) return;
    const row = e.target.closest("tr.pick");
    if (row) pick(+row.dataset.i);
  });
  $("#vs-atoms").addEventListener("change", e => {
    if (e.target.dataset.atom) state.types.set(+e.target.dataset.atom, e.target.value);
  });
  $("#vs-kind").onchange = () => { kindChosen(true); scene(); };
  $("#vs-params").addEventListener("input", e => { if (e.target.dataset.name) parameterChanged(e.target); });
  $("#vs-add").onclick = addSite;
  $("#vs-list").addEventListener("click", e => {
    const button = e.target.closest("button[data-remove]");
    if (!button) return;
    state.sites.splice(+button.dataset.remove, 1);
    siteList();
  });
  $("#vs-save").onclick = () => {
    const text = JSON.stringify({ description: "Virtual sites made in the user interface of ACT", virtual_sites: state.sites }, null, 2) + "\n";
    const url = URL.createObjectURL(new Blob([text], { type: "application/json" })), link = el("a", { href: url, download: "vsites.json" });
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    toast("Saved vsites.json");
  };
  window.addEventListener("resize", () => { if (V && !$("#fftab-vsite").hidden) { V.resize(); V.render(); } });
  siteList();
}
