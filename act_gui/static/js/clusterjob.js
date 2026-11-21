/*
 * This source file is part of the Alexandria Chemistry Toolkit.
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; either version 2
 * of the License, or (at your option) any later version.
 */
/**
 * @file The choice to run a calculation on this computer or as a job on
 * the cluster, with the settings of the job, the job as it would be sent,
 * and sending it. Used by the calculations of the molecule workspace that
 * can go to the cluster.
 *
 * All text that comes from the server is put on the page as text.
 */
import { alertBox, call, el, num, pill, seg, session, toast } from "./common.js";

/**
 * The size of a file in words.
 * @param {number} bytes The number of bytes.
 * @returns {string} The size in bytes, kB or MB.
 */
const fileSize = bytes => (bytes < 1024 ? `${bytes} bytes` : bytes < 1048576 ? `${num(bytes / 1024, 3)} kB` : `${num(bytes / 1048576, 3)} MB`);

/**
 * Add the choice and the card of the job to the page.
 * @param {string} prefix  The start of the identifiers of the elements, such as b2.
 * @param {Element} host   Where the choice goes.
 * @param {object} options processes: whether the job asks for a number of
 *                         processes; onChange: called when the place changes.
 * @returns {object} onCluster(), whether the job goes to the cluster;
 *                   settings(), what the job asks for; show(body), the job
 *                   as it would be sent; and update(), after the cluster changed.
 */
export function clusterChoice(prefix, host, { processes = true, onChange = () => {} } = {}) {
  const id = name => `${prefix}-${name}`;
  const place = el("div", { class: "seg", id: id("place"), role: "group", "aria-label": "Where it runs" },
    el("button", { type: "button", "aria-pressed": "true", data: { v: "here" } }, "On this computer"),
    el("button", { type: "button", "aria-pressed": "false", data: { v: "cluster" } }, "On the cluster"));
  const field = (label, input) => el("div", { class: "field" }, el("label", { for: input.id }, label), input);
  const name = el("input", { class: "mono", type: "text", id: id("jobname"), placeholder: "made from the date", maxLength: 64,
                             spellcheck: false, autocomplete: "off", autocapitalize: "off" });
  const tasks = el("input", { type: "number", id: id("tasks"), min: "1", max: "4096", step: "1", value: "1" });
  const hours = el("input", { type: "number", id: id("hours"), min: "0.25", max: "720", step: "0.25", value: "1" });
  const gpu = el("input", { type: "checkbox", id: id("gpu") });
  const opts = el("div", { class: "row", id: id("clusteropts"), hidden: true }, field("Name of the job", name),
    processes ? field("Processes", tasks) : "", field("Time limit (hours)", hours),
    el("div", { class: "field" }, el("span", { class: "lab" }, "Hardware"), el("label", { class: "check" }, gpu, " Needs a GPU")));
  const where = el("div", { class: "row top", id: id("where"), hidden: true },
    el("div", { class: "field" }, el("span", { class: "lab" }, "Run"), place), opts);
  // The job as it would be sent
  const files = el("table", { class: "data", id: id("jobfiles") });
  const script = el("pre", { class: "code", id: id("jobscript") });
  const taken = el("p", { class: "outcome bad", id: id("jobtaken"), hidden: true }, "There is a job with this name already. Give another name and show the job again.");
  const send = el("button", { class: "btn primary", type: "button", id: id("send") }, "Send the job");
  const nosend = el("button", { class: "btn", type: "button", id: id("nosend") }, "Do not send");
  const jobpill = el("span", { class: "pill", id: id("jobpill"), hidden: true });
  const whereto = el("span", { class: "muted", id: id("jobwhere") });
  const card = el("div", { class: "jobcard", id: id("job"), hidden: true },
    el("h3", {}, "Job for the cluster ", whereto),
    el("p", { class: "muted fine" }, "This is what would be sent. Nothing has been sent yet, and nobody has logged in."),
    taken, el("div", { class: "xscroll" }, files), script,
    el("div", { class: "row" }, send, nosend, jobpill),
    el("p", { class: "muted fine" }, "Sending logs in on the cluster once. After that the job is followed in the tab Cluster, also when this page was closed in between."));
  host.append(where, card);
  let pending = null;

  const onCluster = () => !where.hidden && place.querySelector('button[aria-pressed="true"]').dataset.v === "cluster";
  const update = () => {
    where.hidden = !session.clusterReady;
    opts.hidden = !onCluster();
    if (!onCluster()) { card.hidden = true; pending = null; }
    onChange();
  };
  seg(place, update);
  document.addEventListener("act-cluster", update);

  const settings = () => {
    const chosen = { minutes: Math.round(Number(hours.value) * 60), gpu: gpu.checked };
    if (processes) chosen.tasks = Number(tasks.value);
    if (name.value.trim()) chosen.name = name.value.trim();
    return chosen;
  };

  /**
   * Show the job as it would be sent.
   * @param {object} body The request of the calculation.
   */
  const show = async body => {
    const cluster = settings();
    const shown = await (await call("/cluster/jobs", { method: "POST", json: { ...body, cluster, send: false } })).json();
    // The name that was shown is the name that is sent
    pending = { ...body, cluster: { ...cluster, name: shown.name }, send: true };
    whereto.textContent = `${shown.name} · ${shown.server}:${shown.directory}`;
    files.textContent = "";
    files.append(el("thead", {}, el("tr", {}, el("th", {}, "Name on the cluster"), el("th", { class: "n" }, "Size"))),
      el("tbody", {}, ...shown.files.map(f => el("tr", {}, el("td", { class: "mono" }, f.name), el("td", { class: "n" }, fileSize(f.size))))));
    script.textContent = shown.script;
    taken.hidden = !shown.taken;
    send.disabled = shown.taken;
    pill(id("jobpill"), "");
    card.hidden = false;
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };

  send.onclick = async () => {
    if (!pending) return;
    send.disabled = true;
    alertBox("");
    pill(id("jobpill"), "Logging in and sending…");
    try {
      const sent = await (await call("/cluster/jobs", { method: "POST", json: pending })).json();
      pending = null;
      card.hidden = true;
      name.value = "";
      toast(`Sent ${sent.name} to the cluster, job ${sent.slurm} of Slurm`);
      // The tab Cluster follows the job from here
      document.dispatchEvent(new CustomEvent("act-cluster-jobs", { detail: { listing: sent, show: true } }));
    } catch (e) {
      pill(id("jobpill"), "Not sent", "bad");
      alertBox("The job was not sent. " + e.message);
      send.disabled = false;
    }
  };
  nosend.onclick = () => { card.hidden = true; pending = null; };
  update();
  return { onCluster, settings, show, update };
}
