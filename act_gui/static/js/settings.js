/*
 * This source file is part of the Alexandria Chemistry Toolkit.
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; either version 2
 * of the License, or (at your option) any later version.
 */
/**
 * @file The screen mode of the page: light, dark or hippy. The button in the
 * top bar goes to the next mode, the tab Settings offers all three. On the
 * desktop the choice is kept in the file with the settings of the user, so
 * that the page starts in it the next time; a server for several users
 * keeps it in the browser instead.
 */
import { $, $$, alertBox, call } from "./common.js";

/** The modes, in the order in which the button goes through them, with their names. */
const THEMES = { light: "Light", dark: "Dark", hippy: "Hippy" };
/** Where the browser keeps the mode, for a server that keeps none. */
const STORAGE = "act-theme";

/** Whether the server keeps the choice, from the session. */
let serverKeeps = false;

/**
 * The mode that the page shows.
 * @returns {string} One of the keys of THEMES.
 */
export const currentTheme = () => (document.documentElement.dataset.theme in THEMES ? document.documentElement.dataset.theme : "light");

/**
 * The mode after a given one, for the button.
 * @param {string} theme The mode now.
 * @returns {string} The next mode, after the last the first.
 */
export function nextTheme(theme) {
  const order = Object.keys(THEMES);
  return order[(order.indexOf(theme) + 1) % order.length];
}

/**
 * Show the page in a mode, without keeping the choice. The plots and the 3D
 * views, which take their colours when they are drawn, are drawn again.
 * @param {string} theme One of the keys of THEMES.
 */
export function applyTheme(theme) {
  if (!(theme in THEMES)) theme = "light";
  document.documentElement.dataset.theme = theme;
  const button = $("#theme"), next = nextTheme(theme);
  button.textContent = THEMES[theme];
  button.title = `Screen mode: ${THEMES[theme].toLowerCase()}. Click for ${THEMES[next].toLowerCase()}.`;
  button.setAttribute("aria-label", button.title);
  $$("#set-theme button").forEach(b => b.setAttribute("aria-pressed", b.dataset.v === theme));
  document.dispatchEvent(new CustomEvent("act-theme"));
}

/**
 * Show the page in a mode and keep the choice for the next time.
 * @param {string} theme One of the keys of THEMES.
 */
async function chooseTheme(theme) {
  applyTheme(theme);
  if (!serverKeeps) {
    try { localStorage.setItem(STORAGE, theme); } catch (e) { /* no storage: the choice lasts as long as the page */ }
    return;
  }
  try {
    await call("/preferences", { method: "PUT", json: { theme } });
  } catch (e) {
    alertBox("The screen mode was not kept for the next time. " + e.message);
  }
}

/**
 * Make the button and the tab Settings work. Called when the server has told who we are.
 * @param {object} told What the server said: the preferences and whether it keeps them.
 */
export function startSettings(told) {
  serverKeeps = Boolean(told.keeps_preferences);
  let theme = (told.preferences || {}).theme;
  if (!serverKeeps) {
    try { theme = localStorage.getItem(STORAGE) || theme; } catch (e) { /* no storage */ }
  }
  applyTheme(theme || currentTheme());
  $("#theme").onclick = () => chooseTheme(nextTheme(currentTheme()));
  $("#set-theme").addEventListener("click", e => {
    const b = e.target.closest("button[data-v]");
    if (b) chooseTheme(b.dataset.v);
  });
  $("#set-themenote").textContent = serverKeeps
    ? "The mode is kept with your other settings, and the page starts in it the next time."
    : "The mode is kept in this browser.";
}
