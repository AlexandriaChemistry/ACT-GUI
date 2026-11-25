/*
 * This source file is part of the Alexandria Chemistry Toolkit.
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; either version 2
 * of the License, or (at your option) any later version.
 */
/**
 * @file What a molecule has besides its atoms, for the viewers: the shells
 * that make atoms polarizable, the virtual sites, and the distributions of
 * the charges.
 *
 * A shell sits on its atom or very close to it, where a small sphere would
 * be inside that of the atom: it is drawn as a transparent sphere around
 * it, a halo that moves off the atom when the shell does. The halos are
 * left out when the charge distributions are drawn, which have that of the
 * shell in them and would hide them. A virtual site
 * is a small sphere with a line to the atom it belongs to. Charges with a
 * Gaussian or Slater distribution are drawn as a transparent sphere on each
 * atom and each virtual site, with the width of the distribution, 1/zeta,
 * as its radius, in the colour of the sign of the charge, see clouds.
 *
 * The particles are those of the description of a molecule by ACT, see
 * moleculeToJson: each has its name, type, particletype (Atom, Shell or
 * VSite), charge, perhaps a second charge (charge_vs) and, if it is no
 * atom, the atoms it belongs to (parents). The coordinates are those of
 * all particles, in the same order.
 */
import { css } from "./plot.js";

/** The radius of the sphere of a shell, larger than that of an atom in a ball and stick model, and of a virtual site in Å. */
const SHELL_RADIUS = 0.58, SITE_RADIUS = 0.2;
/** How much of what is behind the sphere of a shell shows through. */
const SHELL_OPACITY = 0.45;
/** The smallest and the largest radius of the sphere of a charge distribution in Å. */
const CLOUD_RADIUS = [0.15, 3];
/** How much of what is behind the sphere of a charge distribution shows through. */
const CLOUD_OPACITY = 0.3;
/** The smallest charge in e of which the distribution is drawn. */
const SMALL_CHARGE = 1e-6;

/**
 * Whether a particle is an atom.
 * @param {object} p The particle.
 * @returns {boolean} Whether it is one.
 */
export const isAtom = p => p.particletype === "Atom";

/**
 * Whether a particle is a shell.
 * @param {object} p The particle.
 * @returns {boolean} Whether it is one.
 */
export const isShell = p => p.particletype === "Shell";

/**
 * Whether a particle is a virtual site: what is no atom and no shell.
 * @param {object} p The particle.
 * @returns {boolean} Whether it is one.
 */
export const isSite = p => !isAtom(p) && !isShell(p);

/**
 * What a molecule has to show besides its atoms.
 * @param {object[]} particles The particles.
 * @returns {object} The number of shells, of virtual sites and of second charges.
 */
export function countExtras(particles) {
  const list = particles || [];
  return { shells: list.filter(isShell).length, sites: list.filter(isSite).length,
           second: list.filter(p => p.charge_vs).length };
}

/**
 * The positions of all particles in the structures of a pdb file that
 * alexandria wrote with the option -ws: each structure has a line for
 * every particle, in the order of the particles.
 * @param {string} text  The pdb file.
 * @param {number} count The number of particles.
 * @returns {number[][][]|null} For each structure the position of each
 * particle in Å; null if the structures do not have a line for each
 * particle, as those of a file without the shells and virtual sites.
 */
export function pdbFrames(text, count) {
  const frames = [];
  let frame = [];
  const close = () => { if (frame.length) frames.push(frame); frame = []; };
  for (const line of text.split(/\r?\n/)) {
    if (line.startsWith("ATOM") || line.startsWith("HETATM")) {
      const xyz = [line.slice(30, 38), line.slice(38, 46), line.slice(46, 54)].map(Number);
      if (xyz.some(v => !Number.isFinite(v))) return null;
      frame.push(xyz);
    } else if (line.startsWith("ENDMDL") || line.startsWith("MODEL")) close();
  }
  close();
  return frames.length && frames.every(f => f.length === count) ? frames : null;
}

