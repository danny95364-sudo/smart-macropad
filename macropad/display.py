"""看板輸出：把「看板要顯示什麼」送出去。

目前兩種輸出（config.toml 的 display）：
    sim     送給電腦上的模擬視窗（localhost UDP），啟動時順便開視窗
    serial  送給 ESP32 CYD（USB 序列埠，display_serial_port 空字串 = 自動找）
    off     不送

看板只是附加功能：開不起來、送失敗都只記 log，常駐程式照常運作。

── 資料格式（模擬視窗和 CYD 共用）──
一行一個 JSON（UTF-8，結尾 \\n）。欄位名稱刻意縮短，CYD 記憶體小。

模式（切換模式時送，平常每 3 秒重送一次當心跳；看板 10 秒沒收到就顯示離線）：
    {"t":"s", "m":"notepad", "n":"記事本", "c":"#1F63B8",
     "k":["鍵1",…,"鍵6"], "r":["左轉","右轉","按下"], "rn":"音量"}
    t   種類，"s" = state
    m   模式代號（換了代號看板才播切換動畫）
    n   模式名稱
    c   模式顏色 #RRGGBB
    k   1～6 鍵標籤，固定 6 個，沒設定的鍵是 ""
    r   旋鈕左轉／右轉／按下的標籤（7、8、9 鍵）
    rn  旋鈕的總稱（例如「音量」），可省略；省略時看板自己從 r 推

按鍵（按下後送）：
    {"t":"p", "i":3, "l":"測試訊息", "x":"記事本模式運作正常", "e":0}
    i   第幾顆鍵 1～9（7、8 旋鈕左右轉，9 旋鈕按下）
    l   標籤
    x   結果文字，可為空字串；有字時看板顯示結果卡
    e   1 = 執行失敗（x 放錯誤訊息），0 = 成功

結束（常駐程式關閉前送）：
    {"t":"o"}   看板直接顯示離線
"""

import json
import socket
import subprocess
import sys
import time

from .config import ROOT

DEFAULT_PORT = 47321
HEARTBEAT_MS = 3000

# 設定檔沒寫 color 時用的模式色（照 design/screen-design.html 方向 B）
MODE_COLORS = {
    "default": "#44546A",
    "ppt": "#C4501E",
    "creo": "#1E7F4F",
    "math": "#1F63B8",
    "mail": "#6B44B8",
    "patent": "#8A6A0C",
}
FALLBACK_COLOR = "#44546A"


def state_message(cfg, mode_id):
    """組出某個模式的 state 訊息。沒設定的鍵退回 default 模式（和按鍵時一樣）。"""
    mode = cfg.modes.get(mode_id, {})

    def label(key):
        _, action = cfg.action_for(mode_id, str(key))
        return (action or {}).get("label", "")

    msg = {
        "t": "s",
        "m": mode_id,
        "n": mode.get("name", mode_id),
        "c": mode.get("color", MODE_COLORS.get(mode_id, FALLBACK_COLOR)),
        "k": [label(i) for i in range(1, 7)],
        "r": [label(i) for i in (7, 8, 9)],
    }
    if mode.get("knob"):
        msg["rn"] = mode["knob"]
    return msg


def press_message(key, label, result="", error=False):
    return {"t": "p", "i": int(key), "l": label, "x": result or "", "e": int(error)}


class Display:
    """看板輸出的共同介面；這個基本版什麼都不做（display = "off"）。"""

    def start(self):
        pass

    def send(self, msg):
        pass

    def close(self):
        pass


class SimDisplay(Display):
    """送給模擬視窗（python -m macropad.display_sim）。"""

    def __init__(self, port=DEFAULT_PORT, launch=True):
        self.addr = ("127.0.0.1", port)
        self.launch = launch
        self.proc = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def start(self):
        if not self.launch:
            return
        try:
            self.proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "macropad.display_sim",
                    "--port",
                    str(self.addr[1]),
                ],
                cwd=ROOT,
            )
        except Exception as e:
            print(f"看板模擬視窗開不起來：{e}")

    def send(self, msg):
        if self.sock.fileno() == -1:
            return  # 已經關閉（結束中還有排定的心跳）
        try:
            line = json.dumps(msg, ensure_ascii=False) + "\n"
            self.sock.sendto(line.encode("utf-8"), self.addr)
        except Exception as e:
            # 視窗還沒開好或被關掉時會失敗，不影響常駐程式
            print(f"看板送出失敗：{e}")

    def close(self):
        if self.sock.fileno() == -1:
            return  # 已經關過
        self.send({"t": "o"})
        try:
            self.sock.close()
        except Exception:
            pass
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass


