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
 * Below the settings are the jobs that were sent to the cluster. The page
 * asks the server of the interface for their state; that server decides
 * when the cluster itself is asked, which is seldom.
 *
 * A job that failed tells why, with the lines of its output that tell it,
 * and what to change. Below the table one job at a time is shown: its
 * output, log and script as text, or the job sent again with other
 * processes, time or GPU, of which the files are copied on the cluster.
 *
 * All text that comes from the server is put on the page as text.
 */
import { $, alertBox, call, el, num, pill, saveFile, session, toast } from "./common.js";

/** The fields of the form, by the name of the setting. */
const FIELDS = {
  server: "cl-server", user: "cl-user", workdir: "cl-workdir", load: "cl-load", load_name: "cl-loadname",
  partition: "cl-partition", gpu_partition: "cl-gpupartition", gpu_gres: "cl-gres", account: "cl-account",
  launcher: "cl-launcher", program: "cl-program", poll_seconds: "cl-poll",
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
    chips.append(el("span", { class: "chip" }, "No cluster yet. Long jobs cannot be sent anywhere until one is set in the tab Settings."));
  }
  $("#cl-file").textContent = state.file
    ? `These settings are kept in ${state.file} and read when the interface starts.`
    : "These settings are kept until the interface stops.";
  $("#cl-check").disabled = !state.configured;
  $("#cl-forget").hidden = !state.configured;
  if (state.problem) alertBox(state.problem);
  // The calculations that can go to the cluster offer it from now on, or no more
  session.clusterReady = state.configured;
  document.dispatchEvent(new CustomEvent("act-cluster"));
}

/* ---------- the jobs on the cluster ---------- */

/** What the page calls the states of a job, and the colour of each. */
const STATES = {
  waiting: ["Waiting", ""], running: ["Running", "warn"], finished: ["Finished", "good"], failed: ["Failed", "bad"],
  lost: ["Ended without result", "bad"], cancelled: ["Stopped", ""],
};
/** The jobs as the server last told them, and the timer of the next question. */
const jobs = { listing: null, timer: null, busy: false, files: new Map() };  // files: the output files that are shown, by job
/** The job below the table: its name, what is shown (view or again) and the job to send again. */
const panel = { name: "", mode: "", pending: null };

/**
 * The size of a file for people.
 * @param {number} bytes The size.
 * @returns {string} The size in bytes, kB or MB.
 */
const size = bytes => (bytes < 1024 ? `${bytes} B` : bytes < 1048576 ? `${(bytes / 1024).toFixed(1)} kB` : `${(bytes / 1048576).toFixed(1)} MB`);

/** The files that are shown as text on the page. */
const TEXT_FILE = /\.(out|log|txt|err|sh|dat|json|xvg|csv)$/;

/**
 * The output files of a job with a button to save each.
 * @param {string} name   The name of the job.
 * @param {object[]} list The files: name and size.
 * @returns {Element} The list.
 */
function fileList(name, list) {
  if (!list.length) return el("p", { class: "muted fine" }, "The job has no output files here.");
  return el("ul", { class: "sites" }, ...list.map(file => el("li", {},
    el("span", {}, `${file.name} · ${size(file.size)}`),
    TEXT_FILE.test(file.name) ? el("button", { class: "btn small", type: "button", data: { act: "view", file: file.name, name } }, "View") : "",
    el("button", { class: "btn small", type: "button", data: { save: file.name, name } }, "Save"))));
}

/**
 * A time of today as hours and minutes, of another day with the date.
 * @param {number} seconds Seconds since the epoch.
 * @returns {string} The time, empty if there is none.
 */
function when(seconds) {
  if (!seconds) return "";
  const date = new Date(seconds * 1000), today = new Date().toDateString() === date.toDateString();
  const time = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return today ? time : `${date.toLocaleDateString([], { day: "numeric", month: "short" })} ${time}`;
}

/**
 * What a job asked for, in words.
 * @param {object} job The job.
 * @returns {string} The processes, the time limit and the GPU.
 */
