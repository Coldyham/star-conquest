// The account panel on the player card (user.html's #account): sign in with a
// private login and password, then claim, show or release one name, change the
// password, sign out. Every rule is the database's (schema.sql's
// claim_name/release_name/my_name); this panel only asks and reports.

import { configured, rpc } from "./api.mjs";
import { changePassword, session, signIn, signOut, signUp } from "./auth.mjs";
import { claimError } from "./claims.mjs";
import { rememberName } from "./me.mjs";

/**
 * Wire up the panel's static markup. `onName(name)` runs when an action leaves
 * the visitor owning a name (signing in to an account that has one, or a
 * claim), so the page can show that name's card; never on page load.
 */
export function mountAccount({ onName = () => {} } = {}) {
  const $ = (id) => document.getElementById(`acct-${id}`);
  const who = $("who");
  const credentialsForm = $("credentials");
  const loginField = $("login");
  const passwordField = $("password");
  const signUpButton = $("sign-up");
  const signedInRow = $("signed-in");
  const changePasswordButton = $("change-password");
  const signOutButton = $("sign-out");
  const claimForm = $("claim");
  const nameField = $("name");
  const releaseButton = $("release");
  const passwordForm = $("new-password");
  const newPasswordField = $("password-new");
  const status = $("status");

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

  /** Redraw for the current session; resolves to the owned name, or "". */
  async function refresh() {
    for (const part of [credentialsForm, signedInRow, claimForm, releaseButton]) part.hidden = true;
    showPasswordForm(false);
    const live = await session();
    if (!live) {
      who.textContent = "Not signed in.";
      credentialsForm.hidden = false;
      return "";
    }
    signedInRow.hidden = false;
    const as = live.login ? `Signed in as ${live.login}` : "Signed in";
    let owned = "";
    try {
      owned = (await rpc("my_name", {})) || "";
    } catch (err) {
      who.textContent = `${as}.`;
      say(`Couldn't read your name: ${err.message}`, "error");
      return "";
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
    return owned;
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
    const owned = await refresh();
    if (owned) onName(owned);
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
      await refresh();
      return;
    }
    const owned = await refresh();
    if (owned) onName(owned);
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

  refresh();
}