SERIAL_BAUD = 115200
RETRY_S = 5  # 沒接上時多久重試一次（靠心跳觸發，不另開執行緒）
# CYD 常見的 USB 轉序列晶片：CH340 / CH9102（WCH）、CP210x（Silicon Labs）
USB_SERIAL_IDS = {(0x1A86, 0x7523), (0x1A86, 0x55D4), (0x10C4, 0xEA60)}


def find_serial_port():
    """自動找 CH340 / CP210x 的序列埠，找不到回傳 None。"""
    from serial.tools import list_ports

    for p in list_ports.comports():
        desc = f"{p.description} {p.manufacturer or ''}".upper()
        if (p.vid, p.pid) in USB_SERIAL_IDS or "CH340" in desc or "CP210" in desc:
            return p.device
    return None


class SerialDisplay(Display):
    """送給 ESP32 CYD（USB 序列埠，115200）。資料格式和 SimDisplay 一樣，一行一個 JSON。

    port 空字串 = 自動找 CH340 / CP210x。找不到、被拔掉都只記 log，
    下次送資料時（心跳每 3 秒）超過 RETRY_S 秒就再試一次。
    """

    def __init__(self, port="", flip_keys=False):
        self.port = (port or "").strip()
        self.flip_keys = flip_keys  # 看板上 1↔6、2↔5、3↔4 對調，對齊實體鍵盤擺放方向
        self.ser = None
        self.next_try = 0.0
        self.closed = False
        self.warned = False  # 同一種失敗只記一次，避免 log 每 5 秒一行

    def start(self):
        self._connect()

    def _connect(self):
        self.next_try = time.monotonic() + RETRY_S
        try:
            import serial
        except ImportError:
            if not self.warned:
                print("看板 serial：沒有安裝 pyserial（uv add pyserial）")
                self.warned = True
            return False
        port = self.port or find_serial_port()
        if not port:
            if not self.warned:
                print("看板 serial：找不到 CH340 / CP210x 序列埠，稍後重試")
                self.warned = True
            return False
        try:
            ser = serial.Serial()
            ser.port = port
            ser.baudrate = SERIAL_BAUD
            ser.timeout = 0
            ser.write_timeout = 0.5
            # DTR / RTS 不拉，開埠時才不會讓 ESP32 重開機
            ser.dtr = False
            ser.rts = False
            ser.open()
        except Exception as e:
            if not self.warned:
                print(f"看板 serial：{port} 開不起來：{e}，稍後重試")
                self.warned = True
            return False
        self.ser = ser
        self.warned = False
        print(f"看板 serial：已連上 {port}")
        return True

    def send(self, msg):
        if self.closed:
            return
        if self.ser is None:
            if time.monotonic() < self.next_try or not self._connect():
                return
        if self.flip_keys:
            msg = flip_key_order(msg)
        try:
            line = json.dumps(msg, ensure_ascii=False) + "\n"
            self.ser.write(line.encode("utf-8"))
            self.ser.reset_input_buffer()  # 看板回的 ok / err 不需要，丟掉免得塞滿
        except Exception as e:
            print(f"看板 serial 送出失敗（可能被拔掉）：{e}，稍後重試")
            self._drop()

    def _drop(self):
        try:
            self.ser.close()
        except Exception:
            pass
        self.ser = None
        self.next_try = time.monotonic() + RETRY_S

    def close(self):
        if self.closed:
            return
        self.send({"t": "o"})
        self.closed = True
        if self.ser is not None:
            try:
                self.ser.flush()
            except Exception:
                pass
            self._drop()


def make_display(cfg):
    kind = str(cfg.raw.get("display", "sim")).lower()
    if kind == "sim":
        return SimDisplay(int(cfg.raw.get("display_port", DEFAULT_PORT)))
    if kind == "serial":
        return SerialDisplay(
            str(cfg.raw.get("display_serial_port", "") or ""),
            flip_keys=bool(cfg.raw.get("display_flip_keys", False)),
        )
    return Display()


def flip_key_order(msg):
    """1～6 鍵在看板上的位置反過來（1↔6、2↔5、3↔4）；旋鈕 7～9 不動。"""
    if msg.get("t") == "s" and len(msg.get("k", [])) == 6:
        return {**msg, "k": msg["k"][::-1]}
    if msg.get("t") == "p" and 1 <= int(msg.get("i", 0)) <= 6:
        return {**msg, "i": 7 - int(msg["i"])}
    return msg
