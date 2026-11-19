/*
 * This source file is part of the Alexandria Chemistry Toolkit.
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; either version 2
 * of the License, or (at your option) any later version.
 */
/**
 * @file Small plots drawn as SVG, all to one design: lines, filled lines and
 * the lines under a spectrum that select a mode. The plots follow the width
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

/**
 * Draw a plot and remember it, such that it can be drawn again when the
 * size of the page changes.
 * @param {string} target The id of the element to draw in.
 * @param {object} cfg    label, xlim, ylim, xlabel, ylabel, series and
 *                        optionally height, aspect, yfmt and onPick.
 */
export function plot(target, cfg) { plots.set(target, cfg); drawPlot(target, cfg); }

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
  const [x0, x1] = cfg.xlim, [y0, y1] = cfg.ylim;
  const X = v => m.l + (v - x0) / (x1 - x0) * (W - m.l - m.r);
  const Y = v => H - m.b - (v - y0) / (y1 - y0) * (H - m.t - m.b);
  const ink = css("--ink"), muted = css("--muted"), line = css("--line");
  const minus = v => String(v).replace("-", "−");
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(cfg.label)}" font-family="IBM Plex Mono, ui-monospace, monospace" font-size="11">`;
  for (const v of ticks(y0, y1, 4)) {
    s += `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="${line}" stroke-width="1"/>`;
    s += `<text x="${m.l - 8}" y="${Y(v) + 4}" text-anchor="end" fill="${muted}">${minus(cfg.yfmt ? cfg.yfmt(v) : v)}</text>`;
  }
  for (const v of ticks(x0, x1, W < 420 ? 4 : 6)) {
    s += `<line x1="${X(v)}" x2="${X(v)}" y1="${H - m.b}" y2="${H - m.b + 5}" stroke="${muted}"/>`;
    s += `<text x="${X(v)}" y="${H - m.b + 18}" text-anchor="middle" fill="${muted}">${minus(v)}</text>`;
  }
  s += `<line x1="${m.l}" x2="${W - m.r}" y1="${H - m.b}" y2="${H - m.b}" stroke="${muted}"/>`;
  s += `<text x="${(m.l + W - m.r) / 2}" y="${H - 6}" text-anchor="middle" fill="${ink}" font-family="IBM Plex Sans, Helvetica Neue, Arial, sans-serif" font-size="12">${esc(cfg.xlabel)}</text>`;
  s += `<text transform="translate(12 ${(m.t + H - m.b) / 2}) rotate(-90)" text-anchor="middle" fill="${ink}" font-family="IBM Plex Sans, Helvetica Neue, Arial, sans-serif" font-size="12">${esc(cfg.ylabel)}</text>`;
  for (const p of cfg.series) {
    const col = css(p.color || "--accent");
    if (p.type === "line" && p.x.length) {
      const d = p.x.map((v, i) => `${i ? "L" : "M"}${X(v).toFixed(1)} ${Y(p.y[i]).toFixed(1)}`).join("");
      if (p.fill) s += `<path d="${d}L${X(p.x[p.x.length - 1]).toFixed(1)} ${Y(y0)}L${X(p.x[0]).toFixed(1)} ${Y(y0)}Z" fill="${col}" fill-opacity="0.12" stroke="none"/>`;
      s += `<path d="${d}" fill="none" stroke="${col}" stroke-width="${p.width || 1.6}" stroke-linejoin="round"/>`;
      if (p.endpoint) { const i = p.x.length - 1; s += `<circle cx="${X(p.x[i])}" cy="${Y(p.y[i])}" r="3.5" fill="${col}"/>`; }
    } else if (p.type === "sticks") {
      p.x.forEach((v, i) => {
        if (v < x0 || v > x1) return;
        const on = i === p.selected;
        s += `<line data-i="${i}" class="pickable" x1="${X(v)}" x2="${X(v)}" y1="${H - m.b - 1}" y2="${H - m.b - (on ? 26 : 12)}" stroke="${on ? col : muted}" stroke-width="${on ? 3 : 2}"/>`;
        // A wider line that cannot be seen, to make the stick easier to hit
        s += `<line data-i="${i}" class="pickable" x1="${X(v)}" x2="${X(v)}" y1="${H - m.b - 1}" y2="${H - m.b - 30}" stroke="${col}" stroke-opacity="0" stroke-width="9"/>`;
      });
    }
  }
  host.innerHTML = s + "</svg>";
  host.onclick = cfg.onPick ? e => {
    const i = e.target.getAttribute && e.target.getAttribute("data-i");
    if (i !== null) cfg.onPick(+i);
  } : null;
}
