"""背單字整合（段 D）：熱鍵 → Vocab 排程 → CYD 序列埠。

由常駐程式（python -m macropad）在 config 的 [vocab] enabled = true 時啟用；
這時 CYD 給背單字用，看板 display 不啟動（同一個 COM 埠只能一方開）。
協定見 design/cyd-vocab-impl-plan.md 段 C；鍵位見 design/cyd-vocab-plan.md「鍵位」。

規格未明處的解讀：
- 鍵號：HotkeyListener 給的編號就是 config [hotkeys] 的 1～9，也就是 SIKAI 設定的實體鍵號。
  display_flip_keys 只影響看板上的顯示位置；舊的 game_bridge 會送 7−鍵號給板子、韌體再轉回，
  新協定鍵不送板子，所以這裡一律用實體鍵號分派，不做 7−k（physical_key()）。
- 作答後：立刻 toast（會 ok／不會 bad）。按「會」立刻換下一張；按「不會」若還在正面就送 flip
  給答案看，BAD_HOLD_S 秒後才換。這段期間板子回的 done 不理。（2026-11-09 試用後修改）
- 旋鈕左轉：只回上一張（重看，不評分、不算曝光）；撤銷已移除。看過上一張再右轉會依序回到
  原本那張，再右轉才出新卡。已經作答過的卡重看時按會／不會不再評分。
- 旋鈕右轉跳過：剛送上的新卡如果還沒作答，收回那次曝光（Vocab.exposed(id, -1)）。
- 到期卡 60 秒沒按鍵：先送 flip 給答案看，DUE_REVEAL_S 秒後換下一張；不評分、今天不再出。
- 暫停（觸控長按）：只停自動前進（板子 learn 計時、PC 的到期卡 60 秒、作答後自動換卡），
  手動按鍵照常有效。鎖定則完全不送卡、按鍵也不處理。
- 使用中時間：沒暫停、沒鎖定、序列埠連著時，tick() 把經過的秒數（每次最多 ACTIVE_STEP_MAX）
  交給 Vocab.add_active()，新字依此涓流放出。
- 曝光（Vocab.exposed）：新卡第一次送上板子時記一次；重看、重送（重連、解鎖）不記。
- 序列埠斷線（寫入或讀取失敗）：丟掉連線，每 RECONNECT_S 秒重試；接上或板子重開機（hello）
  就重送目前這張卡和暫停狀態。
"""

from __future__ import annotations

import dataclasses
import queue
import time

RECONNECT_S = 5.0  # 序列埠斷線重試間隔
DUE_TIMEOUT_S = 60.0  # 到期卡無按鍵秒數 → 翻面給答案
DUE_REVEAL_S = 8.0  # 到期卡被忽略：翻面後多久換下一張
GOOD_HOLD_S = 0.0  # 按「會」立刻換下一張
BAD_HOLD_S = 3.0  # 按「不會」：翻面給答案看這麼久再換
ACTIVE_STEP_MAX = 1.0  # 每次 tick 最多算幾秒使用中時間（防睡眠後一次跳很多）
EMPTY_RETRY_S = 60.0  # 今天沒卡時多久再問一次（跨 04:00 換日）
HISTORY = 20  # 旋鈕左轉可回看的張數
LINE_MAX = 500  # 板子一行緩衝 512 bytes

ROLE_BAD, ROLE_GOOD, ROLE_PREV, ROLE_NEXT, ROLE_FLIP = (
    "bad",
    "good",
    "prev",
    "next",
    "flip",
)
VOCAB_KEYS = {"1", "2", "7", "8", "9"}


def physical_key(key):
    """熱鍵編號 → 實體鍵號。兩者相同（見模組說明）；display_flip_keys 不影響分派。"""
    return int(key)


def key_role(key, knob_reverse=False):
    """實體鍵號 → 背單字動作；3～6 等其他鍵回傳 None（交給原本的 actions）。"""
    k = physical_key(key)
    if knob_reverse and k in (7, 8):
        k = 15 - k
    return {1: ROLE_BAD, 2: ROLE_GOOD, 7: ROLE_PREV, 8: ROLE_NEXT, 9: ROLE_FLIP}.get(k)


def _clean(s):
    return str(s).replace("\t", " ").replace("\r", " ").replace("\n", " ")


def card_line(card, left=None, right=None):
    f = [
        card.id,
        card.kind,
        card.word,
        card.pos,
        card.zh,
        card.example,
        card.status_left if left is None else left,
        card.status_right if right is None else right,
    ]
    line = "\t".join(["card"] + [_clean(x) for x in f])
    while len(line.encode("utf-8")) > LINE_MAX:  # 例句太長就截斷
        f[5] = f[5][:-4] + "…" if len(f[5]) > 4 else ""
        line = "\t".join(["card"] + [_clean(x) for x in f])
    return line


