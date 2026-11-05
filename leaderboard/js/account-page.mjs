// account.html: sign in with a private login and password, then claim, show or
// release one name. Every rule is the database's (schema.sql's
// claim_name/release_name/my_name); this page only asks and reports.

import { configured, rpc } from "./api.mjs";
import { changePassword, session, signIn, signOut, signUp } from "./auth.mjs";
import { claimError } from "./claims.mjs";
import { mountMyScores, rememberName } from "./me.mjs";
import { mountNav } from "./nav.mjs";

const who = document.getElementById("who");
const credentialsForm = document.getElementById("credentials");
const loginField = document.getElementById("login");
const passwordField = document.getElementById("password");
const signUpButton = document.getElementById("sign-up");
const signedInRow = document.getElementById("signed-in");
const changePasswordButton = document.getElementById("change-password");
const signOutButton = document.getElementById("sign-out");
const claimForm = document.getElementById("claim");
const nameField = document.getElementById("name");
const releaseButton = document.getElementById("release");
const passwordForm = document.getElementById("new-password");
const newPasswordField = document.getElementById("password-new");
const status = document.getElementById("status");

function say(message, kind = "") {
  status.textContent = message;
  status.className = `status ${kind}`;
}

/** Show or fold away the new-password field under its row button. */
function showPasswordForm(open) {
  passwordForm.hidden = !open;
  changePasswordButton.setAttribute("aria-expanded", String(open));
  if (open) newPasswordField.focus();
}

async function refresh() {
  for (const part of [credentialsForm, signedInRow, claimForm, releaseButton]) part.hidden = true;
  showPasswordForm(false);
  const live = await session();
  if (!live) {
    who.textContent = "Not signed in.";
    credentialsForm.hidden = false;
    return;
  }
  signedInRow.hidden = false;
  const as = live.login ? `Signed in as ${live.login}` : "Signed in";
  let owned = "";
  try {
    owned = (await rpc("my_name", {})) || "";
  } catch (err) {
    who.textContent = `${as}.`;
    say(`Couldn't read your name: ${err.message}`, "error");
    return;
  }
  if (owned) {
    who.textContent = `${as} — your name is “${owned}”.`;
    rememberName(owned);
    releaseButton.hidden = false;
  } else {
    who.textContent = `${as}. You haven't claimed a name yet.`;
    // The login is private and may be an email, so it is never offered as the
    // public name.
    claimForm.hidden = false;
  }
}

async function enter(how) {
  if (!configured()) {
    say("This leaderboard isn't connected to its database yet.", "error");
    return;
  }
  say(how === signUp ? "Creating your account…" : "Signing in…");
  const { error } = await how(loginField.value, passwordField.value);
  if (error) {
    say(error, "error");
    return;
  }
  passwordField.value = "";
  say(how === signUp ? "Account made. Now claim a name." : "");
  await refresh();
}

credentialsForm.addEventListener("submit", (event) => {
  event.preventDefault();
  enter(signIn);
});

signUpButton.addEventListener("click", () => enter(signUp));

changePasswordButton.addEventListener("click", () => showPasswordForm(passwordForm.hidden));

signOutButton.addEventListener("click", async () => {
  signOut();
  say("");
  await refresh();
});

claimForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = nameField.value.trim();
  if (!name) {
    say("Type a name to claim.", "error");
    return;
  }
  say("Claiming…");
  try {
    await rpc("claim_name", { p_name: name });
    rememberName(name);
    say(`“${name}” is yours.`);
  } catch (err) {
    say(claimError(err.message), "error");
  }
  await refresh();
});

releaseButton.addEventListener("click", async () => {
  if (!confirm("Release your name? Its scores stay on the board, but anyone could then post under it.")) return;
  try {
    await rpc("release_name", {});
    say("Released.");
  } catch (err) {
    say(err.message, "error");
  }
  await refresh();
});

passwordForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const problem = await changePassword(newPasswordField.value);
  if (problem) {
    say(problem, "error");
    return;
  }
  newPasswordField.value = "";
  showPasswordForm(false);
  say("Password changed.");
});

mountNav();
mountMyScores();
refresh();
