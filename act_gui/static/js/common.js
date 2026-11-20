/*
 * This source file is part of the Alexandria Chemistry Toolkit.
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; either version 2
 * of the License, or (at your option) any later version.
 */
/**
 * @file What the workspaces of the page share: requests to the server,
 * messages for the user, and the tabs and buttons.
 *
 * All text that comes from the server or from files is put on the page as
 * text, never as markup.
 */
import { redrawPlots } from "./plot.js";

export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];
/** A number with a real minus sign. */
export const fmt = (x, d = 2) => (x < 0 ? "−" : "") + Math.abs(x).toFixed(d);
/**
 * Make an element.
 * @param {string} tag       The name of the element.
 * @param {object} props     Properties to set; "class" and "data" are understood.
 * @param {...(Node|string)} children What goes inside, text as text.
 * @returns {Element} The element.
 */
export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key === "data") Object.assign(node.dataset, value);
    else if (key in node) node[key] = value;
    else node.setAttribute(key, value);
  }
  node.append(...children);
  return node;
}

/**
 * A number as it is shown in a table: a real minus sign, and no more
 * digits than asked for. Very small and very large numbers get an exponent.
 * @param {number} x      The number.
 * @param {number} digits The number of significant digits.
 * @returns {string} The text, empty for what is no number.
 */
export function num(x, digits = 6) {
  if (typeof x !== "number" || !isFinite(x)) return "";
  const size = Math.abs(x);
  const text = size !== 0 && (size < 1e-4 || size >= 1e9) ? size.toExponential(Math.max(0, digits - 1)).replace(/\.?0+e/, "e")
    : String(parseFloat(size.toPrecision(digits)));
  return (x < 0 ? "−" : "") + text;
}

/** What the server told the page when it started. */
export const session = {
  token: "",            // for requests that change something
  pdf: false,           // whether the server can make PDF files of plots
  forcefield: null,     // the force field of the force field workspace: { ref, label }
  clusterReady: false,  // whether the user has a cluster that jobs can be sent to
};

/* ---------- messages ---------- */

let toastTimer;
/**
 * Show a short message at the bottom of the page.
 * @param {string} msg The message.
 */
export function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("on"), 3600);
}

/**
 * Show an error above the workspace, or remove it.
 * @param {string} msg The message, empty to remove it.
 */
export function alertBox(msg) {
  const a = $("#alert");
  a.textContent = msg || "";
  a.hidden = !msg;
}

/* ---------- the server ---------- */

/**
 * Send a request to the server.
 * @param {string} path  The path below /api.
 * @param {object} opts  method, json (an object to send), body (a file), params.
 * @returns {Promise<object>} The answer.
 * @throws {Error} With the message of the server if it refuses.
 */
export async function call(path, { method = "GET", json, body, params } = {}) {
  const url = new URL("/api" + path, location.origin);
  for (const k in params || {}) url.searchParams.set(k, params[k]);
  const headers = { "X-ACT-Token": session.token };
  if (json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(json); }
  const answer = await fetch(url, { method, headers, body, credentials: "same-origin" });
  if (!answer.ok) {
    let detail = "The server answered with status " + answer.status;
    try { detail = (await answer.json()).detail || detail; } catch (e) { /* not JSON */ }
    throw new Error(detail);
  }
  return answer;
}

/**
 * Start a calculation and wait for its result.
 * @param {object} body       The request: the task and what it needs.
 * @param {function} onUpdate Called with the job each time it is asked for.
 * @returns {Promise<object>} The job when it has finished.
 * @throws {Error} If the calculation failed or was stopped.
 */
export async function runTask(body, onUpdate = null) {
  let job = await (await call("/jobs", { method: "POST", json: body })).json();
  let seen = 0;
  for (;;) {
    if (onUpdate) onUpdate(job);
    seen += job.progress.length;
    if (job.status === "finished") return job;
    if (job.status === "failed") throw new Error(job.error || "The calculation failed");
    if (job.status === "cancelled") throw new Error("The calculation was stopped");
    await new Promise(r => setTimeout(r, 250));
    job = await (await call("/jobs/" + job.id, { params: { progress_from: seen } })).json();
  }
}