/**
 * The lines of a pdb file with all particles that belong to atoms: the
 * file without its shells and virtual sites, as the viewer reads it.
 * @param {string} text        The pdb file, see pdbFrames.
 * @param {object[]} particles The particles.
 * @returns {string} The file with the atoms only. The numbers of the atoms are kept, so that the bonds still hold.
 */
export function pdbAtoms(text, particles) {
  let k = 0;
  return text.split(/\r?\n/).filter(line => {
    if (line.startsWith("MODEL") || line.startsWith("ENDMDL")) { k = 0; return true; }
    if (!line.startsWith("ATOM") && !line.startsWith("HETATM")) return true;
    const p = particles[k];
    k += 1;
    return !p || isAtom(p);
  }).join("\n");
}

/**
 * The charge distributions that are drawn: one for each atom and one for
 * each virtual site. The charge of an atom, that of its shell and its
 * second charge are in the same place, often with the same width and the
 * opposite sign, so one sphere for each of them would hide the others. An
 * atom gets one sphere for what is distributed of them together, as wide
 * as the widest, in the colour of the sign of their sum.
 * @param {object[]} particles The particles.
 * @param {object} widths      The distributions of the force field, see the task ff_charges: the
 * kind of distribution and the zeta of each particle type; null to draw none.
 * @returns {object[]} For each the particle it is drawn on, its radius in Å and its charge in e.
 */
function clouds(particles, widths) {
  if (!widths || !widths.types) return [];
  const groups = new Map();
  const add = (owner, k, charge, zeta) => {
    // A zeta of zero is a point charge
    if (!(zeta > 0) || !(Math.abs(charge) > SMALL_CHARGE)) return;
    const group = groups.get(owner) || { k, zeta, charge: 0 };
    // On the particle with the widest distribution, the atom if its shell is as wide
    if (zeta < group.zeta) { group.k = k; group.zeta = zeta; }
    group.charge += charge;
    groups.set(owner, group);
  };
  // The atoms first, so that a shell joins what its atom has
  const order = particles.map((p, k) => k).sort((a, b) => Number(!isAtom(particles[a])) - Number(!isAtom(particles[b])) || a - b);
  for (const k of order) {
    const p = particles[k], type = widths.types[p.type] || {};
    const parent = isShell(p) && (p.parents || []).length ? p.parents[0] : k;
    add(parent, k, p.charge, type.zeta);
    if (p.charge_vs) add(parent, k, p.charge_vs, type.zeta_vs);
  }
  return [...groups.values()].map(group => ({ k: group.k, charge: group.charge,
    radius: Math.min(CLOUD_RADIUS[1], Math.max(CLOUD_RADIUS[0], 10 / group.zeta)) }));
}

/**
 * A structure of some of the particles as a pdb file, for the viewer.
 * @param {object[]} entries     The particles to write: { k }, the place of each in the list of particles.
 * @param {number[][][]} frames  For each structure the position of each particle in Å.
 * @returns {string} The file, with a model for each structure and the entries numbered from one.
 */
function pdbOf(entries, frames) {
  const lines = [];
  frames.forEach((frame, n) => {
    lines.push("MODEL     " + String(n + 1).padStart(4));
    entries.forEach((entry, i) => {
      const [x, y, z] = frame[entry.k];
      lines.push("HETATM" + String(i + 1).padStart(5) + "  X   XXX A   1    " +
                 x.toFixed(3).padStart(8) + y.toFixed(3).padStart(8) + z.toFixed(3).padStart(8) + "  1.00  0.00           X");
    });
    lines.push("ENDMDL");
  });
  return lines.join("\n") + "\n";
}

/**
 * Put some of the particles in the viewer as a model of spheres.
 * @param {object} viewer       The viewer.
 * @param {object[]} entries    The particles: { k, radius, color }.
 * @param {number[][][]} frames For each structure the position of each particle in Å.
 * @param {number[][]} disp     The displacement of each particle for a vibration, of the one structure; null for none.
 * @param {number} opacity      How solid the spheres are, 1 for solid.
 * @returns {object|null} The model, null if there are no particles.
 */
