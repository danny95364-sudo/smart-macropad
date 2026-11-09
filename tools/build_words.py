"""合併字表：vocab/tsl.csv + vocab/ngsl_a.csv + vocab/ngsl_b.csv → vocab/words.csv

穿插規則：兩邊各自照原頻率序，依「在自己表中的相對位置」交錯合併，
所以多益專用字（TSL）和 NGSL 1001～2809 會一路混著出。rank 改成合併後的順序。
排除不適合放在辦公桌螢幕上的字（DROP）。

用法：uv run python tools/build_words.py
"""

import csv
from pathlib import Path

VOCAB = Path(__file__).resolve().parent.parent / "vocab"
DROP = {"gay", "abortion", "terrorist", "damn", "bloody", "slave", "sex", "hell"}
FIELDS = ["id", "word", "pos", "zh", "example", "topic", "rank"]


def read(name):
    with open(VOCAB / name, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(line for line in f if not line.startswith("#")))
    return sorted(rows, key=lambda r: int(r["rank"]))


def main():
    tsl = read("tsl.csv")
    ngsl = read("ngsl_a.csv") + read("ngsl_b.csv")
    keyed = [((i + 0.5) / len(tsl), 0, r) for i, r in enumerate(tsl)]
    keyed += [((i + 0.5) / len(ngsl), 1, r) for i, r in enumerate(ngsl)]
    keyed.sort(key=lambda k: (k[0], k[1]))
    out, seen = [], set()
    for _, _, r in keyed:
        if r["id"] in DROP or r["id"] in seen:
            continue
        seen.add(r["id"])
        out.append({k: r[k] for k in FIELDS} | {"rank": str(len(out) + 1)})
    with open(VOCAB / "words.csv", "w", encoding="utf-8", newline="") as f:
        f.write(
            "# Merged from tsl.csv (TOEIC Service List 1.2) and ngsl_a/b.csv (NGSL 1.2 ranks 1001-2809).\n"
        )
        f.write(
            "# Both lists: Browne, Culligan (& Phillips), newgeneralservicelist.com, CC BY-SA 4.0. Built by tools/build_words.py\n"
        )
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(out)
    print(f"tsl {len(tsl)} + ngsl {len(ngsl)} → words.csv {len(out)}")


if __name__ == "__main__":
    main()
