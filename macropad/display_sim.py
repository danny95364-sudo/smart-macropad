"""電腦上的看板模擬視窗（ESP32 CYD 到貨前用）。

    python -m macropad.display_sim                 等常駐程式送資料
    python -m macropad.display_sim --port 47321    指定 UDP 埠

畫面 320×240（CYD 2.8 吋橫放），照 design/screen-design.html 的「方向 B 色塊」畫。
無邊框、置頂、可拖曳；雙擊切換 1× / 2×，右鍵選單可關閉。位置與縮放記在 display_sim.json。
資料格式寫在 display.py 開頭註解；這裡只負責收和畫。

和設計稿的差別：設計稿用 Tabler 圖示，這裡沒有圖示字型，
鍵格左上改顯示實體鍵編號，模式方塊用六個小方塊代表鍵盤。
"""

import argparse
import ctypes
import json
import socket
import sys
import time
import tkinter as tk
import tkinter.font as tkfont

from .config import ROOT
from .display import DEFAULT_PORT

W, H = 320, 240
FONT = "Microsoft JhengHei"
POS_FILE = ROOT / "display_sim.json"

# 方向 B 配色
BG = "#000000"
TX = "#FFFFFF"
TX2 = "#A0A0A0"
CARD = "#F2F2F2"
CTX = "#111111"
CTX2 = "#555555"
LINE = "#333333"
ERR = "#D92D20"
OFF_COLOR = "#333333"

# 版面（邏輯座標，畫的時候再乘縮放）
XS, YS = (56, 144, 232), (4, 122)
TILE_W, TILE_H = 84, 114

PRESS_MS = 150  # 按下的格子變白
CARD_MS = 4000  # 結果卡停留
ERR_MS = 6000  # 錯誤卡停留
SWITCH_MS = 500  # 切換模式的大色塊
KNOB_MS = 300  # 旋鈕轉動時左條反白
OFFLINE_S = 10  # 多久沒收到資料算離線

user32 = ctypes.WinDLL("user32")
GWL_EXSTYLE = -20
WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x8, 0x80, 0x08000000


