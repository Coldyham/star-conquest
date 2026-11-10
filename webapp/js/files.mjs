// Files the player keeps (`starconquest.files`): the session's `download`
// effect hands the browser a file to save, and the file picker reads one back
// for `file.open`. The picker opens on the press itself, since a browser only
// lets a page open it from inside a user's gesture.

/** Save `text` as file `name` (the `download` effect). */
export function download({ name, text, type }) {
  const url = URL.createObjectURL(new Blob([text], { type: type || "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.style.display = "none";
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

/** Ask the player for a file, and hand `opened(name, text)` what it holds.
 * Nothing happens if they cancel. */
export function pickFile(opened) {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = ".json,application/json";
  input.style.display = "none";
  input.addEventListener("change", () => {
    const file = input.files && input.files[0];
    input.remove();
    if (file) file.text().then((text) => opened(file.name, text), (err) => console.warn("couldn't read", err));
  });
  document.body.append(input);
  input.click();
}
