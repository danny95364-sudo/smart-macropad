"""右下角的小提示視窗。不會搶走焦點，所以不會干擾正在用的軟體。"""

import ctypes
import tkinter as tk

user32 = ctypes.WinDLL("user32")
GWL_EXSTYLE = -20
WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x8, 0x80, 0x08000000


class Toast:
    def __init__(self, root, duration_ms=1400):
        self.root = root
        self.duration_ms = duration_ms
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.92)
        frame = tk.Frame(self.win, bg="#222222", padx=14, pady=10)
        frame.pack()
        self.title = tk.Label(
            frame, bg="#222222", fg="#9ad0ff", font=("Microsoft JhengHei UI", 10)
        )
        self.title.pack(anchor="w")
        self.body = tk.Label(
            frame,
            bg="#222222",
            fg="#ffffff",
            font=("Microsoft JhengHei UI", 13, "bold"),
        )
        self.body.pack(anchor="w")
        self.detail = tk.Label(
            frame, bg="#222222", fg="#bbbbbb", font=("Microsoft JhengHei UI", 10)
        )
        self.detail.pack(anchor="w")
        self._job = None
        self._styled = False

    def _no_activate(self):
        hwnd = user32.GetParent(self.win.winfo_id())
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(
            hwnd,
            GWL_EXSTYLE,
            style | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
        )

    def show(self, title, body, detail=""):
        self.title.config(text=title)
        self.body.config(text=body)
        self.detail.config(text=detail)
        if detail:
            self.detail.pack(anchor="w")
        else:
            self.detail.pack_forget()
        self.win.update_idletasks()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry(f"+{sw - w - 24}+{sh - h - 72}")
        self.win.deiconify()
        if not self._styled:
            self._no_activate()
            self._styled = True
        if self._job:
            self.root.after_cancel(self._job)
        self._job = self.root.after(self.duration_ms, self.win.withdraw)