function spheres(viewer, entries, frames, disp, opacity) {
  if (!entries.length) return null;
  let model;
  if (frames.length > 1) {
    model = viewer.addModelsAsFrames(pdbOf(entries, frames), "pdb", { keepH: true });
  } else {
    model = viewer.addModel();
    model.addAtoms(entries.map((entry, i) => {
      const [x, y, z] = frames[0][entry.k];
      const atom = { elem: "X", atom: "X" + (i + 1), serial: i + 1, x, y, z, bonds: [], bondOrder: [], hetflag: true };
      if (disp) [atom.dx, atom.dy, atom.dz] = disp[entry.k];
      return atom;
    }));
  }
  // Nothing of the model is drawn but what is asked for here
  model.setStyle({}, {});
  entries.forEach((entry, i) => {
    const sphere = { radius: entry.radius, color: entry.color };
    if (opacity < 1) sphere.opacity = opacity;
    model.setStyle({ serial: i + 1 }, { sphere });
  });
  if (disp) {
    model.frames = [model.atoms];   // vibrate() needs a first frame
    model.vibrate(12, 0.55, true);
  }
  return model;
}

/**
 * Draw the shells, the virtual sites and the charge distributions of a molecule.
 * @param {object} viewer       The viewer, with the atoms in it already.
 * @param {object[]} particles  The particles.
 * @param {number[][][]} frames For each structure the position of each particle in Å: one, or those of a trajectory.
 * @param {object} show         What to draw: sites (the shells and virtual sites, true or false), widths (the
 * charge distributions of the force field, null for none) and disp (the displacement of each particle of a
 * vibration of the one structure, which then moves, null for none).
 * @returns {object} How many shells, virtual sites and charge distributions were drawn.
 */
export function drawExtras(viewer, particles, frames, { sites = false, widths = null, disp = null } = {}) {
  const count = { shells: 0, sites: 0, clouds: 0 };
  if (!particles || !frames || !frames.length) return count;
  // What cancels has no sign: in the colour of what is neither
  const colour = charge => css(Math.abs(charge) <= SMALL_CHARGE ? "--series2" : charge < 0 ? "--neg" : "--pos");
  const drawn = clouds(particles, widths).map(cloud => ({ k: cloud.k, radius: cloud.radius, color: colour(cloud.charge) }));
  spheres(viewer, drawn, frames, disp, CLOUD_OPACITY);
  count.clouds = drawn.length;
  if (sites) {
    const shells = [], virtual = [];
    particles.forEach((p, k) => {
      // The viewer does not draw a transparent sphere inside another one: with the charge
      // distributions, which have that of the shell of an atom in them, the shells are left out
      if (isShell(p)) { if (!drawn.length) shells.push({ k, radius: SHELL_RADIUS, color: css("--shell") }); }
      else if (isSite(p)) virtual.push({ k, radius: SITE_RADIUS, color: css("--warn") });
    });
    spheres(viewer, shells, frames, disp, SHELL_OPACITY);
    spheres(viewer, virtual, frames, disp, 1);
    count.shells = shells.length; count.sites = virtual.length;
    if (frames.length === 1 && !disp) {
      // A line from each virtual site to the nearest of the atoms it belongs to
      const x = frames[0], at = k => ({ x: x[k][0], y: x[k][1], z: x[k][2] });
      for (const site of virtual) {
        const parents = (particles[site.k].parents || []).filter(i => x[i]);
        if (!parents.length) continue;
        const distance = i => Math.hypot(...x[i].map((v, d) => v - x[site.k][d]));
        const nearest = parents.reduce((best, i) => (distance(i) < distance(best) ? i : best));
        if (distance(nearest) > 1e-3) {
          viewer.addCylinder({ start: at(site.k), end: at(nearest), radius: 0.03, color: css("--warn"), dashed: true, fromCap: 1, toCap: 1 });
        }
      }
    }
  }
  return count;
}

/**
 * Whether a force field has charges with a distribution to draw.
 * @param {object} widths The distributions of the force field, see the task ff_charges.
 * @returns {boolean} Whether one of its particle types has a width.
 */
export const hasClouds = widths => Boolean(widths && widths.types &&
  Object.values(widths.types).some(type => type.zeta > 0 || type.zeta_vs > 0));
