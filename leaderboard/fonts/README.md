# Fonts

`press-start-2p.woff2` — [Press Start 2P](https://fonts.google.com/specimen/Press+Start+2P)
v16, the arcade-cabinet pixel face used for the cabinet chrome (headings, ranks,
scores). Licensed under the SIL Open Font License 1.1; see `OFL.txt`.

Self-hosted rather than hotlinked from Google Fonts, so the deploy stays
self-contained and needs no third-party request — the same reason
`tools/build_web.sh` mirrors the pygame WASM wheel into the game's build.

To re-fetch:

```sh
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
URL=$(curl -s -A "$UA" "https://fonts.googleapis.com/css2?family=Press+Start+2P" \
      | grep -oP 'https://[^)]+\.woff2' | head -1)
curl -o press-start-2p.woff2 "$URL"
```

Body prose deliberately stays on a system monospace stack — the pixel face is
unreadable at paragraph length.
