# Fonts

`press-start-2p.woff2` — [Press Start 2P](https://fonts.google.com/specimen/Press+Start+2P)
v16, the arcade-cabinet pixel face used for the cabinet chrome (headings, ranks,
scores) and the campaign map's numbers (also served to the game at
`/play/fonts/`, `tools/build_next.FONTS`). Licensed under the SIL Open Font
License 1.1; see `OFL.txt`.

It is Google's **latin** subset (ASCII and Latin-1, 220 glyphs). Google's CSS
lists one `@font-face` per subset, latin last; until 2026-11-24 this file was
the first one listed, cyrillic-ext, which has no digits or Latin letters, so
every pixel heading quietly fell back to Courier New.

Self-hosted rather than hotlinked from Google Fonts, so the deploy stays
self-contained and needs no third-party request — the same reason
`tools/build_web.sh` mirrors the pygame WASM wheel into the game's build.

To re-fetch:

```sh
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
# The latin block only: the first url listed is cyrillic-ext.
URL=$(curl -s -A "$UA" "https://fonts.googleapis.com/css2?family=Press+Start+2P" \
      | sed -n '/\/\* latin \*\//,/}/p' | grep -oP 'https://[^)]+\.woff2')
curl -o press-start-2p.woff2 "$URL"
```

Body prose deliberately stays on a system monospace stack — the pixel face is
unreadable at paragraph length.
