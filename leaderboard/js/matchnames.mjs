// Readable names for play-by-post matches, derived from the match id — a
// label, never a key. Mirrors starconquest/matchnames.py word for word;
// tests/test_leaderboard_sync.py pins the two together.

export const ADJECTIVES = [
  "amber", "ancient", "arctic", "ashen", "azure", "bold", "brave", "bright", "brisk",
  "bronze", "calm", "candid", "cobalt", "cold", "copper", "cosmic", "crimson", "crisp",
  "curious", "dapper", "daring", "deep", "distant", "dusky", "dusty", "eager", "early",
  "eastern", "ebony", "electric", "elder", "emerald", "endless", "fading", "fair",
  "faint", "far", "fast", "fearless", "fierce", "fiery", "final", "first", "fleet",
  "floral", "flying", "fond", "frosty", "gallant", "gentle", "giant", "gilded", "glad",
  "gleaming", "glowing", "golden", "grand", "granite", "grassy", "gray", "great",
  "green", "hidden", "hollow", "honest", "humble", "hushed", "icy", "idle", "inner",
  "iron", "ivory", "jade", "jolly", "keen", "kind", "last", "late", "lazy", "lemon",
  "level", "little", "lively", "lone", "lost", "loud", "lucky", "lunar", "lush",
  "magic", "marble", "merry", "mighty", "mild", "misty", "modest", "molten", "mossy",
  "muted", "narrow", "neat", "nimble", "noble", "northern", "olive", "onyx", "open",
  "orange", "outer", "pale", "patient", "pearl", "plain", "plum", "polar", "polite",
  "proud", "purple", "quick", "quiet", "radiant", "rapid", "rare", "ready", "red",
  "regal", "restless", "rising", "roaming", "robust", "rocky", "rosy", "round", "royal",
  "ruby", "rustic", "rusty", "sable", "sacred", "sandy", "scarlet", "secret", "serene",
  "shady", "sharp", "shining", "shy", "silent", "silken", "silver", "simple", "sleek",
  "sleepy", "slow", "small", "smoky", "snowy", "soft", "solar", "solemn", "solid",
  "sonic", "southern", "spare", "spry", "stable", "starry", "steady", "steep",
  "stellar", "still", "stoic", "stormy", "stout", "strong", "sturdy", "subtle",
  "sudden", "sunny", "swift", "tall", "tame", "tawny", "teal", "tender", "thin",
  "tidal", "timid", "tiny", "topaz", "tranquil", "true", "twin", "unseen", "upper",
  "valiant", "velvet", "verdant", "vast", "violet", "vivid", "wandering", "warm",
  "wary", "western", "whole", "wild", "windy", "wise", "witty", "wooden", "woven",
  "young", "zealous", "zesty", "agile", "airy", "balmy", "blazing", "blessed", "breezy",
  "bubbly", "busy", "cheerful", "clever", "cloudy", "cozy", "dreamy", "drifting", "dry",
  "dual", "elegant", "faithful", "festive", "fluent", "free", "fresh", "friendly",
  "frozen", "gusty", "handy", "hardy", "hearty", "jovial", "joyful", "lofty", "loyal",
  "lucid", "mellow", "nomadic", "peaceful", "playful", "precise", "pure", "quaint",
  "rugged",
];

export const NOUNS = [
  "anchor", "anvil", "arch", "arrow", "atlas", "aurora", "badger", "banner", "barge",
  "basin", "beacon", "bear", "bell", "birch", "bison", "blade", "bloom", "boulder",
  "bridge", "brook", "cairn", "canyon", "canvas", "cape", "cargo", "castle", "cedar",
  "chapel", "cinder", "cipher", "citadel", "cliff", "cloud", "clover", "comet",
  "compass", "condor", "cosmos", "cove", "crane", "crater", "creek", "crest", "crow",
  "crown", "crystal", "current", "cypress", "delta", "desert", "dune", "eagle", "echo",
  "eclipse", "ember", "engine", "falcon", "feather", "fern", "ferry", "field", "finch",
  "fjord", "flame", "flare", "forest", "forge", "fountain", "fox", "galaxy", "garden",
  "garnet", "gate", "geyser", "glacier", "glade", "glen", "globe", "grove", "gull",
  "harbor", "harp", "harvest", "haven", "hawk", "heron", "hill", "hive", "horizon",
  "island", "ivy", "jasper", "jetty", "journey", "jungle", "kestrel", "kettle", "kite",
  "lagoon", "lake", "lantern", "larch", "lark", "lattice", "ledge", "lens", "lily",
  "lion", "lotus", "lynx", "magnet", "mantle", "maple", "marsh", "mast", "meadow",
  "mesa", "meteor", "mill", "mirror", "moon", "moth", "mountain", "nebula", "nest",
  "needle", "nomad", "oak", "oasis", "ocean", "orbit", "orchard", "osprey", "otter",
  "owl", "paddle", "palace", "panther", "parrot", "path", "peak", "pebble", "pelican",
  "pier", "pilot", "pine", "planet", "plateau", "plume", "pond", "poplar", "portal",
  "prairie", "prism", "pulsar", "quarry", "quasar", "quill", "rain", "rapids", "raven",
  "reef", "ridge", "river", "robin", "rocket", "root", "rover", "saddle", "sail",
  "salmon", "sapphire", "satellite", "scroll", "sea", "shadow", "shell", "shield",
  "shore", "signal", "sky", "slate", "sparrow", "spire", "spring", "spruce", "star",
  "stone", "storm", "stream", "summit", "sun", "swallow", "swan", "temple", "thicket",
  "thistle", "thunder", "tide", "tiger", "timber", "torch", "tower", "trail", "tundra",
  "tunnel", "valley", "vapor", "vault", "vessel", "village", "vine", "voyage", "walrus",
  "wave", "willow", "wind", "wing", "winter", "wolf", "wren", "yarrow", "zenith",
  "zephyr", "acorn", "albatross", "alder", "anthem", "apple", "aspen", "bamboo",
  "beetle", "berry", "bramble", "breeze", "buffalo", "candle", "caravan", "cavern",
  "cherry", "chime", "cobra", "cricket", "dolphin", "dragon", "drum", "falls", "fig",
  "flint", "galleon", "glider", "gorge", "hammer", "hare", "heath", "hearth",
];

const MATCH_ID = /^[0-9a-f]{16}$/;

/** "amber-comet-harbor" for a match id, or "" for anything that isn't one. */
export function phrase(matchId) {
  if (typeof matchId !== "string" || !MATCH_ID.test(matchId)) return "";
  const [a, b] = [0, 2].map((i) => parseInt(matchId.slice(i, i + 2), 16));
  let c = parseInt(matchId.slice(4, 6), 16);
  if (c === b) c = (c + 1) % NOUNS.length;
  return `${ADJECTIVES[a]}-${NOUNS[b]}-${NOUNS[c]}`;
}