function asked(job) {
  const hours = Math.floor(job.minutes / 60), minutes = job.minutes % 60;
  const time = [hours ? `${hours} h` : "", minutes ? `${minutes} min` : ""].filter(Boolean).join(" ");
  return `${job.tasks} ${job.tasks === 1 ? "process" : "processes"}, ${time}${job.gpu ? ", GPU" : ""}`;
}

/**
 * How far a job was when the cluster was last asked, in words.
 * @param {object} job The job.
 * @returns {string} The generation or the iteration and the lowest χ², empty for a job that does not tell.
 */
function progressOf(job) {
  const p = job.progress;
  if (!p) return "";
  const parts = [];
  if (p.event === "generation" && p.ngenerations > 1) parts.push(`generation ${p.generation} of ${p.ngenerations}`);
  else if (p.event === "generation") parts.push("training done");
  else if (p.event === "step" && p.iteration !== undefined) parts.push(`iteration ${Math.floor(p.iteration)}`);
  else if (p.event === "start") parts.push("started");
  if (p.chi2 !== undefined) parts.push(`χ² ${num(p.chi2, 5)}`);
  return parts.join(" · ");
}

/**
 * A small button in the row of a job.
 * @param {string} label The text.
 * @param {string} act   What it does: stop, fetch, open or forget.
 * @param {string} name  The name of the job.
 * @returns {Element} The button.
 */
const action = (label, act, name) => el("button", { class: "btn small", type: "button", data: { act, name } }, label);

/**
 * Why a job failed, what to change, and the lines of its output that tell it.
 * @param {object} job The job, with its diagnosis.
 * @returns {Element} What is shown under the row of the job.
 */
function diagnosisOf(job) {
  const d = job.diagnosis;
  const box = el("div", { class: "diag" }, el("p", { class: "diag-reason" }, d.reason), el("p", {}, d.hint));
  for (const detail of d.details) box.append(el("p", { class: "muted" }, detail));
  if (d.lines.length) {
    box.append(el("ul", { class: "diag-lines" }, ...d.lines.map(line => el("li", {},
      // Where the line is: the file opens there
      el("button", { type: "button", title: "Show the file at this line", data: { act: "view", name: job.name, file: line.file, line: line.line ?? "" } },
         line.line ? `${line.file}:${line.line}` : line.file),
      el("code", {}, line.text), line.count > 1 ? el("span", { class: "times" }, `×${line.count}`) : ""))));
  }
  const buttons = el("div", { class: "row" });
  if (job.again) buttons.append(action("Send again…", "again", job.name));
  if (d.fix === "settings") buttons.append(el("button", { class: "btn small", type: "button", data: { act: "settings", name: job.name } }, "Change the settings…"));
  for (const file of d.files) buttons.append(el("button", { class: "btn small", type: "button", data: { act: "view", name: job.name, file } }, `View ${file}`));
  buttons.append(el("button", { class: "btn small", type: "button", data: { act: "view", name: job.name, file: "job.sh" } }, "View the script"));
  box.append(buttons);
  if (!job.again) box.append(el("p", { class: "muted fine" }, "The interface that sent this job did not note its files, so it cannot be sent again from here: send the calculation as a new job from its tab."));
  return box;
}

/**
 * Show the jobs, and ask the cluster again when it is time.
 * @param {object} listing What the server says: jobs, poll_seconds, wait, paused, unread, directory and, after a question, problem.
 */