/**
 * Send a file to the server.
 * @param {File} file   The file.
 * @param {string} kind molecule, forcefield or molprop.
 * @returns {Promise<object>} The identifier and the label of the file.
 */
export async function upload(file, kind) {
  return (await call("/uploads", { method: "POST", body: file, params: { kind, name: file.name } })).json();
}

/**
 * Fetch a file that the server makes and hand it to the browser to save.
 * @param {Element} button  The button that was pressed.
 * @param {string} path     The path below /api.
 * @param {object} params   The parameters of the request.
 * @param {string} busy     The text of the button while the file is made.
 * @param {string} fallback The name of the file if the server gives none.
 * @returns {Promise<boolean>} Whether the file was saved.
 */
export async function saveFile(button, path, params, busy, fallback) {
  const label = button.textContent;
  button.disabled = true; button.textContent = busy;
  try {
    const answer = await call(path, { params });
    const named = /filename="?([^";]+)"?/.exec(answer.headers.get("content-disposition") || "");
    const name = named ? named[1] : fallback;
    const url = URL.createObjectURL(await answer.blob()), link = document.createElement("a");
    link.href = url; link.download = name;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    toast("Saved " + name);
    return true;
  } catch (e) {
    alertBox("The file could not be saved. " + e.message);
    return false;
  } finally { button.disabled = false; button.textContent = label; }
}

/* ---------- tabs and buttons ---------- */

/**
 * Put the underline of a row of tabs under the selected tab.
 * @param {Element} nav The row of tabs.
 */
export function moveInk(nav) {
  const b = $('button[aria-selected="true"]', nav), ink = $(".ink", nav);
  if (!b || !ink || !b.offsetWidth) return;
  if (!ink.dataset.placed) {
    // The first time without motion
    ink.style.transition = "none";
    ink.dataset.placed = "1";
    requestAnimationFrame(() => requestAnimationFrame(() => { ink.style.transition = ""; }));
  }
  ink.style.width = b.offsetWidth + "px";
  ink.style.transform = `translateX(${b.offsetLeft}px)`;
}

/**
 * Make a row of tabs work.
 * @param {Element} nav        The row of tabs.
 * @param {function} onSelect  Called with the name of the selected tab.
 */
export function tabs(nav, onSelect) {
  nav.addEventListener("click", e => {
    const b = e.target.closest("button");
    if (!b) return;
    $$("button", nav).forEach(x => x.setAttribute("aria-selected", x === b));
    moveInk(nav);
    onSelect(b.dataset.tab || b.dataset.view);
  });
}

/**
 * Make a group of buttons work of which one is pressed.
 * @param {Element} root     The group.
 * @param {function} onPick  Called with the value of the pressed button.
 */
export function seg(root, onPick) {
  root.addEventListener("click", e => {
    const b = e.target.closest("button");
    if (!b) return;
    $$("button", root).forEach(x => x.setAttribute("aria-pressed", x === b));
    onPick(b.dataset.v);
  });
}

/**
 * Show one of a set of elements.
 * @param {string[]} ids The ids of the elements.
 * @param {string} on    The id of the one to show.
 */
export function showOnly(ids, on) {
  ids.forEach(i => { const p = document.getElementById(i); if (p) p.hidden = i !== on; });
  requestAnimationFrame(() => { redrawPlots(); $$(".tabs").forEach(moveInk); });
}

/**
 * Show the state of a calculation in a pill.
 * @param {string} id    The id of the pill.
 * @param {string} text  The text, empty to hide the pill.
 * @param {string} kind  good, warn or bad.
 */
export function pill(id, text, kind = "") {
  const p = document.getElementById(id);
  p.hidden = !text;
  p.textContent = text;
  p.className = "pill" + (kind ? " " + kind : "");
}
