/*
 * This source file is part of the Alexandria Chemistry Toolkit.
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; either version 2
 * of the License, or (at your option) any later version.
 */
/**
 * @file Small plots drawn as SVG, all to one design: lines, filled lines,
 * points and the lines under a spectrum that select a mode. The plots follow the width
 * of their element and the colours of the style sheet.
 */

/**
 * The value of a custom property of the style sheet.
 * @param {string} name The name, such as --accent.
 * @returns {string} The value.
 */
export const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/**
 * Round numbers for the ticks of an axis.
 * @param {number} lo The start of the axis.
 * @param {number} hi The end of the axis.
 * @param {number} n  About how many ticks.
 * @returns {number[]} The positions of the ticks.
 */
function ticks(lo, hi, n = 5) {
  const span = hi - lo || 1, step0 = Math.pow(10, Math.floor(Math.log10(span / n)));
  const err = span / n / step0;
  const step = step0 * (err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1);
  const t = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-6; v += step) t.push(+v.toFixed(10));
  return t;
}

/** The plots that are on the page, by the id of their element. */
const plots = new Map();
/** The part of a plot that is shown after zooming, by the id of its element: { xlim, ylim }. */
const views = new Map();

/**
 * Draw a plot and remember it, such that it can be drawn again when the
 * size of the page changes. A new plot is shown whole.
 * @param {string} target The id of the element to draw in.
 * @param {object} cfg    label, xlim, ylim, xlabel, ylabel, series and
 *                        optionally height, aspect, yfmt, onPick, zoom,
 *                        onPoint and marked. With xlim from a large to a
 *                        small value the x-axis runs from right to left.
 *                        A series with a name is in the legend. With zoom
 *                        the wheel zooms, dragging moves the plot and a
 *                        double click shows all of it again. onPoint(i, id)
 *                        is called with the series and the point that is
 *                        clicked, of series of points that are pickable:
 *                        the id of the point where the series has ids,
 *                        its place otherwise. marked { series, id } puts a
 *                        ring around a point.
 */
export function plot(target, cfg) { plots.set(target, cfg); views.delete(target); drawPlot(target, cfg); }

/**
 * Mark a point of a plot that is on the page, or none.
 * @param {string} target The id of the element of the plot.
 * @param {object|null} marked The series and the id or place of the point: { series, id }.
 */
export function markPoint(target, marked) {
  const cfg = plots.get(target);
  if (!cfg) return;
  cfg.marked = marked;
  drawPlot(target, cfg);
}

/**
 * Zoom a plot that is on the page, around its middle.
 * @param {string} target The id of the element of the plot.
 * @param {number} factor Below one to zoom in, above one to zoom out.
 */
export function zoomPlot(target, factor) {
  const cfg = plots.get(target);
  if (!cfg) return;
  const view = views.get(target) || { xlim: cfg.xlim, ylim: cfg.ylim };
  const middle = lim => (lim[0] + lim[1]) / 2;
  views.set(target, scaled(view, factor, middle(view.xlim), middle(view.ylim)));
  drawPlot(target, cfg);
}

/**
 * Show all of a plot again.
 * @param {string} target The id of the element of the plot.
 */
export function resetZoom(target) {
  views.delete(target);
  const cfg = plots.get(target);
  if (cfg) drawPlot(target, cfg);
}

/**
 * A part of a plot that is larger or smaller around a point.
 * @param {object} view   The part that is shown: { xlim, ylim }.
 * @param {number} factor How much larger.
 * @param {number} cx     The point in x that stays where it is.
 * @param {number} cy     The point in y that stays where it is.
 * @returns {object} The new part.
 */
function scaled(view, factor, cx, cy) {
  const around = (lim, c) => [c + (lim[0] - c) * factor, c + (lim[1] - c) * factor];
  return { xlim: around(view.xlim, cx), ylim: around(view.ylim, cy) };
}

/** Draw all plots again, after the size of the page or the theme changed. */
export function redrawPlots() { plots.forEach((cfg, t) => drawPlot(t, cfg)); }

/**
 * Limits for an axis that leave some room around the values.
 * @param {number[]} values The values.
 * @param {number} pad      The room as a fraction of the range.
 * @returns {number[]} The lower and upper limit.
 */
export function limits(values, pad = 0.08) {
  let lo = Math.min(...values), hi = Math.max(...values);
  if (!isFinite(lo) || !isFinite(hi)) return [0, 1];
  if (hi - lo < 1e-9) { const d = Math.abs(hi) * 0.05 || 1; return [lo - d, hi + d]; }
  const d = (hi - lo) * pad;
  return [lo - d, hi + d];
}

/**
 * Text that is safe to put in markup.
 * @param {*} text The text.
 * @returns {string} The text with the special characters of HTML replaced.
 */
