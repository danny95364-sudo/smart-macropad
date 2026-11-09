"""CYD 截圖：送 "shot" 給板子，收 RGB565 原始像素存成 PNG。

    uv run python tools/cyd_shot.py [--port COM4] [--out shot.png]

COM4 被 game_bridge（背單字.bat）佔用時要先停掉它。115200 baud 傳一張約 14 秒。
"""
import argparse
import struct
import sys
import time
import zlib

import serial


def write_png(path, w, h, rgb565_be):
    rows = bytearray()
    for y in range(h):
        rows.append(0)
        base = y * w * 2
        for x in range(w):
            v = (rgb565_be[base + 2 * x] << 8) | rgb565_be[base + 2 * x + 1]
            r, g, b = (v >> 11) & 31, (v >> 5) & 63, v & 31
            rows += bytes(((r * 527 + 23) >> 6, (g * 259 + 33) >> 6, (b * 527 + 23) >> 6))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(bytes(rows), 9)))
        f.write(chunk(b"IEND", b""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM4")
    ap.add_argument("--out", default="shot.png")
    args = ap.parse_args()

    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout = args.port, 115200, 30
    ser.dtr = ser.rts = False  # 不讓 ESP32 重開機
    ser.open()
    ser.reset_input_buffer()
    ser.write(b"shot\n")

    deadline = time.time() + 10
    while True:
        line = ser.readline().decode("utf-8", "replace").strip()
        if line.startswith("shot "):
            w, h = map(int, line.split()[1:3])
            break
        if time.time() > deadline:
            sys.exit("板子沒回應 shot（韌體太舊或埠不對）")

    need = w * h * 2
    data = ser.read(need)
    ser.close()
    if len(data) != need:
        sys.exit(f"只收到 {len(data)}/{need} bytes")
    write_png(args.out, w, h, data)
    print(f"{args.out} {w}x{h}")


if __name__ == "__main__":
    main()
