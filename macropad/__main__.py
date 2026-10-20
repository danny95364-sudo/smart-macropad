"""常駐程式入口。

    python -m macropad            正常執行
    python -m macropad --dry-run  只顯示「會做什麼」，不真的送出按鍵（測試用）

結束：按 Ctrl+Alt+Shift+0，或在視窗按 Ctrl+C。
"""

import argparse
import queue
import signal
import sys
import tkinter as tk

from . import config as config_mod
from .actions import run_action
from .hotkeys import HotkeyListener
from .toast import Toast

QUIT_KEY = "quit"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="不真的執行動作")
    args = ap.parse_args()
    if sys.stdout is None:
        # 用 pythonw 背景執行時沒有主控台，把訊息和錯誤寫進 log 檔
        log = open(config_mod.ROOT / "macropad.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
    else:
        sys.stdout.reconfigure(errors="replace")

    cfg = config_mod.load()
    bindings = dict(cfg.hotkeys)
    bindings[QUIT_KEY] = cfg.raw.get("quit_hotkey", "ctrl+alt+shift+0")

    listener = HotkeyListener(bindings)
    listener.start()
    listener.ready.wait(3)
    if listener.failed:
        print(
            f"這些熱鍵註冊失敗（可能程式已經在執行，或被其他軟體佔用）：{listener.failed}"
        )
        if len(listener.failed) == len(bindings):
            sys.exit(1)

    root = tk.Tk()
    root.withdraw()
    toast = Toast(root)
    print(
        f"智慧快捷鍵盤已啟動{'（測試模式）' if args.dry_run else ''}，按 Ctrl+Alt+Shift+0 結束"
    )
    toast.show("智慧快捷鍵盤", "已啟動", "按 Ctrl+Alt+Shift+0 結束")

    def shutdown(*_):
        print("結束")
        listener.stop()
        toast.show("智慧快捷鍵盤", "已關閉")
        root.after(1200, root.quit)

    # tkinter 的 mainloop 收不到 Ctrl+C，改用 signal 處理
    signal.signal(signal.SIGINT, shutdown)

    def handle(key, exe):
        if key == QUIT_KEY:
            shutdown()
            return
        mode_id = cfg.mode_for(exe)
        used_mode, action = cfg.action_for(mode_id, key)
        mode_name = cfg.modes.get(mode_id, {}).get("name", mode_id)
        if not action:
            print(f"[{mode_name}] 鍵 {key}：沒有設定（{exe}）")
            toast.show(mode_name, f"鍵 {key} 沒有設定")
            return
        label = action.get("label", f"鍵 {key}")
        try:
            result = run_action(action, dry_run=args.dry_run)
        except Exception as e:
            print(f"[{mode_name}] 鍵 {key} {label} 失敗：{e}")
            toast.show(mode_name, f"{label} 失敗", str(e)[:60])
            return
        note = "" if used_mode == mode_id else "（共用）"
        print(f"[{mode_name}] 鍵 {key} {label}{note}（{exe}）{result}")
        if action.get("toast", True):
            toast.show(mode_name, label + note, result)

    def poll():
        try:
            while True:
                handle(*listener.events.get_nowait())
        except queue.Empty:
            pass
        root.after(30, poll)

    root.after(30, poll)
    root.mainloop()


if __name__ == "__main__":
    main()
