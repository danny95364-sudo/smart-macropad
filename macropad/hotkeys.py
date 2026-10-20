"""全域熱鍵監聽。

用 Windows 的 RegisterHotKey，不用鍵盤 hook：只收得到註冊過的組合鍵，
不會看到其他按鍵，公司的防毒也比較不會把它當成側錄程式。
"""

import ctypes
import queue
import threading
from ctypes import wintypes

from . import win32

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.RegisterHotKey.argtypes = [
    wintypes.HWND,
    ctypes.c_int,
    wintypes.UINT,
    wintypes.UINT,
]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
]
user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]

MOD = {"alt": 0x0001, "ctrl": 0x0002, "shift": 0x0004, "win": 0x0008}
MOD_NOREPEAT = 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012


class HotkeyListener(threading.Thread):
    """在背景執行緒註冊熱鍵；按下時把 (按鍵編號, 前景程式) 放進 events。"""

    def __init__(self, bindings):
        """bindings: {按鍵編號: 'ctrl+alt+shift+1'}"""
        super().__init__(daemon=True)
        self.bindings = bindings
        self.events = queue.Queue()
        self.ready = threading.Event()
        self.failed = []
        self._tid = None

    def run(self):
        self._tid = kernel32.GetCurrentThreadId()
        ids = {}
        for i, (key, combo) in enumerate(self.bindings.items(), start=1):
            *mods, main = combo.lower().split("+")
            modflags = MOD_NOREPEAT
            for m in mods:
                modflags |= MOD[m]
            if user32.RegisterHotKey(None, i, modflags, win32.VK[main]):
                ids[i] = key
            else:
                self.failed.append(combo)
        self.ready.set()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in ids:
                # 前景程式要在這裡就抓，避免之後彈出的提示視窗搶走前景
                self.events.put((ids[msg.wParam], win32.foreground_exe()))

        for i in ids:
            user32.UnregisterHotKey(None, i)

    def stop(self):
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