function showJobs(listing) {
  jobs.listing = listing;
  const table = $("#cj-table"), active = listing.jobs.filter(job => job.state === "waiting" || job.state === "running");
  table.textContent = "";
  $("#cj-empty").hidden = listing.jobs.length > 0;
  if (listing.jobs.length) {
    table.append(el("thead", {}, el("tr", {}, el("th", {}, "Job"), el("th", {}, "Calculation"), el("th", {}, "State"),
      el("th", {}, "Slurm"), el("th", {}, "Sent"), el("th", {}, "Asked for"), el("th", {}, "On the cluster"), el("th", {}, ""))));
    const body = el("tbody");
    for (const job of listing.jobs) {
      const [label, kind] = STATES[job.state] || [job.state, ""];
      const done = job.state !== "waiting" && job.state !== "running";
      const buttons = el("div", { class: "row tight" });
      if (!done) buttons.append(action("Stop", "stop", job.name));
      if (job.state === "finished" && job.fetched) buttons.append(action("Show results", "open", job.name));
      if (job.fetched) buttons.append(action(jobs.files.has(job.name) ? "Hide files" : "Files", "files", job.name));
      if (done && job.state !== "cancelled" && !job.fetched) buttons.append(action("Fetch results", "fetch", job.name));
      // A job that failed has the button with why it failed
      if (job.again && !job.diagnosis) buttons.append(action("Send again…", "again", job.name));
      if (done) buttons.append(action("Forget", "forget", job.name));
      body.append(el("tr", { data: { job: job.name } }, el("td", { class: "mono" }, job.name), el("td", {}, job.title),
        el("td", {}, el("span", { class: "pill" + (kind ? " " + kind : "") }, label),
          progressOf(job) ? el("div", { class: "muted fine jobprogress" }, progressOf(job)) : ""),
        el("td", { class: "mono" }, job.slurm + (job.slurm_state && !done ? ` ${job.slurm_state.toLowerCase()}` : "")),
        el("td", {}, when(job.created)), el("td", {}, asked(job)), el("td", { class: "mono" }, `${job.server}:${job.directory}`),
        el("td", {}, buttons)));
      if (job.diagnosis) body.append(el("tr", { class: "more" }, el("td", { colSpan: 8 }, diagnosisOf(job))));
      else if (job.error) body.append(el("tr", { class: "more" }, el("td", { colSpan: 8 }, el("pre", { class: "code" }, job.error))));
      if (jobs.files.has(job.name)) body.append(el("tr", { class: "more" }, el("td", { colSpan: 8 }, fileList(job.name, jobs.files.get(job.name)))));
    }
    table.append(body);
  }
  const checked = Math.max(0, ...listing.jobs.map(job => job.checked || 0));
  $("#cj-note").textContent = active.length
    ? `${active.length} not done · asked every ${listing.poll_seconds} s` + (checked ? ` · last at ${when(checked)}` : "")
    : "";
  $("#cj-ask").disabled = !active.length || jobs.busy;
  const problem = listing.paused
    ? listing.paused + " The cluster is not asked again until you press “Ask the cluster now”."
    : (listing.problem || "");
  $("#cj-problem").hidden = !problem;
  $("#cj-problem").textContent = problem;
  $("#cj-kept").textContent = (listing.jobs.length ? `What is known about the jobs, and their results, are kept in ${listing.directory}. ` : "")
    + (listing.unread.length ? `Not read, because the file job.json there is not as the interface wrote it: ${listing.unread.join(", ")}.` : "");
  $("#cj-foot").hidden = !problem && !$("#cj-kept").textContent;
  // The job below the table may have been forgotten
  if (panel.name && !listing.jobs.some(job => job.name === panel.name)) closePanel();
  // The next question, when there is something to ask and no failed login in the way
  clearTimeout(jobs.timer);
  if (active.length && !listing.paused) jobs.timer = setTimeout(() => ask(false), (Math.max(listing.wait, 5) + 1) * 1000);
}

/**
 * Ask the cluster how the jobs are doing. The server does not log in more
 * often than the settings say, whatever is asked here.
 * @param {boolean} manual Whether it is the user who asks.
 */
async function ask(manual) {
  if (jobs.busy) return;
  jobs.busy = true;
  $("#cj-ask").disabled = true;
  if (manual) pill("cl-pill", "Asking the cluster…");
  try {
    const before = jobs.listing ? jobs.listing.jobs : [];
    const listing = await (await call("/cluster/jobs/check", { method: "POST", params: manual ? { manual: "true" } : {} })).json();
    jobs.busy = false;
    showJobs(listing);
    if (manual) pill("cl-pill", "");
    if (manual && !listing.asked && listing.wait > 0) toast(`The cluster was asked a moment ago. Try again in ${Math.ceil(listing.wait)} s.`);
    for (const job of listing.jobs) {
      const was = before.find(old => old.name === job.name);
      if (was && was.state !== job.state && job.state !== "waiting" && job.state !== "running") toast(`Job ${job.name}: ${(STATES[job.state] || [job.state])[0].toLowerCase()}`);
    }
  } catch (e) {
    jobs.busy = false;
    if (manual) { pill("cl-pill", ""); alertBox("The cluster could not be asked. " + e.message); }
    if (jobs.listing) showJobs(jobs.listing);
  }
}