# ---------------------------------------------------------------- 序列埠


def _open_serial(port):
    import serial

    from .display import SERIAL_BAUD

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


def _find_port():
    from .display import find_serial_port

    return find_serial_port()


class SerialLink:
    """一行一則的序列埠連線。斷了就丟掉，ensure() 每 RECONNECT_S 秒才真的重試一次。"""

    def __init__(
        self,
        port="",
        opener=_open_serial,
        finder=_find_port,
        clock=time.monotonic,
        log=print,
    ):
        self.port = (port or "").strip()
        self.opener = opener
        self.finder = finder
        self.clock = clock
        self.log = log
        self.ser = None
        self.next_try = 0.0
        self._buf = b""
        self._warned = None

    @property
    def connected(self):
        return self.ser is not None

    def ensure(self):
        if self.ser is not None:
            return True
        if self.clock() < self.next_try:
            return False
        self.next_try = self.clock() + RECONNECT_S
        port = self.port or self.finder()
        if not port:
            self._warn("背單字：找不到 CH340 / CP210x 序列埠，每 5 秒重試")
            return False
        try:
            self.ser = self.opener(port)
        except Exception as e:
            self._warn(f"背單字：{port} 開不起來：{e}（其他程式佔用？）每 5 秒重試")
            return False
        self._buf = b""
        self._warned = None
        self.log(f"背單字：已連上 {port}")
        return True

    def _warn(self, msg):
        if self._warned != msg:  # 同一種失敗只記一次
            self.log(msg)
            self._warned = msg

    def write(self, line):
        if self.ser is None:
            return False
        try:
            self.ser.write((line + "\n").encode("utf-8"))
            return True
        except Exception as e:
            self.log(f"背單字：送出失敗（可能被拔掉）：{e}")
            self.drop()
            return False

    def read_lines(self):
        if self.ser is None:
            return []
        try:
            data = self.ser.read(4096)
        except Exception as e:
            self.log(f"背單字：讀取失敗（可能被拔掉）：{e}")
            self.drop()
            return []
        if not data:
            return []
        self._buf += data
        *lines, self._buf = self._buf.split(b"\n")
        return [
            ln.decode("utf-8", "replace").rstrip("\r") for ln in lines if ln.strip()
        ]

    def drop(self, retry_now=False):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None
        self.next_try = 0.0 if retry_now else self.clock() + RECONNECT_S

    def close(self):
        self.drop()


# ---------------------------------------------------------------- 控制器


@dataclasses.dataclass
class _Shown:
    card: object
    answered: bool = False
    counted: bool = False  # 送上時有記曝光（右轉跳過要收回）


