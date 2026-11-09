#!/usr/bin/env python3
"""Check vocab CSV characters against LovyanGFX efontTW_14 / efontTW_24 glyph coverage.

Font data is a u8g2-format byte array inside lgfx_efont_tw.c (C string literal);
we decode it and walk the glyph records to collect encodings.
"""

import argparse, csv, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONTS = (
    "lgfx_efont_tw_14",
    "lgfx_efont_tw_24",
)  # efontTW_14 (zh/pos) and efontTW_24 (zh)
ESC = {
    "n": 10,
    "t": 9,
    "r": 13,
    "a": 7,
    "b": 8,
    "f": 12,
    "v": 11,
    "\\": 92,
    "'": 39,
    '"': 34,
    "?": 63,
}


def find_font_file():
    cands = []
    for base in (
        ROOT / "firmware" / "cyd" / ".pio" / "libdeps",
        Path.home() / ".pio-cyd" / "libdeps",
    ):
        if base.is_dir():
            found = sorted(
                base.glob("*/LovyanGFX/src/lgfx/Fonts/efont/lgfx_efont_tw.c"),
                key=lambda p: (
                    p.parts[-7] != "cyd_vocab",
                    str(p),
                ),  # prefer env cyd_vocab
            )
            cands += found
    if not cands:
        sys.exit(
            "error: lgfx_efont_tw.c not found under firmware/cyd/.pio/libdeps "
            "or ~/.pio-cyd/libdeps (build the firmware once so PlatformIO fetches LovyanGFX)"
        )
    return cands[0]


def decode_c_strings(chunk):
    out = bytearray()
    for m in re.finditer(r'"((?:[^"\\]|\\.)*)"', chunk):
        s = m.group(1)
        i = 0
        while i < len(s):
            c = s[i]
            if c != "\\":
                out += c.encode("latin-1")
                i += 1
                continue
            i += 1
            c = s[i]
            if c in "01234567":
                j = i
                while j < len(s) and j < i + 3 and s[j] in "01234567":
                    j += 1
                out.append(int(s[i:j], 8) & 255)
                i = j
            elif c == "x":
                j = i + 1
                while j < len(s) and s[j] in "0123456789abcdefABCDEF":
                    j += 1
                out.append(int(s[i + 1 : j], 16) & 255)
                i = j
            else:
                out.append(ESC[c])
                i += 1
    return bytes(out)


def extract_array(text, name):
    m = re.search(r"const uint8_t\s+" + re.escape(name) + r"\[\d+\]\s*=", text)
    if not m:
        sys.exit(f"error: array {name} not found in font file")
    e = text.find("#endif", m.end())  # array body ends at the next #endif line
    return decode_c_strings(text[m.end() : e])


def glyph_set(data):
    """u8g2 font: 23-byte header; offsets in the header are relative to byte 23.
    8-bit part: [enc u8][jump u8]; unicode part: lookup table (jump u16, enc u16)
    followed by records [enc u16 BE][jump u8], terminated by enc == 0."""
    base = 23
    start_unicode = base + ((data[21] << 8) | data[22])
    cps = set()
    p = base
    while p < start_unicode:
        enc, jump = data[p], data[p + 1]
        if jump == 0:
            break
        cps.add(enc)
        p += jump
    # the first lookup entry's jump is the table length, i.e. where glyph records begin
    p = start_unicode + ((data[start_unicode] << 8) | data[start_unicode + 1])
    while p + 3 <= len(data):
        enc = (data[p] << 8) | data[p + 1]
        jump = data[p + 2]
        if enc == 0 or jump == 0:
            break
        cps.add(enc)
        p += jump
    return cps


def load_fonts(path):
    text = path.read_text(encoding="latin-1")
    return {n: glyph_set(extract_array(text, n)) for n in FONTS}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(ROOT / "vocab" / "tsl.csv"))
    ap.add_argument(
        "--stats", action="store_true", help="print code point counts per font"
    )
    a = ap.parse_args()
    fp = find_font_file()
    fonts = load_fonts(fp)
    if a.stats:
        print(f"font file: {fp}")
        for n, s in fonts.items():
            print(f"{n}: {len(s)} code points")
    both = set.intersection(*fonts.values())
    csvp = Path(a.csv)
    if not csvp.is_file():
        sys.exit(f"error: csv not found: {csvp}")
    with csvp.open(encoding="utf-8-sig", newline="") as f:
        lines = [l for l in f if not l.lstrip().startswith("#") and l.strip()]
    missing = 0

    def report(row, rid, field, ch):
        nonlocal missing
        missing += 1
        print(f"row {row} id={rid} field={field} char={ch} U+{ord(ch):04X}")

    for n, r in enumerate(csv.DictReader(lines), start=1):
        rid = r.get("id", "")
        for field in ("zh", "pos", "topic"):
            for ch in r.get(field) or "":
                if ch == " ":
                    continue
                if ord(ch) not in both:
                    report(n, rid, field, ch)
        for field in ("example", "word"):
            for ch in r.get(field) or "":
                if not (0x20 <= ord(ch) <= 0x7E):
                    report(n, rid, field, ch)
    print(f"missing: {missing}")
    sys.exit(0 if missing == 0 else 1)


if __name__ == "__main__":
    main()
