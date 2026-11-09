"""CYD 看板測試：依序送 state（記事本）→ press（第 3 鍵）→ 換模式 state。

    uv run --python 3.12 --with pyserial firmware/cyd/send_demo.py
    uv run --python 3.12 --with pyserial firmware/cyd/send_demo.py --port COM4
    uv run --python 3.12 --with pyserial firmware/cyd/send_demo.py --off   最後送離線
    --hold 秒數：最後停在 PPT 頁多久（預設 20，每 3 秒重送），結束後 10 秒看板自己進離線

看板每收到一行會回 ok / err，這裡一併印出來。
"""

import argparse
import json
import sys
import time

import serial
from serial.tools import list_ports

NOTEPAD = {
    "t": "s",
    "m": "notepad",
    "n": "記事本",
    "c": "#1F63B8",
    "k": ["插入日期時間", "全選", "測試訊息", "", "", ""],
    "r": ["音量 -", "音量 +", "靜音"],
}
PRESS = {"t": "p", "i": 3, "l": "測試訊息", "x": "記事本模式運作正常", "e": 0}
PPT = {
    "t": "s",
    "m": "ppt",
    "n": "PPT",
    "c": "#C4501E",
    "k": ["複製格式", "貼上格式", "群組", "取消群組", "從這頁播放", "複製物件"],
    "r": ["上一頁", "下一頁", "水平置中"],
    "rn": "換頁",
}


def find_port():
    for p in list_ports.comports():
        desc = f"{p.description} {p.manufacturer or ''}".upper()
        if p.vid in (0x1A86, 0x10C4) or "CH340" in desc or "CP210" in desc:
            return p.device
    return None


def drain(ser, wait):
    """等 wait 秒，期間把看板回的訊息印出來。"""
    end = time.monotonic() + wait
    buf = b""
    while time.monotonic() < end:
        buf += ser.read(256)
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            print("  <-", line.decode("utf-8", "replace").rstrip())
        time.sleep(0.02)
    if buf.strip():
        print("  <-", buf.decode("utf-8", "replace").rstrip())


def send(ser, msg):
    line = json.dumps(msg, ensure_ascii=False)
    print("->", line)
    ser.write((line + "\n").encode("utf-8"))
    ser.flush()


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", help="序列埠（預設自動找 CH340 / CP210x）")
    ap.add_argument("--off", action="store_true", help="最後送 {t:o}，看板顯示離線")
    ap.add_argument(
        "--hold",
        type=float,
        default=20,
        help="最後停在 PPT 頁幾秒（每 3 秒重送，預設 20）",
    )
    args = ap.parse_args()

    port = args.port or find_port()
    if not port:
        sys.exit("找不到 CH340 / CP210x 序列埠，用 --port 指定")
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout = port, 115200, 0
    ser.dtr = ser.rts = False  # 不讓 ESP32 重開機
    ser.open()
    print(f"已開啟 {port}，等 2.5 秒（萬一開埠讓板子重開機，等它開完）")
    drain(ser, 2.5)

    send(ser, NOTEPAD)
    drain(ser, 2)
    send(ser, PRESS)
    drain(ser, 3)
    send(ser, PPT)
    drain(ser, 3)
    # 停在 PPT 狀態頁：每 3 秒重送當心跳，停止後 10 秒看板進離線
    end = time.monotonic() + args.hold
    while time.monotonic() < end:
        send(ser, PPT)
        drain(ser, 3)
    if args.off:
        send(ser, {"t": "o"})
        drain(ser, 0.5)
    ser.close()
    print("完成")


if __name__ == "__main__":
    main()
