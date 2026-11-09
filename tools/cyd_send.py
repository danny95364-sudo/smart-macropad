"""送假指令給 CYD 單字卡韌體（env cyd_vocab），印出板子回的訊息。測協定與截圖用。

    uv run python tools/cyd_send.py card                       # 範例 learn 卡
    uv run python tools/cyd_send.py card --id invoice --kind due  # 從 vocab/tsl.csv 讀這個 id
    uv run python tools/cyd_send.py card --word x --zh 中文 --example "..." --sl D12·新3 --sr 答8·剩5
    uv run python tools/cyd_send.py flip | pause | resume
    uv run python tools/cyd_send.py toast 會 ok               # 色系 ok / bad / info
    uv run python tools/cyd_send.py status D12·新3 答8·剩5
    uv run python tools/cyd_send.py listen                     # 只聽（看 tap / hold / back / done）

--wait 秒數：送完後繼續印板子回覆多久（預設 1.5）。協定見 design/cyd-vocab-impl-plan.md 段 C。
COM4 被 game_bridge（背單字.bat）佔用時要先停掉它。
"""

import argparse
import csv
import sys
import time
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = {
    "id": "reimburse",
    "word": "reimburse",
    "pos": "動",
    "zh": "報銷；償還",
    "example": "The company will reimburse your travel costs.",
}


def load_csv_row(rid):
    path = ROOT / "vocab" / "tsl.csv"
    with path.open(encoding="utf-8-sig", newline="") as f:
        lines = [l for l in f if l.strip() and not l.lstrip().startswith("#")]
    for r in csv.DictReader(lines):
        if r["id"] == rid:
            return r
    sys.exit(f"vocab/tsl.csv 找不到 id {rid}")


def clean(s):
    return s.replace("\t", " ").replace("\n", " ").replace("\r", " ")


def build_line(a):
    c = a.cmd
    if c == "card":
        row = dict(SAMPLE)
        if a.id and not a.word:
            row.update(load_csv_row(a.id))
        for k in ("id", "word", "pos", "zh", "example"):
            v = getattr(a, k)
            if v is not None:
                row[k] = v
        f = [
            row["id"],
            a.kind,
            row["word"],
            row["pos"],
            row["zh"],
            row["example"],
            a.sl,
            a.sr,
        ]
        return "\t".join(["card"] + [clean(x) for x in f])
    if c == "toast":
        return f"toast\t{clean(a.text)}\t{a.kind}"
    if c == "status":
        return f"status\t{clean(a.left)}\t{clean(a.right)}"
    if c in ("flip", "pause", "resume"):
        return c
    return None  # listen


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM4")
    ap.add_argument("--wait", type=float, default=1.5, help="送完後聽回覆幾秒")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("card")
    p.add_argument("--id")
    p.add_argument("--kind", choices=("learn", "due"), default="learn")
    p.add_argument("--word")
    p.add_argument("--pos")
    p.add_argument("--zh")
    p.add_argument("--example")
    p.add_argument("--sl", default="D12·新3", help="狀態列左")
    p.add_argument("--sr", default="答8·剩5", help="狀態列右")
    p = sub.add_parser("toast")
    p.add_argument("text")
    p.add_argument("kind", nargs="?", choices=("ok", "bad", "info"), default="info")
    p = sub.add_parser("status")
    p.add_argument("left")
    p.add_argument("right")
    for c in ("flip", "pause", "resume"):
        sub.add_parser(c)
    p = sub.add_parser("listen")
    p.add_argument("seconds", nargs="?", type=float, default=30)
    a = ap.parse_args()

    line = build_line(a)
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout = a.port, 115200, 0.1
    ser.dtr = ser.rts = False  # 不讓 ESP32 重開機
    ser.open()
    ser.reset_input_buffer()
    if line is not None:
        data = (line + "\n").encode("utf-8")
        if len(data) > 512:
            print(
                f"警告：這行 {len(data)} bytes，超過板子 512 bytes 緩衝",
                file=sys.stderr,
            )
        ser.write(data)
        print(">", line.replace("\t", " | "))
    wait = a.seconds if a.cmd == "listen" else a.wait
    end = time.time() + wait
    while time.time() < end:
        r = ser.readline()
        if r:
            print("<", r.decode("utf-8", "replace").rstrip("\r\n").replace("\t", " | "))
    ser.close()


if __name__ == "__main__":
    main()