export const esc = text => String(text).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/**
 * Draw one plot.
 * @param {string} target The id of the element.
 * @param {object} cfg    See plot().
 */
function drawPlot(target, cfg) {
  const host = document.getElementById(target);
  if (!host || !host.clientWidth) return;
  const W = host.clientWidth, H = Math.round(Math.min(cfg.height || 260, W * (cfg.aspect || 0.62)));
  const m = { l: 66, r: 14, t: 14, b: 40 };
  const view = views.get(target) || { xlim: cfg.xlim, ylim: cfg.ylim };
  const [x0, x1] = view.xlim, [y0, y1] = view.ylim;
  const X = v => m.l + (v - x0) / (x1 - x0) * (W - m.l - m.r);
  const Y = v => H - m.b - (v - y0) / (y1 - y0) * (H - m.t - m.b);
  const ink = css("--ink"), muted = css("--muted"), line = css("--line");
  const minus = v => String(v).replace("-", "−");
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(cfg.label)}" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="11">`;
  for (const v of ticks(y0, y1, 4)) {
    s += `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="${line}" stroke-width="1"/>`;
    s += `<text x="${m.l - 8}" y="${Y(v) + 4}" text-anchor="end" fill="${muted}">${minus(cfg.yfmt ? cfg.yfmt(v) : v)}</text>`;
  }
  const xlo = Math.min(x0, x1), xhi = Math.max(x0, x1);
  for (const v of ticks(xlo, xhi, W < 420 ? 4 : 6)) {
    s += `<line x1="${X(v)}" x2="${X(v)}" y1="${H - m.b}" y2="${H - m.b + 5}" stroke="${muted}"/>`;
    s += `<text x="${X(v)}" y="${H - m.b + 18}" text-anchor="middle" fill="${muted}">${minus(v)}</text>`;
  }
  s += `<line x1="${m.l}" x2="${W - m.r}" y1="${H - m.b}" y2="${H - m.b}" stroke="${muted}"/>`;
  s += `<text x="${(m.l + W - m.r) / 2}" y="${H - 6}" text-anchor="middle" fill="${ink}" font-family="IBM Plex Sans, Helvetica Neue, Arial, sans-serif" font-size="12">${esc(cfg.xlabel)}</text>`;
  s += `<text transform="translate(12 ${(m.t + H - m.b) / 2}) rotate(-90)" text-anchor="middle" fill="${ink}" font-family="IBM Plex Sans, Helvetica Neue, Arial, sans-serif" font-size="12">${esc(cfg.ylabel)}</text>`;
  // What lies outside the axes is not drawn, after zooming
  const clip = `clip-${target}`;
  s += `<defs><clipPath id="${clip}"><rect x="${m.l}" y="${m.t}" width="${W - m.l - m.r}" height="${H - m.t - m.b}"/></clipPath></defs><g clip-path="url(#${clip})">`;
  for (const p of cfg.series) {
    const col = css(p.color || "--accent");
    if (p.type === "line" && p.x.length) {
      const d = p.x.map((v, i) => `${i ? "L" : "M"}${X(v).toFixed(1)} ${Y(p.y[i]).toFixed(1)}`).join("");
      if (p.fill) s += `<path d="${d}L${X(p.x[p.x.length - 1]).toFixed(1)} ${Y(y0)}L${X(p.x[0]).toFixed(1)} ${Y(y0)}Z" fill="${col}" fill-opacity="0.12" stroke="none"/>`;
      s += `<path d="${d}" fill="none" stroke="${col}" stroke-width="${p.width || 1.6}" stroke-linejoin="round"${p.dash ? ' stroke-dasharray="5 4"' : ""}/>`;
      if (p.endpoint) { const i = p.x.length - 1; s += `<circle cx="${X(p.x[i])}" cy="${Y(p.y[i])}" r="3.5" fill="${col}"/>`; }
    } else if (p.type === "points") {
      // Many points in one path: a short line with round ends is a dot
      const d = p.x.map((v, i) => `M${X(v).toFixed(1)} ${Y(p.y[i]).toFixed(1)}h0`).join("");
      s += `<path d="${d}" fill="none" stroke="${col}" stroke-opacity="0.6" stroke-width="${p.size || 5}" stroke-linecap="round"/>`;
    } else if (p.type === "sticks") {
      p.x.forEach((v, i) => {
        if (v < xlo || v > xhi) return;
        const on = i === p.selected;
        s += `<line data-i="${i}" class="pickable" x1="${X(v)}" x2="${X(v)}" y1="${H - m.b - 1}" y2="${H - m.b - (on ? 26 : 12)}" stroke="${on ? col : muted}" stroke-width="${on ? 3 : 2}"/>`;
        // A wider line that cannot be seen, to make the stick easier to hit
        s += `<line data-i="${i}" class="pickable" x1="${X(v)}" x2="${X(v)}" y1="${H - m.b - 1}" y2="${H - m.b - 30}" stroke="${col}" stroke-opacity="0" stroke-width="9"/>`;
      });
    }
  }
  // The point that is marked: a ring around it
  const mark = cfg.marked && cfg.series[cfg.marked.series];
  if (mark) {
    const j = mark.ids ? mark.ids.indexOf(cfg.marked.id) : cfg.marked.id;
    if (j >= 0 && j < mark.x.length) {
      s += `<circle class="marked" cx="${X(mark.x[j]).toFixed(1)}" cy="${Y(mark.y[j]).toFixed(1)}" r="7" fill="none" stroke="${ink}" stroke-width="2"/>`;
    }
  }
  s += "</g>";
  // The legend: the series that have a name, at the top left
  cfg.series.filter(p => p.name).forEach((p, k) => {
    const y = m.t + 14 + k * 18, col = css(p.color || "--accent");
    s += `<g class="legend"><rect x="${m.l + 8}" y="${y - 11}" width="${String(p.name).length * 7 + 30}" height="16" rx="3" fill="${css("--surface")}" fill-opacity="0.85"/>`;
    s += p.type === "points" ? `<circle cx="${m.l + 18}" cy="${y - 3}" r="4" fill="${col}"/>`
      : `<line x1="${m.l + 12}" x2="${m.l + 24}" y1="${y - 3}" y2="${y - 3}" stroke="${col}" stroke-width="2"/>`;
    s += `<text x="${m.l + 30}" y="${y + 1}" fill="${ink}">${esc(p.name)}</text></g>`;
  });
  host.innerHTML = s + "</svg>";
  host.onclick = cfg.onPick ? e => {
    const i = e.target.getAttribute && e.target.getAttribute("data-i");
    if (i !== null) cfg.onPick(+i);
  } : null;
  // From a place on the screen to the values of the axes, on the plot as
  // it is now: dragging draws it again
  const at = e => {
    const box = host.firstChild.getBoundingClientRect(), k = box.width / W;
    return { px: (e.clientX - box.left) / k, py: (e.clientY - box.top) / k };
  };
  const value = ({ px, py }) => ({ x: x0 + (px - m.l) / (W - m.l - m.r) * (x1 - x0), y: y0 + (H - m.b - py) / (H - m.t - m.b) * (y1 - y0) });
  // The nearest point that can be picked, within ten pixels, of those that are shown
  const nearest = spot => {
    let best = null, limit = 100;
    const xlo2 = Math.min(x0, x1), xhi2 = Math.max(x0, x1);
    cfg.series.forEach((p, i) => {
      if (p.type !== "points" || !p.pickable) return;
      for (let j = 0; j < p.x.length; j++) {
        if (p.x[j] < xlo2 || p.x[j] > xhi2 || p.y[j] < y0 || p.y[j] > y1) continue;
        const dx = X(p.x[j]) - spot.px, dy = Y(p.y[j]) - spot.py, d = dx * dx + dy * dy;
        if (d < limit) { limit = d; best = { series: i, id: p.ids ? p.ids[j] : j }; }
      }
    });
    return best;
  };
  host.classList.toggle("zoomable", Boolean(cfg.zoom));
  host.classList.toggle("pointable", Boolean(cfg.onPoint));
  host.onwheel = cfg.zoom ? e => {
    e.preventDefault();
    const spot = value(at(e));
    views.set(target, scaled({ xlim: [x0, x1], ylim: [y0, y1] }, e.deltaY > 0 ? 1.25 : 0.8, spot.x, spot.y));
    drawPlot(target, cfg);
  } : null;
  host.ondblclick = cfg.zoom ? () => resetZoom(target) : null;
  host.onpointerdown = cfg.zoom || cfg.onPoint ? e => {
    if (e.button !== 0) return;
    const start = at(e), from = { xlim: [x0, x1], ylim: [y0, y1] };
    let moved = false;
    const move = ev => {
      const now = at(ev), dx = now.px - start.px, dy = now.py - start.py;
      if (!cfg.zoom || (!moved && dx * dx + dy * dy < 16)) return;
      moved = true;
      // The plot follows the pointer
      const sx = (x1 - x0) / (W - m.l - m.r) * dx, sy = (y1 - y0) / (H - m.t - m.b) * dy;
      views.set(target, { xlim: [from.xlim[0] - sx, from.xlim[1] - sx], ylim: [from.ylim[0] + sy, from.ylim[1] + sy] });
      drawPlot(target, cfg);
    };
    const up = ev => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      if (moved || !cfg.onPoint) return;
      const hit = nearest(at(ev));
      if (hit) cfg.onPoint(hit.series, hit.id);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  } : null;
}
