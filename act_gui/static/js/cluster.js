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
 * name there and a working directory, and can change how ACT is made
 * available in a job and where jobs go in Slurm. The server of the
 * interface keeps the settings in a file that it reads when it starts, and
 * logs in with the ssh program of this machine; the page never sees a
 * password or a key.
 *
 * All text that comes from the server is put on the page as text.
 */
import { $, alertBox, call, el, pill, toast } from "./common.js";

/** The fields of the form, by the name of the setting. */
const FIELDS = {
  server: "cl-server", user: "cl-user", workdir: "cl-workdir", load: "cl-load", load_name: "cl-loadname",
  partition: "cl-partition", gpu_partition: "cl-gpupartition", gpu_gres: "cl-gres", account: "cl-account",
  launcher: "cl-launcher", poll_seconds: "cl-poll",
};
/** What is asked for with each way of making ACT available. */
const LOAD_LABELS = { module: "Name of the module", source: "The file, with its path", conda: "Name of the environment" };

/** Show the field for what is loaded, with the question that fits the way. */
function loadKind() {
  const kind = $("#cl-load").value;
  $("#cl-loadfield").hidden = kind === "none";
  $("#cl-loadlabel").textContent = LOAD_LABELS[kind] || "";
}

/**
 * Show the settings that the server has.
 * @param {object} state What the server says: configured, cluster, proposed, file and problem.
 */
function show(state) {
  // What the user has not given yet starts with what is proposed
  const values = { server: "", user: "", workdir: "", ...state.proposed, ...(state.cluster || {}) };
  for (const [key, id] of Object.entries(FIELDS)) $("#" + id).value = values[key];
  loadKind();
  const chips = $("#cl-chips");
  chips.textContent = "";
  if (state.configured) {
    const c = state.cluster;
    chips.append(el("span", { class: "chip" }, "jobs go to ", el("b", {}, `${c.user}@${c.server}`)),
                 el("span", { class: "chip" }, "in ", el("b", {}, c.workdir)),
                 el("span", { class: "chip" }, "partitions ", el("b", {}, c.partition)),
                 el("span", { class: "chip" }, "with a GPU ", el("b", {}, `${c.gpu_partition}, ${c.gpu_gres}`)));
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
 * @returns {object} The settings, as the server takes them.
 */
function form() {
  const given = {};
  for (const [key, id] of Object.entries(FIELDS)) given[key] = $("#" + id).value.trim();
  // A whole number; what is no number is refused by the server with a message
  const seconds = Number(given.poll_seconds);
  given.poll_seconds = given.poll_seconds !== "" && Number.isInteger(seconds) ? seconds : given.poll_seconds;
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
  $("#cl-load").addEventListener("change", loadKind);
  // The settings that were saved are checked, so a change has to be saved first
  for (const id of Object.values(FIELDS)) {
    const field = $("#" + id), changed = () => { $("#cl-check").disabled = true; result(""); };
    field.addEventListener("input", changed);
    field.addEventListener("change", changed);
    if (field.tagName === "INPUT") field.addEventListener("keydown", e => { if (e.key === "Enter") save(); });
  }
  try {
    show(await (await call("/cluster")).json());
  } catch (e) {
    alertBox("The settings of the cluster could not be fetched. " + e.message);
  }
}
