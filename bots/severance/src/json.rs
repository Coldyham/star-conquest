//! A minimal JSON reader/writer.
//!
//! The protocol is one JSON object per line, keys not positions, and a 24-node
//! payload is a few kilobytes -- so this is a plain recursive-descent parser
//! over the line's bytes rather than a dependency.

use std::fmt::Write as _;

#[derive(Debug, Clone)]
pub enum J {
    Null,
    Bool(bool),
    Num(f64),
    Str(String),
    Arr(Vec<J>),
    Obj(Vec<(String, J)>),
}

const EMPTY: [J; 0] = [];

impl J {
    pub fn get(&self, key: &str) -> Option<&J> {
        match self {
            J::Obj(fields) => fields.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }

    pub fn num(&self) -> Option<f64> {
        match self {
            J::Num(n) => Some(*n),
            J::Bool(b) => Some(if *b { 1.0 } else { 0.0 }),
            _ => None,
        }
    }

    /// Float field with a default. Missing and wrong-typed both take the
    /// default: the runner may omit a field (lane_turns) and a bot that dies
    /// on it is a bot that scores nothing.
    pub fn f(&self, key: &str, default: f64) -> f64 {
        self.get(key).and_then(|v| v.num()).unwrap_or(default)
    }

    pub fn i(&self, key: &str, default: i64) -> i64 {
        match self.get(key).and_then(|v| v.num()) {
            Some(n) => n as i64,
            None => default,
        }
    }

    pub fn flag(&self, key: &str, default: bool) -> bool {
        match self.get(key) {
            Some(J::Bool(b)) => *b,
            Some(J::Num(n)) => *n != 0.0,
            _ => default,
        }
    }

    pub fn arr(&self, key: &str) -> &[J] {
        match self.get(key) {
            Some(J::Arr(items)) => items,
            _ => &EMPTY,
        }
    }

    pub fn has(&self, key: &str) -> bool {
        !matches!(self.get(key), None | Some(J::Null))
    }

    pub fn text(&self, key: &str) -> &str {
        match self.get(key) {
            Some(J::Str(s)) => s.as_str(),
            _ => "",
        }
    }
}

pub fn parse(src: &str) -> Option<J> {
    let bytes = src.as_bytes();
    let mut at = 0usize;
    let value = parse_value(bytes, &mut at)?;
    Some(value)
}

fn skip_ws(b: &[u8], at: &mut usize) {
    while *at < b.len() && matches!(b[*at], b' ' | b'\t' | b'\n' | b'\r') {
        *at += 1;
    }
}

fn parse_value(b: &[u8], at: &mut usize) -> Option<J> {
    skip_ws(b, at);
    match *b.get(*at)? {
        b'{' => parse_obj(b, at),
        b'[' => parse_arr(b, at),
        b'"' => parse_str(b, at).map(J::Str),
        b't' => lit(b, at, "true").map(|_| J::Bool(true)),
        b'f' => lit(b, at, "false").map(|_| J::Bool(false)),
        b'n' => lit(b, at, "null").map(|_| J::Null),
        _ => parse_num(b, at),
    }
}

fn lit(b: &[u8], at: &mut usize, word: &str) -> Option<()> {
    if b.len() >= *at + word.len() && &b[*at..*at + word.len()] == word.as_bytes() {
        *at += word.len();
        Some(())
    } else {
        None
    }
}

fn parse_num(b: &[u8], at: &mut usize) -> Option<J> {
    let start = *at;
    while *at < b.len() && matches!(b[*at], b'0'..=b'9' | b'-' | b'+' | b'.' | b'e' | b'E') {
        *at += 1;
    }
    std::str::from_utf8(&b[start..*at])
        .ok()?
        .parse::<f64>()
        .ok()
        .map(J::Num)
}

fn parse_str(b: &[u8], at: &mut usize) -> Option<String> {
    if *b.get(*at)? != b'"' {
        return None;
    }
    *at += 1;
    let mut out = String::new();
    loop {
        let c = *b.get(*at)?;
        *at += 1;
        match c {
            b'"' => return Some(out),
            b'\\' => {
                let esc = *b.get(*at)?;
                *at += 1;
                match esc {
                    b'"' => out.push('"'),
                    b'\\' => out.push('\\'),
                    b'/' => out.push('/'),
                    b'b' => out.push('\u{8}'),
                    b'f' => out.push('\u{c}'),
                    b'n' => out.push('\n'),
                    b'r' => out.push('\r'),
                    b't' => out.push('\t'),
                    b'u' => {
                        let hex = std::str::from_utf8(b.get(*at..*at + 4)?).ok()?;
                        let code = u32::from_str_radix(hex, 16).ok()?;
                        *at += 4;
                        // Surrogate pairs: the payload carries star names, which
                        // can be non-ASCII. A lone surrogate becomes U+FFFD.
                        let ch = if (0xD800..0xDC00).contains(&code)
                            && b.get(*at) == Some(&b'\\')
                            && b.get(*at + 1) == Some(&b'u')
                        {
                            let low = std::str::from_utf8(b.get(*at + 2..*at + 6)?).ok()?;
                            let low = u32::from_str_radix(low, 16).ok()?;
                            *at += 6;
                            char::from_u32(0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00))
                        } else {
                            char::from_u32(code)
                        };
                        out.push(ch.unwrap_or('\u{fffd}'));
                    }
                    _ => return None,
                }
            }
            _ => {
                // Copy the whole UTF-8 sequence, not the lead byte alone.
                let extra = match c {
                    0x00..=0x7f => 0,
                    0xc0..=0xdf => 1,
                    0xe0..=0xef => 2,
                    _ => 3,
                };
                let start = *at - 1;
                *at += extra;
                out.push_str(std::str::from_utf8(b.get(start..*at)?).ok()?);
            }
        }
    }
}