class VocabController:
    """把按鍵、板子回報、計時接到 Vocab。所有方法都在同一個執行緒（tk 主迴圈）呼叫。"""

    def __init__(
        self, vocab, link, clock=time.monotonic, knob_reverse=False, log=print
    ):
        self.v = vocab
        self.link = link
        self.clock = clock
        self.knob_reverse = knob_reverse
        self.log = log
        self.cur = None  # _Shown，板子上這張
        self.side = "front"
        self.back_seen = False
        self.advance_at = None  # 作答後自動換卡的時間
        self.hold_s = GOOD_HOLD_S  # 這次作答後要停幾秒
        self.due_deadline = None  # 到期卡 60 秒
        self.due_reveal_at = None  # 到期卡被忽略、已翻面：這時換下一張
        self.empty_retry_at = None
        self.user_paused = False
        self.locked = False
        self.hist = []  # 之前的卡（_Shown）
        self.fwd = []  # 回看時，原本後面的卡
        self.last_tick = None

    # ---------- 送出 ----------
    def _send(self, line):
        return self.link.write(line)

    def _toast(self, text, kind):
        self._send(f"toast\t{_clean(text)}\t{kind}")

    def _send_status(self):
        left, right = self.v.status()  # 到期卡的「複習」色塊由韌體依 kind 畫
        self._send(f"status\t{_clean(left)}\t{_clean(right)}")

    def _show(self, shown, new=False):
        self.cur = shown
        self.side = "front"
        self.back_seen = False
        self.advance_at = None
        self.due_reveal_at = None
        self.empty_retry_at = None
        c = shown.card
        self.due_deadline = self.clock() + DUE_TIMEOUT_S if c.kind == "due" else None
        if new:
            line = card_line(c)
        else:
            line = card_line(c, *self.v.status())
        self._send(line)
        if new and c.kind == "learn":
            self.v.exposed(c.id)
            shown.counted = True

    def _push_hist(self):
        if self.cur is not None:
            self.hist.append(self.cur)
            del self.hist[:-HISTORY]

    def next(self):
        """下一張：回看中就往回走，否則向 Vocab 要新卡。"""
        if self.locked:
            return
        if self.fwd:
            self._push_hist()
            self._show(self.fwd.pop())
            return
        card = self.v.next_card()
        self._push_hist()
        if card is None:
            self.cur = None
            self.advance_at = self.due_deadline = self.due_reveal_at = None
            self.empty_retry_at = self.clock() + EMPTY_RETRY_S
            self._send_status()
            self._toast("今天的字都完成了", "info")
            return
        self._show(_Shown(card), new=True)

    def prev(self):
        if not self.hist:
            self._toast("沒有上一張", "info")
            return
        if self.cur is not None:
            self.fwd.append(self.cur)
        self._show(self.hist.pop())

    def resync(self):
        """連線剛接上、板子重開機、解鎖後：把板子拉回目前狀態。"""
        if self.locked:
            self._send("pause")
            return
        self._send("pause" if self.user_paused else "resume")
        if self.cur is None or self.cur.answered:
            self.next()
        else:
            self._show(self.cur)

    # ---------- 輸入 ----------
    def on_key(self, key):
        """熱鍵進來。是背單字的鍵就處理並回傳 True；其他鍵回傳 False 交給原本的 actions。"""
        role = key_role(key, self.knob_reverse)
        if role is None:
            return False
        if self.locked:
            return True
        self._touch()
        if role == ROLE_FLIP:
            self.flip()
        elif role in (ROLE_GOOD, ROLE_BAD):
            self.answer(role == ROLE_GOOD)
        elif role == ROLE_NEXT:
            self.skip()
        elif role == ROLE_PREV:
            self.prev()
        return True

    def skip(self):
        """旋鈕右轉：跳過剛送上、還沒作答的新卡不算曝光。"""
        s = self.cur
        if s is not None and s.counted and not s.answered:
            self.v.exposed(s.card.id, -1)
            s.counted = False
        self.next()

    def _touch(self):
        """任何按鍵都重算到期卡的 60 秒（已經翻面等著換卡的也取消）。"""
        if self.cur and self.cur.card.kind == "due" and not self.cur.answered:
            self.due_deadline = self.clock() + DUE_TIMEOUT_S
            self.due_reveal_at = None

    def flip(self):
        if self.cur is None:
            return
        self._send("flip")
        if self.side == "back":
            self.side = "front"  # 翻到背面時板子會回 back，這邊只記翻回正面

    def answer(self, good):
        s = self.cur
        if s is None or self.advance_at is not None:
            return
        if s.answered:
            self._toast("已作答", "info")
            return
        rating = self.v.answer(s.card.id, good, self.back_seen)
        if rating is None:
            return
        s.answered = True
        self.due_deadline = self.due_reveal_at = None
        self._toast("會" if good else "不會", "ok" if good else "bad")
        self._send_status()
        if not good and self.side == "front":
            self._send("flip")  # 不會：給答案看 BAD_HOLD_S 秒再換
        self.hold_s = GOOD_HOLD_S if good else BAD_HOLD_S
        self.advance_at = self.clock() + self.hold_s
        self.log(f"背單字：{s.card.id} → {rating}")

    def toggle_pause(self):
        self.user_paused = not self.user_paused
        self._send("pause" if self.user_paused else "resume")
        if not self.user_paused:
            now = self.clock()
            if self.due_reveal_at is not None:
                self.due_reveal_at = now + DUE_REVEAL_S
            elif self.cur and self.cur.card.kind == "due" and not self.cur.answered:
                self.due_deadline = now + DUE_TIMEOUT_S
            if self.advance_at is not None:
                self.advance_at = now + self.hold_s
        self.log(f"背單字：{'暫停' if self.user_paused else '繼續'}")

    def on_line(self, line):
        f = line.split("\t")
        cmd = f[0].strip()
        if cmd == "hello":
            self.log(f"背單字：板子開機 {' '.join(f[1:])}")
            self.resync()
        elif cmd == "back" and len(f) > 1:
            if self.cur and f[1] == self.cur.card.id:
                self.side = "back"
                self.back_seen = True
                self.v.back_shown(
                    f[1]
                )  # 不重算 60 秒：按鍵／觸控已經算過，PC 自己翻的不算
        elif cmd == "done" and len(f) > 1:
            if (
                self.cur
                and f[1] == self.cur.card.id
                and not self.cur.answered
                and not self.locked
            ):
                self.next()
        elif cmd == "tap":
            if not self.locked:
                self._touch()
                self.flip()
        elif cmd == "hold":
            if not self.locked:
                self.toggle_pause()
        elif cmd == "err":
            self.log(f"背單字：板子回報錯誤 {' '.join(f[1:])}")

    # ---------- 計時與系統事件 ----------
    def tick(self):
        """主迴圈定時呼叫：重連、讀板子、計時。"""
        if not self.link.connected:
            if self.link.ensure():
                self.resync()
        for line in self.link.read_lines():
            self.on_line(line)
        now = self.clock()
        last, self.last_tick = self.last_tick, now
        if self.locked:
            return
        if last is not None and not self.user_paused and self.link.connected:
            self.v.add_active(min(max(0.0, now - last), ACTIVE_STEP_MAX))
        if (
            self.advance_at is not None
            and not self.user_paused
            and now >= self.advance_at
        ):
            self.advance_at = None
            self.next()
        elif (
            self.cur
            and self.cur.card.kind == "due"
            and not self.cur.answered
            and not self.user_paused
            and self.due_deadline is not None
            and now >= self.due_deadline
        ):
            self.log(f"背單字：{self.cur.card.id} 60 秒沒按鍵，翻面後跳過")
            self.due_deadline = None
            self.due_reveal_at = now + DUE_REVEAL_S
            if self.side == "front":
                self._send("flip")
        elif (
            self.cur
            and not self.cur.answered
            and not self.user_paused
            and self.due_reveal_at is not None
            and now >= self.due_reveal_at
        ):
            self.due_reveal_at = None
            self.v.skip_due(self.cur.card.id)
            self.next()
        elif (
            self.cur is None
            and self.empty_retry_at is not None
            and now >= self.empty_retry_at
        ):
            self.next()

    def start(self):
        if self.link.ensure():
            self.resync()

    def lock(self):
        if self.locked:
            return
        self.locked = True
        self.advance_at = None
        self._send("pause")
        self.log("背單字：電腦鎖定，暫停")

    def unlock(self):
        if not self.locked:
            return
        self.locked = False
        self.log("背單字：解鎖，重連序列埠")
        self.link.drop(retry_now=True)
        if self.link.ensure():
            self.resync()  # 連不上的話 tick() 每 5 秒重試，接上時再 resync

    def close(self):
        self.link.close()