/**
 * Do what a button in the row of a job says.
 * @param {Element} button The button.
 */
async function jobAction(button) {
  const { act, name } = button.dataset;
  if (act === "forget" && button.dataset.sure !== "yes") {
    // Asked twice: the results on this computer go with the job
    button.dataset.sure = "yes";
    button.textContent = "Forget, with its results here?";
    setTimeout(() => { if (button.isConnected) { button.dataset.sure = ""; button.textContent = "Forget"; } }, 5000);
    return;
  }
  if (act === "view") { viewFile(name, button.dataset.file, Number(button.dataset.line) || 0); return; }
  if (act === "again") { showAgain(name); return; }
  if (act === "settings") { $('#nav button[data-view="settings"]').click(); return; }
  button.disabled = true;
  alertBox("");
  try {
    if (act === "files") {
      // The output files that came back, shown or hidden
      if (jobs.files.has(name)) jobs.files.delete(name);
      else jobs.files.set(name, (await (await call(`/cluster/jobs/${encodeURIComponent(name)}/files`)).json()).files);
      showJobs(jobs.listing);
      return;
    }
    if (act === "open") {
      const opened = await (await call(`/cluster/jobs/${encodeURIComponent(name)}/open`, { method: "POST" })).json();
      const job = await (await call("/jobs/" + opened.job)).json();
      document.dispatchEvent(new CustomEvent("act-show-result", { detail: { job, name } }));
      button.disabled = false;
      return;
    }
    const path = `/cluster/jobs/${encodeURIComponent(name)}`;
    const answer = act === "fetch" ? await call(path + "/fetch", { method: "POST" })
      : await call(path, { method: "DELETE", params: act === "forget" ? { forget: "true" } : {} });
    showJobs(await answer.json());
    toast({ stop: `Stopped ${name}`, forget: `Forgot ${name}`, fetch: `Fetched the results of ${name}` }[act]);
  } catch (e) {
    button.disabled = false;
    alertBox({ stop: "The job could not be stopped. ", forget: "The job could not be forgotten. ",
               fetch: "The results could not be fetched. ", open: "The results could not be shown. ",
               files: "The files of the job could not be listed. " }[act] + e.message);
  }
}

/* ---------- one job below the table ---------- */

/**
 * Show the panel of a job.
 * @param {string} name The name of the job.
 * @param {string} mode view, for its files, or again, to send it again.
 * @param {string} title The title of the panel.
 */
function openPanel(name, mode, title) {
  panel.name = name;
  panel.mode = mode;
  panel.pending = null;
  $("#cj-paneltitle").textContent = title;
  $("#cj-viewer").hidden = mode !== "view";
  $("#cj-again").hidden = mode !== "again";
  $("#cj-panel").hidden = false;
}

/** Close the panel of a job. */
function closePanel() {
  panel.name = panel.mode = "";
  panel.pending = null;
  $("#cj-panel").hidden = true;
  $("#cj-view").textContent = "";
}

/**
 * Show a file of a job as text, with the lines that tell why it failed
 * marked, and scroll to one of them.
 * @param {string} name The name of the job.
 * @param {string} file The name of the file.
 * @param {number} line The number of the line to show, 0 for the first that is marked.
 */