fn parse_arr(b: &[u8], at: &mut usize) -> Option<J> {
    *at += 1; // '['
    let mut items = Vec::new();
    skip_ws(b, at);
    if *b.get(*at)? == b']' {
        *at += 1;
        return Some(J::Arr(items));
    }
    loop {
        items.push(parse_value(b, at)?);
        skip_ws(b, at);
        match *b.get(*at)? {
            b',' => *at += 1,
            b']' => {
                *at += 1;
                return Some(J::Arr(items));
            }
            _ => return None,
        }
    }
}

fn parse_obj(b: &[u8], at: &mut usize) -> Option<J> {
    *at += 1; // '{'
    let mut fields = Vec::new();
    skip_ws(b, at);
    if *b.get(*at)? == b'}' {
        *at += 1;
        return Some(J::Obj(fields));
    }
    loop {
        skip_ws(b, at);
        let key = parse_str(b, at)?;
        skip_ws(b, at);
        if *b.get(*at)? != b':' {
            return None;
        }
        *at += 1;
        fields.push((key, parse_value(b, at)?));
        skip_ws(b, at);
        match *b.get(*at)? {
            b',' => *at += 1,
            b'}' => {
                *at += 1;
                return Some(J::Obj(fields));
            }
            _ => return None,
        }
    }
}

/// The only replies this bot sends are `ready` and `orders`, so writing is a
/// pair of formatters rather than a general serializer.
pub fn ready_line(name: &str, version: &str) -> String {
    let mut out = String::new();
    let _ = write!(
        out,
        "{{\"type\":\"ready\",\"name\":\"{}\",\"version\":\"{}\"}}",
        name, version
    );
    out
}

pub fn orders_line(orders: &[(i64, i64, i64)]) -> String {
    let mut out = String::from("{\"type\":\"orders\",\"orders\":[");
    for (i, (src, dst, ships)) in orders.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        let _ = write!(
            out,
            "{{\"src\":{},\"dst\":{},\"ships\":{}}}",
            src, dst, ships
        );
    }
    out.push_str("]}");
    out
}
