// account.html: sign in with Google, then claim, show or release one name.
// Every rule is the database's (schema.sql's claim_name/release_name/my_name);
// this page only asks and reports.

import { configured, rpc } from "./api.mjs";
import { finishSignIn, session, signIn, signOut } from "./auth.mjs";
import { claimError } from "./claims.mjs";
import { mountMyScores, rememberName } from "./me.mjs";
import { mountNav } from "./nav.mjs";

const who = document.getElementById("who");
const signInButton = document.getElementById("sign-in");
const signOutButton = document.getElementById("sign-out");
const claimForm = document.getElementById("claim");
const nameField = document.getElementById("name");
const releaseButton = document.getElementById("release");
const status = document.getElementById("status");

function say(message, kind = "") {
  status.textContent = message;
  status.className = `status ${kind}`;
}

async function refresh() {
  signInButton.hidden = signOutButton.hidden = claimForm.hidden = releaseButton.hidden = true;
  const live = await session();
  if (!live) {
    who.textContent = "Not signed in.";
    signInButton.hidden = false;
    return;
  }
  signOutButton.hidden = false;
  let owned = "";
  try {
    owned = (await rpc("my_name", {})) || "";
  } catch (err) {
    who.textContent = `Signed in as ${live.email || "a Google account"}.`;
    say(`Couldn't read your name: ${err.message}`, "error");
    return;
  }
  if (owned) {
    who.textContent = `Signed in as ${live.email || "a Google account"} — your name is “${owned}”.`;
    rememberName(owned);
    releaseButton.hidden = false;
  } else {
    who.textContent = `Signed in as ${live.email || "a Google account"}. You haven't claimed a name yet.`;
    claimForm.hidden = false;
  }
}

signInButton.addEventListener("click", () => {
  if (!configured()) {
    say("This leaderboard isn't connected to its database yet.", "error");
    return;
  }
  signIn().catch((err) => say(`Couldn't start signing in: ${err.message}`, "error"));
});

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

mountNav();
mountMyScores();
(async () => {
  // Back from Google: trade the code for a session. A refusal arrives as
  // ?error_description= instead (the person cancelled, or the provider is off).
  const refused = new URL(location.href).searchParams.get("error_description");
  if (refused) say(refused, "error");
  else await finishSignIn();
  await refresh();
})();
