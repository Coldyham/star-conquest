// account.html: the account panel lives on the player card now (account.mjs on
// user.html). Forward there, to this browser's own card when it has a name.

import { userHref } from "./format.mjs";
import { myName } from "./me.mjs";

const name = myName();
location.replace(name ? userHref([name]) : "user.html");
