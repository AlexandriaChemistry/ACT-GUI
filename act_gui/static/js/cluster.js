/*
 * This source file is part of the Alexandria Chemistry Toolkit.
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; either version 2
 * of the License, or (at your option) any later version.
 */
/**
 * @file The cluster for long jobs: the user gives the server, the user
 * name there and a working directory. The server of the interface keeps
 * them in a file that it reads when it starts, and logs in with the ssh
 * program of this machine; the page never sees a password or a key.
 *
 * All text that comes from the server is put on the page as text.
 */
import { $, alertBox, call, el, pill, toast } from "./common.js";

/** The fields of the form, by the name of the setting. */
const FIELDS = { server: "cl-server", user: "cl-user", workdir: "cl-workdir" };

/**
 * Show the settings that the server has.
 * @param {object} state What the server says: configured, cluster, file and problem.
 */
function show(state) {
  for (const [key, id] of Object.entries(FIELDS)) $("#" + id).value = state.cluster ? state.cluster[key] : "";
  const chips = $("#cl-chips");
  chips.textContent = "";
  if (state.configured) {
    const c = state.cluster;
    chips.append(el("span", { class: "chip" }, "jobs go to ", el("b", {}, `${c.user}@${c.server}`)),
                 el("span", { class: "chip" }, "in ", el("b", {}, c.workdir)));
  } else {
    chips.append(el("span", { class: "chip" }, "No cluster yet. Long jobs cannot be sent anywhere."));
  }
  $("#cl-file").textContent = state.file
    ? `These settings are kept in ${state.file} and read when the interface starts.`
    : "These settings are kept until the interface stops.";
  $("#cl-check").disabled = !state.configured;
  $("#cl-forget").hidden = !state.configured;
  if (state.problem) alertBox(state.problem);
}

/**
 * What the form says.
 * @returns {object} server, user and workdir.
 */
function form() {
  const given = {};
  for (const [key, id] of Object.entries(FIELDS)) given[key] = $("#" + id).value.trim();
  return given;
}

/** Keep the settings of the form. */
async function save() {
  const button = $("#cl-save");
  button.disabled = true;
  alertBox("");
  result("");
  try {
    show(await (await call("/cluster", { method: "PUT", json: form() })).json());
    toast("Saved the settings of the cluster");
  } catch (e) {
    alertBox("The settings were not saved. " + e.message);
  } finally { button.disabled = false; }
}

/**
 * Show the outcome of a check of the login.
 * @param {string} message The message, empty to show none.
 * @param {boolean} ok     Whether it worked.
 */
function result(message, ok = false) {
  const box = $("#cl-result");
  box.hidden = !message;
  box.textContent = message;
  box.className = "outcome " + (ok ? "good" : "bad");
  pill("cl-pill", message ? (ok ? "Ready" : "Not ready") : "", ok ? "good" : "bad");
}

/** Log in once with the settings that are kept, to see whether jobs can be sent. */
async function check() {
  const button = $("#cl-check");
  button.disabled = true;
  alertBox("");
  result("");
  pill("cl-pill", "Logging in…");
  try {
    const answer = await (await call("/cluster/check", { method: "POST" })).json();
    result(answer.message, answer.ok);
  } catch (e) {
    pill("cl-pill", "");
    alertBox("The login could not be checked. " + e.message);
  } finally { button.disabled = false; }
}

/** Forget the settings, also in the file. */
async function forget() {
  alertBox("");
  result("");
  try {
    show(await (await call("/cluster", { method: "DELETE" })).json());
    toast("The settings of the cluster are gone");
  } catch (e) { alertBox("The settings could not be removed. " + e.message); }
}

/**
 * Make this part of the page work. Called when the server has told who we are.
 * @param {object} told What the server said: whether it sends jobs to a cluster.
 */
export async function startCluster(told) {
  const tab = $('#nav button[data-view="cluster"]');
  // A server for several users has no cluster of one user
  tab.hidden = !told.cluster;
  if (!told.cluster) return;
  $("#cl-save").onclick = save;
  $("#cl-check").onclick = check;
  $("#cl-forget").onclick = forget;
  // The settings that were saved are checked, so a change has to be saved first
  for (const id of Object.values(FIELDS)) {
    $("#" + id).addEventListener("input", () => { $("#cl-check").disabled = true; result(""); });
    $("#" + id).addEventListener("keydown", e => { if (e.key === "Enter") save(); });
  }
  try {
    show(await (await call("/cluster")).json());
  } catch (e) {
    alertBox("The settings of the cluster could not be fetched. " + e.message);
  }
}
