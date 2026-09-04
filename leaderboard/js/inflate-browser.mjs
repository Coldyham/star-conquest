// The browser's half of token-decode.mjs: zlib-wrapped DEFLATE -> raw bytes.
//
// 'deflate' is the zlib-wrapped variant in the Compression Streams spec, which is
// what Python's zlib.compress emits (2-byte header + Adler-32 trailer);
// 'deflate-raw' would be the wrong one. pako's inflate (not inflateRaw) expects
// the same wrapper, and is only fetched where the native API is missing.

const PAKO = "https://esm.sh/pako@2.1.0";

export async function inflate(bytes) {
  if (typeof DecompressionStream !== "undefined") {
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate"));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }
  const pako = await import(/* @vite-ignore */ PAKO);
  return (pako.inflate ?? pako.default.inflate)(bytes);
}