# ---------------------------------------------------------------- Windows 鎖定通知

WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_LOCK, WTS_SESSION_UNLOCK = 0x7, 0x8
NOTIFY_FOR_THIS_SESSION = 0
GWLP_WNDPROC = -4


def session_event(msg, wparam):
    """視窗訊息 → "lock" / "unlock" / None（純函式，方便測試）。"""
    if msg != WM_WTSSESSION_CHANGE:
        return None
    return {WTS_SESSION_LOCK: "lock", WTS_SESSION_UNLOCK: "unlock"}.get(wparam)


def watch_session(root, events):
    """在 tk root 的 hwnd 掛 WTSRegisterSessionNotification，鎖定／解鎖放進 events（queue）。

    視窗程序裡只放 queue、不呼叫 tk（避免在 Tcl 處理訊息途中重入）；主迴圈再取出處理。
    回傳 True 表示掛上了。
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    wtsapi = ctypes.WinDLL("wtsapi32", use_last_error=True)
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(
        LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    )
    user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    user32.CallWindowProcW.restype = LRESULT
    user32.CallWindowProcW.argtypes = [
        ctypes.c_void_p,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    wtsapi.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
    wtsapi.WTSRegisterSessionNotification.restype = wintypes.BOOL

    root.update_idletasks()
    hwnd = root.winfo_id()
    old = {"proc": None}

    def proc(h, msg, wp, lp):
        ev = session_event(msg, wp)
        if ev:
            events.put(ev)
        return user32.CallWindowProcW(old["proc"], h, msg, wp, lp)

    cb = WNDPROC(proc)
    root._vocab_wndproc = cb  # 留住參照，不然會被回收
    old["proc"] = user32.SetWindowLongPtrW(
        hwnd, GWLP_WNDPROC, ctypes.cast(cb, ctypes.c_void_p)
    )
    if not old["proc"]:
        return False
    return bool(wtsapi.WTSRegisterSessionNotification(hwnd, NOTIFY_FOR_THIS_SESSION))


def drain_session_events(events, ctl):
    while True:
        try:
            ev = events.get_nowait()
        except queue.Empty:
            return
        if ev == "lock":
            ctl.lock()
        elif ev == "unlock":
            ctl.unlock()
