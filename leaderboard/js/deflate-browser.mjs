// The browser's half of token-encode.mjs: raw bytes -> zlib-wrapped DEFLATE.
//
// Mirrors inflate-browser.mjs, including which variant: 'deflate' is the
// zlib-wrapped one in the Compression Streams spec, and so what Python's
// zlib.decompress expects — 'deflate-raw' would be the wrong one.
//
// A browser without CompressionStream gets null here rather than the pako
// fallback the reading side keeps, because writing degrades and reading doesn't:
// encodeToken then emits the same token uncompressed, which the game reads
// anyway. The cost is a longer link, not a fetch on the critical path.

export const deflate = typeof CompressionStream === "undefined" ? null : async (bytes) => {
  const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream("deflate"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
};