def _mix(c1, c2, a):
    """c1 和 c2 混色，a = c2 的比例。tkinter 沒有透明度，用混色代替。"""
    p = [int(c1[i : i + 2], 16) for i in (1, 3, 5)]
    q = [int(c2[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * a):02X}" for x, y in zip(p, q))


def _knob_name(left, right):
    """沒有 rn 時從左右轉標籤推旋鈕總稱：「音量 -」「音量 +」→「音量」。"""
    a, b = left.rstrip(" +-－＋"), right.rstrip(" +-－＋")
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return a[:n].strip() if n >= 2 else ""


class Board:
    def __init__(self, port, scale=None, pos=None):
        saved = self._load_pos()
        self.scale = scale or saved.get("scale", 2)
        self.root = tk.Tk()
        self.root.title("看板模擬")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG)
        self.canvas = tk.Canvas(self.root, bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack()
        x, y = pos or (saved.get("x"), saved.get("y"))
        if x is None:
            x = self.root.winfo_screenwidth() - W * self.scale - 40
            y = 40  # 右上角，避開右下角的提示視窗
        self.x, self.y = int(x), int(y)
        self._fonts = {}

        # 收到的狀態
        self.state = None  # 最近一次 state 訊息
        self.last_rx = 0.0  # 最近一次收到資料的時間
        self.started = time.time()
        self.offline = True
        # 暫時性的畫面（到期時間）
        self.press = None  # (鍵號, 到期)
        self.card = None  # (press 訊息, 到期)
        self.switch = None  # (舊模式名, 到期)
        self.knob = None  # 到期
        self._timer = None

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", port))
        self.sock.setblocking(False)

        self.canvas.bind("<ButtonPress-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<ButtonRelease-1>", lambda e: self._save_pos())
        self.canvas.bind("<Double-Button-1>", lambda e: self.set_scale(3 - self.scale))
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="1× 實際大小", command=lambda: self.set_scale(1))
        self.menu.add_command(label="2× 放大", command=lambda: self.set_scale(2))
        self.menu.add_separator()
        self.menu.add_command(label="關閉看板", command=self.root.destroy)
        self.canvas.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))

        self.set_scale(self.scale, save=False)
        self.root.after(50, self._no_activate)
        self.root.after(20, self._poll)
        self.root.after(1000, self._tick)

    # ── 視窗 ──
    def _no_activate(self):
        # 點看板不搶前景，常駐程式才不會把看板自己當成目前的軟體
        hwnd = user32.GetParent(self.root.winfo_id())
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(
            hwnd,
            GWL_EXSTYLE,
            style | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
        )

    def set_scale(self, s, save=True):
        self.scale = s
        self._fonts = {}
        self.canvas.config(width=W * s, height=H * s)
        self.root.geometry(f"{W * s}x{H * s}+{self.x}+{self.y}")
        self.redraw()
        if save:
            self._save_pos()

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.x, e.y_root - self.y

    def _drag_move(self, e):
        self.x, self.y = e.x_root - self._dx, e.y_root - self._dy
        self.root.geometry(f"+{self.x}+{self.y}")

    @staticmethod
    def _load_pos():
        try:
            return json.loads(POS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_pos(self):
        try:
            data = {"x": self.x, "y": self.y, "scale": self.scale}
            POS_FILE.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass

    # ── 收資料 ──
    def _poll(self):
        while True:
            try:
                data, _ = self.sock.recvfrom(65536)
            except (BlockingIOError, OSError):
                break
            for line in data.decode("utf-8", "replace").splitlines():
                try:
                    self.handle(json.loads(line))
                except Exception as e:
                    print(f"看不懂的資料：{line[:80]}（{e}）")
        self.root.after(20, self._poll)

    def _tick(self):
        # 太久沒收到資料就顯示離線；離線時每秒更新分鐘數
        if not self.offline and time.time() - self.last_rx > OFFLINE_S:
            self.offline = True
            self.redraw()
        elif self.offline:
            self.redraw()
        self.root.after(1000, self._tick)

    def handle(self, msg):
        now = time.time()
        kind = msg.get("t")
        if kind == "o":
            self.offline = True
            self.started = now
            self.redraw()
            return
        self.last_rx = now
        was_offline, self.offline = self.offline, False
        if kind == "s":
            old = self.state
            self.state = msg
            if old and old.get("m") != msg.get("m") and not was_offline:
                self.switch = (old.get("n", ""), now + SWITCH_MS / 1000)
                self.card = None
        elif kind == "p" and self.state:
            i = int(msg.get("i", 0))
            self.switch = None
            if 1 <= i <= 6:
                self.press = (i, now + PRESS_MS / 1000)
            else:
                self.knob = now + KNOB_MS / 1000
            if msg.get("e"):
                self.card = (msg, now + ERR_MS / 1000)
            elif msg.get("x"):
                self.card = (msg, now + CARD_MS / 1000)
            elif self.card and 1 <= i <= 6:
                self.card = None  # 任一鍵收掉結果卡
        self.redraw()

    # ── 畫 ──
    def font(self, size, bold=False):
        key = (size, bold)
        if key not in self._fonts:
            self._fonts[key] = tkfont.Font(
                family=FONT,
                size=-round(size * self.scale),
                weight="bold" if bold else "normal",
            )
        return self._fonts[key]

    def _measure(self, text, size, bold):
        return self.font(size, bold).measure(text) / self.scale

    def wrap(self, text, size, bold, width):
        """依寬度斷行；「／」和空白當成可斷點，斷在行首時丟掉。"""
        lines, cur = [], ""
        for ch in text:
            if self._measure(cur + ch, size, bold) > width and cur:
                lines.append(cur.rstrip(" ／/"))
                cur = "" if ch in " ／/" else ch
            else:
                cur += ch
        if cur:
            lines.append(cur)
        return [ln for ln in lines if ln]

    def rect(self, x, y, w, h, fill="", outline="", width=0, r=0):
        s = self.scale
        x0, y0, x1, y1 = x * s, y * s, (x + w) * s, (y + h) * s
        if not r:
            self.canvas.create_rectangle(
                x0, y0, x1, y1, fill=fill, outline=outline or fill, width=width * s
            )
            return
        r *= s
        if fill:
            c = self.canvas
            c.create_rectangle(x0 + r, y0, x1 - r, y1, fill=fill, width=0)
            c.create_rectangle(x0, y0 + r, x1, y1 - r, fill=fill, width=0)
            for cx, cy in (
                (x0, y0),
                (x1 - 2 * r, y0),
                (x0, y1 - 2 * r),
                (x1 - 2 * r, y1 - 2 * r),
            ):
                c.create_oval(cx, cy, cx + 2 * r, cy + 2 * r, fill=fill, width=0)
        if outline:
            # 圓角外框：四段直線＋四個 90° 弧，線寬往內縮半條
            c = self.canvas
            w = width * s
            a, b, e, f = x0 + w / 2, y0 + w / 2, x1 - w / 2, y1 - w / 2
            c.create_line(a + r, b, e - r, b, fill=outline, width=w)
            c.create_line(a + r, f, e - r, f, fill=outline, width=w)
            c.create_line(a, b + r, a, f - r, fill=outline, width=w)
            c.create_line(e, b + r, e, f - r, fill=outline, width=w)
            for cx, cy, start in (
                (a, b, 90),
                (e - 2 * r, b, 0),
                (a, f - 2 * r, 180),
                (e - 2 * r, f - 2 * r, 270),
            ):
                c.create_arc(
                    cx,
                    cy,
                    cx + 2 * r,
                    cy + 2 * r,
                    start=start,
                    extent=90,
                    style="arc",
                    outline=outline,
                    width=w,
                )

    def text(self, x, y, s, size, color, bold=False, anchor="nw"):
        k = self.scale
        self.canvas.create_text(
            x * k, y * k, text=s, font=self.font(size, bold), fill=color, anchor=anchor
        )

    def redraw(self):
        if self._timer:
            self.root.after_cancel(self._timer)
            self._timer = None
        now = time.time()
        for name in ("press", "card", "switch"):
            v = getattr(self, name)
            if v and v[-1] <= now:
                setattr(self, name, None)
        if self.knob and self.knob <= now:
            self.knob = None

        self.canvas.delete("all")
        self.rect(0, 0, W, H, fill=BG)
        if self.offline or not self.state:
            self.draw_offline()
        else:
            self.draw_live()

        # 下一個暫時畫面到期時重畫
        ends = [v[-1] for v in (self.press, self.card, self.switch) if v]
        if self.knob:
            ends.append(self.knob)
        if ends:
            ms = max(10, int((min(ends) - now) * 1000) + 5)
            self._timer = self.root.after(ms, self.redraw)

    def draw_mode_block(self, color, name):
        self.rect(0, 0, 52, 56, fill=color)
        # 六個小方塊代表鍵盤（設計稿這裡是模式圖示）
        for i in range(6):
            self.rect(15 + (i % 3) * 8, 10 + (i // 3) * 8, 6, 6, fill=TX)
        self.text(26, 34, name, 14, TX, bold=True, anchor="n")

    def draw_offline(self):
        self.draw_mode_block(OFF_COLOR, "離線")
        for i in range(6):
            self.rect(XS[i % 3], YS[i // 3], TILE_W, TILE_H, outline=LINE, width=2, r=6)
        self.rect(56, 70, 260, 100, fill=BG)
        # 斷線符號：一個圈加斜線
        k = self.scale
        self.canvas.create_oval(
            170 * k, 76 * k, 202 * k, 108 * k, outline=TX2, width=2 * k
        )
        self.canvas.create_line(
            175 * k, 103 * k, 197 * k, 81 * k, fill=TX2, width=2 * k
        )
        self.text(186, 114, "電腦未連線", 22, TX, bold=True, anchor="n")
        self.text(186, 146, "啟動 macropad 後自動恢復", 14, TX2, anchor="n")
        mins = int((time.time() - (self.last_rx or self.started)) // 60)
        self.text(26, 214, f"{mins} 分", 14, TX2, anchor="n")

    def draw_live(self):
        st = self.state
        c = st.get("c", "#44546A")
        keys = (list(st.get("k", [])) + [""] * 6)[:6]
        left, right, press = (list(st.get("r", [])) + ["", "", ""])[:3]

        self.draw_mode_block(c, st.get("n", ""))
        self.draw_knob(left, right, press, st.get("rn") or _knob_name(left, right))

        if self.switch:
            self.rect(56, 4, 260, 232, fill=c, r=6)
            self.text(186, 98, st.get("n", ""), 32, TX, bold=True, anchor="n")
            if self.switch[0]:
                self.text(186, 146, f"從 {self.switch[0]} 切換", 14, TX, anchor="n")
            return

        card = self.card[0] if self.card else None
        err = bool(card and card.get("e"))
        src = int(card.get("i", 0)) if card else 0
        for i in range(6):
            if card and i >= 3:
                continue  # 下排被結果卡蓋住
            self.draw_tile(i, keys[i], c, src=src, err=err)

        if card:
            self.draw_card(card, err)

    def draw_knob(self, left, right, press, name):
        k = self.scale
        hl = bool(self.knob)
        if hl:
            self.rect(0, 62, 52, 92, fill="#FFFFFF")
        fg = "#000000" if hl else TX
        # 旋轉圖示：一段弧加箭頭
        self.canvas.create_arc(
            17 * k,
            70 * k,
            35 * k,
            88 * k,
            start=120,
            extent=280,
            style="arc",
            outline="#555555" if hl else TX2,
            width=2 * k,
        )
        if name:
            lines = self.wrap(name, 22, True, 48)
            for j, ln in enumerate(lines[:2]):
                self.text(26, 96 + j * 26, ln, 22, fg, bold=True, anchor="n")
        else:
            self.text(26, 98, left, 14, fg, bold=True, anchor="n")
            self.text(26, 120, right, 14, fg, bold=True, anchor="n")
        self.rect(14, 160, 24, 1, fill=LINE)
        self.text(26, 170, "按下", 14, TX2, anchor="n")
        for j, ln in enumerate(self.wrap(press, 14, True, 40)[:2]):
            self.text(26, 190 + j * 18, ln, 14, TX, bold=True, anchor="n")

    def draw_tile(self, i, label, c, src=0, err=False):
        x, y = XS[i % 3], YS[i // 3]
        if not label:
            # 這顆鍵沒設定：只畫外框
            self.rect(x, y, TILE_W, TILE_H, outline=LINE, width=2, r=6)
            self.text(x + 10, y + 8, str(i + 1), 14, TX2)
            return
        bg, fg, num = c, TX, _mix(TX, c, 0.3)
        if self.press and self.press[0] == i + 1:
            bg, fg, num = "#FFFFFF", "#000000", "#555555"
        if err and src == i + 1:
            bg, num = ERR, TX
        self.rect(x, y, TILE_W, TILE_H, fill=bg, r=6)
        if err and src == i + 1:
            self.draw_warning(x + 10, y + 10, 18, TX, bg)
        else:
            self.text(x + 10, y + 8, str(i + 1), 14, num, bold=True)
        if self.card and not err and src == i + 1 and i < 3:
            self.rect(x, y + TILE_H - 6, TILE_W, 6, fill="#FFFFFF")  # 結果來源的白條

        size, lh = 22, 26
        lines = self.wrap(label, size, True, 64)
        if len(lines) > 2:
            # 設計稿是 2 字一行最多兩行；字多的標籤降到 18 px，避免蓋到左上的鍵號
            size, lh = 18, 22
            lines = self.wrap(label, size, True, 64)[:3]
        bottom = y + TILE_H - 10
        for j, ln in enumerate(reversed(lines)):
            self.text(x + 10, bottom - j * lh, ln, size, fg, bold=True, anchor="sw")

    def draw_warning(self, x, y, s, color, inner):
        k = self.scale
        pts = [(x + s / 2) * k, y * k, (x + s) * k, (y + s) * k, x * k, (y + s) * k]
        self.canvas.create_polygon(pts, fill=color, outline=color)
        self.text(
            x + s / 2, y + s * 0.3, "!", round(s * 0.6), inner, bold=True, anchor="n"
        )

    def draw_card(self, msg, err):
        label, result = msg.get("l", ""), msg.get("x", "")
        key = int(msg.get("i", 0))
        if err:
            self.rect(56, 122, 260, 114, fill=ERR, r=6)
            self.draw_warning(68, 134, 24, TX, ERR)
            self.text(100, 132, f"{label or f'鍵 {key}'} 失敗", 22, TX, bold=True)
            for j, ln in enumerate(self.wrap(result, 14, False, 236)[:2]):
                self.text(68, 166 + j * 18, ln, 14, TX)
            self.text(68, 208, "任一鍵返回", 14, _mix(TX, ERR, 0.2))
            return
        self.rect(56, 122, 260, 114, fill=CARD, r=6)
        # 結果數值：放得下就 32 px，放不下降到 22 / 18 px 並斷行
        for size, lh, n in ((32, 40, 1), (22, 28, 2), (18, 22, 3)):
            lines = self.wrap(result, size, True, 236)
            if len(lines) <= n:
                break
        lines = lines[:n]
        top = 130 + max(0, (72 - lh * len(lines)) // 2)
        for j, ln in enumerate(lines):
            self.text(68, top + j * lh, ln, size, CTX, bold=True)
        where = "旋鈕" if key >= 7 else f"鍵 {key}"
        foot = f"{label} · {where}" if label else where
        self.text(68, 208, f"{foot} · {CARD_MS // 1000} 秒後收合", 14, CTX2)

    def run(self):
        self.root.mainloop()
        self.sock.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument(
        "--scale", type=int, choices=(1, 2), help="縮放（預設用上次的，沒有就 2×）"
    )
    ap.add_argument("--pos", help="視窗位置 x,y（測試用，不記住）")
    args = ap.parse_args()
    if sys.stdout is None:
        # pythonw 沒有主控台，訊息寫進同一個 log
        log = open(ROOT / "macropad.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log

    # 高 DPI 螢幕上用實際像素畫，2× 才會是清楚的 640×480
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    pos = tuple(int(v) for v in args.pos.split(",")) if args.pos else None
    try:
        board = Board(args.port, args.scale, pos)
    except OSError as e:
        print(f"看板模擬視窗開不起來（UDP {args.port} 可能已有視窗在用）：{e}")
        sys.exit(1)
    board.run()


if __name__ == "__main__":
    main()
