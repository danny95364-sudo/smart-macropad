"""CYD 小遊戲「六鎖保險箱」的按鍵轉送程式。

    uv run --python 3.12 python -m macropad.game_bridge            自動找序列埠
    uv run --python 3.12 python -m macropad.game_bridge --port COM4
    uv run --python 3.12 python -m macropad.game_bridge -v         印出 CYD 回應

鍵盤（RegisterHotKey，組合鍵讀 config.toml [hotkeys]）→ 序列埠一行 {"k":N} → CYD。
遊戲邏輯全在 CYD 上，這裡只轉送按鍵。結束：Ctrl+C 或 quit_hotkey。

執行前要先停掉常駐程式 python -m macropad（它佔著序列埠、也註冊了同一組熱鍵）。
CYD 要先燒 cyd_game 韌體（見 design/cyd-game-plan.md）。

鍵位對齊看板：config 的 display_flip_keys = true 時 1↔6、2↔5、3↔4 對調，
和看板一樣讓畫面上的鎖芯位置對齊實體按鍵。旋鈕左右若要反過來，在 config 加
knob_reverse = true（預設不反轉）。
"""

import argparse
import json
import queue
import sys
import time

from . import config as config_mod
from .display import SERIAL_BAUD, find_serial_port
from .hotkeys import HotkeyListener

QUIT_KEY = "quit"
RETRY_S = 2


def map_key(key, flip_keys=False, knob_reverse=False):
    """實體鍵號（1～9）→ 送給 CYD 的鍵號。"""
    if flip_keys and 1 <= key <= 6:
        return 7 - key
    if knob_reverse and key in (7, 8):
        return 15 - key
    return key


def open_serial(port):
    import serial

    ser = serial.Serial()
    ser.port = port
    ser.baudrate = SERIAL_BAUD
    ser.timeout = 0
    ser.write_timeout = 0.5
    # DTR / RTS 不拉，開埠時才不會讓 ESP32 重開機
    ser.dtr = False
    ser.rts = False
    ser.open()
    return ser


class Link:
    """序列埠連線；斷了就丟掉，下次送鍵時再重連。"""

    def __init__(self, port, verbose=False):
        self.port = port
        self.verbose = verbose
        self.ser = None
        self.next_try = 0.0

    def connect(self):
        if self.ser is not None:
            return True
        if time.monotonic() < self.next_try:
            return False
        self.next_try = time.monotonic() + RETRY_S
        port = self.port or find_serial_port()
        if not port:
            print("找不到 CH340 / CP210x 序列埠，稍後重試")
            return False
        try:
            self.ser = open_serial(port)
        except Exception as e:
            print(f"{port} 開不起來：{e}（常駐程式還開著？）稍後重試")
            return False
        print(f"已連上 {port}")
        return True

    def send_key(self, k):
        if not self.connect():
            return
        try:
            self.ser.write((json.dumps({"k": k}) + "\n").encode())
        except Exception as e:
            print(f"送出失敗（可能被拔掉）：{e}，稍後重試")
            self.drop()

    def poll(self):
        """讀掉 CYD 的回應（ok / err / boot），-v 時印出來。"""
        if self.ser is None:
            return
        try:
            data = self.ser.read(4096)
        except Exception:
            self.drop()
            return
        if data and self.verbose:
            for line in data.decode("utf-8", "replace").splitlines():
                if line.strip():
                    print(f"  CYD: {line.strip()}")

    def drop(self):
        try:
            self.ser.close()
        except Exception:
            pass
        self.ser = None

    def close(self):
        if self.ser is not None:
            self.drop()


def main():
    ap = argparse.ArgumentParser(description="六鎖保險箱：鍵盤 → CYD 按鍵轉送")
    ap.add_argument(
        "--port", default="", help="序列埠，例如 COM4；預設讀 config 或自動找"
    )
    ap.add_argument("-v", "--verbose", action="store_true", help="印出 CYD 回應")
    args = ap.parse_args()
    # 背景執行（輸出導到檔案）時也要即時看到訊息
    sys.stdout.reconfigure(errors="replace", line_buffering=True)

    cfg = config_mod.load()
    flip = bool(cfg.raw.get("display_flip_keys", False))
    knob_rev = bool(cfg.raw.get("knob_reverse", False))
    port = args.port or str(cfg.raw.get("display_serial_port", "") or "")

    bindings = {
        k: v for k, v in cfg.hotkeys.items() if k in {str(i) for i in range(1, 10)}
    }
    bindings[QUIT_KEY] = cfg.raw.get("quit_hotkey", "ctrl+alt+shift+0")
    listener = HotkeyListener(bindings)
    listener.start()
    listener.ready.wait(3)
    if listener.failed:
        print(
            f"這些熱鍵註冊失敗（常駐程式 python -m macropad 還開著？）：{listener.failed}"
        )
        if len(listener.failed) == len(bindings):
            sys.exit(1)

    link = Link(port, verbose=args.verbose)
    link.connect()
    print(
        f"六鎖保險箱轉送中（鍵位對調 {'開' if flip else '關'}，旋鈕反轉 {'開' if knob_rev else '關'}）"
        "，Ctrl+C 結束"
    )
    try:
        while True:
            try:
                key, _exe = listener.events.get(timeout=0.1)
            except queue.Empty:
                link.poll()
                continue
            if key == QUIT_KEY:
                break
            k = map_key(int(key), flip, knob_rev)
            if args.verbose:
                print(f"鍵 {key} → 送 {k}")
            link.send_key(k)
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()
        link.close()
        print("結束")


if __name__ == "__main__":
    main()