async function viewFile(name, file, line = 0) {
  const job = (jobs.listing ? jobs.listing.jobs : []).find(j => j.name === name);
  let list = jobs.files.get(name);
  try {
    if (!list) list = (await (await call(`/cluster/jobs/${encodeURIComponent(name)}/files`)).json()).files;
    const told = await (await call(`/cluster/jobs/${encodeURIComponent(name)}/text/${encodeURIComponent(file)}`)).json();
    openPanel(name, "view", `${name} · ${file}`);
    // The other files of the job that can be read here
    const files = $("#cj-viewfiles");
    files.textContent = "";
    for (const other of list.filter(f => TEXT_FILE.test(f.name))) {
      files.append(el("button", { type: "button", "aria-pressed": String(other.name === file), data: { v: other.name } }, other.name));
    }
    const marked = new Set((job && job.diagnosis ? job.diagnosis.lines : []).filter(l => l.file === file && l.line).map(l => l.line));
    const view = $("#cj-view");
    view.textContent = "";
    const lines = told.text.split("\n");
    if (lines.length && lines[lines.length - 1] === "") lines.pop();
    let plain = [];
    const flush = () => { if (plain.length) { view.append(plain.join("\n") + "\n"); plain = []; } };
    lines.forEach((text, i) => {
      const number = told.first + i;
      if (!marked.has(number) && number !== line) { plain.push(text); return; }
      flush();
      view.append(el("mark", { class: number === line ? "here" : "", data: { line: number } }, text), "\n");
    });
    flush();
    const last = told.first + lines.length - 1;
    $("#cj-viewnote").textContent = `${size(told.size)} · ` + (told.cut ? `the last part, lines ${told.first}–${last}` : `${lines.length} lines`)
      + (marked.size ? " · the lines that tell why the job failed are marked" : "");
    const target = view.querySelector(`mark[data-line="${line}"]`) || view.querySelector("mark");
    if (target) view.scrollTop = target.offsetTop - view.offsetTop - 60;
    else view.scrollTop = file === "job.sh" ? 0 : view.scrollHeight;
    $("#cj-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (e) {
    alertBox(`The file ${file} could not be shown. ` + e.message);
  }
}

/**
 * Show the form to send a job again, filled in with what the job asked
 * for, or with what its diagnosis proposes.
 * @param {string} name The name of the job.
 */
function showAgain(name) {
  const job = jobs.listing.jobs.find(j => j.name === name);
  if (!job) return;
  const suggest = (job.diagnosis && job.diagnosis.suggest) || {};
  openPanel(name, "again", `Send ${name} again`);
  $("#cj-again-name").value = "";
  $("#cj-again-name").placeholder = `${name}-r2 or the next free one`;
  $("#cj-again-tasks").value = String(job.tasks);
  $("#cj-again-tasksfield").hidden = job.task === "openmm_run";
  $("#cj-again-hours").value = String(Math.round(((suggest.minutes || job.minutes) / 60) * 100) / 100);
  $("#cj-again-gpu").checked = suggest.gpu ?? job.gpu;
  $("#cj-again-job").hidden = true;
  const changed = [suggest.minutes ? "more time" : "", suggest.gpu === false ? "no GPU" : ""].filter(Boolean);
  $("#cj-againnote").textContent = `The new job gets the files of ${name}, which are copied on the cluster, and the settings of the cluster as they are now. `
    + (changed.length ? `It asks for ${changed.join(" and ")}, as proposed above; change what you like.`
      : job.diagnosis && job.diagnosis.fix === "processes" ? "Give it fewer processes, as proposed above." : "Change what it asks for.");
  $("#cj-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
  (job.diagnosis && job.diagnosis.fix === "processes" ? $("#cj-again-tasks") : $("#cj-again-hours")).focus();
}

/**
 * What the form says the new job asks for.
 * @returns {object} The choices, as the server takes them.
 */
function againChoices() {
  const chosen = { minutes: Math.round(Number($("#cj-again-hours").value) * 60), gpu: $("#cj-again-gpu").checked };
  if (!$("#cj-again-tasksfield").hidden) chosen.tasks = Number($("#cj-again-tasks").value);
  if ($("#cj-again-name").value.trim()) chosen.name = $("#cj-again-name").value.trim();
  return chosen;
}

/** Show the job that would be sent again: nobody logs in for it. */
async function showAgainJob() {
  const button = $("#cj-again-show"), name = panel.name;
  button.disabled = true;
  alertBox("");
  try {
    const cluster = againChoices();
    const shown = await (await call(`/cluster/jobs/${encodeURIComponent(name)}/again`, { method: "POST", json: { cluster, send: false } })).json();
    panel.pending = { cluster: { ...cluster, name: shown.name }, send: true };
    $("#cj-again-name").value = shown.name;
    $("#cj-again-where").textContent = `${shown.name} · ${shown.server}:${shown.directory}`;
    const table = $("#cj-again-files");
    table.textContent = "";
    table.append(el("thead", {}, el("tr", {}, el("th", {}, "Name on the cluster"), el("th", {}, "From"))),
      el("tbody", {}, ...shown.files.map(f => el("tr", {}, el("td", { class: "mono" }, f.name),
        el("td", {}, f.from ? `copied from ${f.from}` : `sent, ${size(f.size)}`)))));
    $("#cj-again-script").textContent = shown.script;
    $("#cj-again-taken").hidden = !shown.taken;
    $("#cj-again-send").disabled = shown.taken;
    pill("cj-againpill", "");
    $("#cj-again-job").hidden = false;
    $("#cj-again-job").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (e) {
    alertBox("The job cannot be sent again. " + e.message);
  } finally { button.disabled = false; }
}

/** Send the job that is shown again: one login. */
async function sendAgain() {
  if (!panel.pending) return;
  const button = $("#cj-again-send"), name = panel.name;
  button.disabled = true;
  alertBox("");
  pill("cj-againpill", "Logging in and sending…");
  try {
    const sent = await (await call(`/cluster/jobs/${encodeURIComponent(name)}/again`, { method: "POST", json: panel.pending })).json();
    closePanel();
    showJobs(sent);
    toast(`Sent ${sent.name} to the cluster, job ${sent.slurm} of Slurm`);
  } catch (e) {
    pill("cj-againpill", "Not sent", "bad");
    alertBox("The job was not sent. " + e.message);
    button.disabled = false;
  }
}

/** Fetch the jobs from the server of the interface, without asking the cluster. */
async function listJobs() {
  try {
    showJobs(await (await call("/cluster/jobs")).json());
  } catch (e) {
    alertBox("The jobs on the cluster could not be listed. " + e.message);
  }
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
  loginPill(message ? (ok ? "Ready" : "Not ready") : "", ok ? "good" : "bad");
}

/**
 * Tell how the login goes, next to the button in the tab Settings and in the tab Cluster.
 * @param {string} text The text, empty to show nothing.
 * @param {string} kind good, bad or nothing.
 */
function loginPill(text, kind = "") {
  pill("cl-pill", text, kind);
  pill("cl-checkpill", text, kind);
}

/** Log in once with the settings that are kept, to see whether jobs can be sent. */
async function check() {
  const button = $("#cl-check");
  button.disabled = true;
  alertBox("");
  result("");
  loginPill("Logging in…");
  try {
    const answer = await (await call("/cluster/check", { method: "POST" })).json();
    result(answer.message, answer.ok);
  } catch (e) {
    loginPill("");
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
  $("#set-cluster").hidden = !told.cluster;
  if (!told.cluster) return;
  $("#cl-tosettings").onclick = () => $('#nav button[data-view="settings"]').click();
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
  $("#cj-ask").onclick = () => ask(true);
  $("#cj-panelclose").onclick = closePanel;
  $("#cj-viewfiles").addEventListener("click", e => {
    const b = e.target.closest("button");
    if (b && panel.name) viewFile(panel.name, b.dataset.v);
  });
  $("#cj-again-show").onclick = showAgainJob;
  $("#cj-again-send").onclick = sendAgain;
  // What is shown is what is sent: a change has to be shown again
  for (const id of ["cj-again-name", "cj-again-tasks", "cj-again-hours", "cj-again-gpu"]) {
    $("#" + id).addEventListener("input", () => { $("#cj-again-job").hidden = true; panel.pending = null; });
  }
  $("#cj-table").addEventListener("click", e => {
    const save = e.target.closest("button[data-save]");
    if (save) {
      const { name } = save.dataset;
      saveFile(save, `/cluster/jobs/${encodeURIComponent(name)}/files/${encodeURIComponent(save.dataset.save)}`, {}, "Saving…", save.dataset.save);
      return;
    }
    const button = e.target.closest("button[data-act]");
    if (button) jobAction(button);
  });
  // A job that was sent from another tab
  document.addEventListener("act-cluster-jobs", e => {
    showJobs(e.detail.listing);
    if (e.detail.show) tab.click();
  });
  try {
    show(await (await call("/cluster")).json());
  } catch (e) {
    alertBox("The settings of the cluster could not be fetched. " + e.message);
  }
  await listJobs();
}
