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
from .display import HEARTBEAT_MS, make_display, press_message, state_message
from .hotkeys import HotkeyListener
from .toast import Toast
from .win32 import foreground_exe

QUIT_KEY = "quit"
WATCH_MS = 300  # 多久看一次前景程式，換模式時更新看板


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

    # 看板（模擬視窗或 CYD）：任何錯誤都不能讓常駐程式停掉
    display = make_display(cfg)
    try:
        display.start()
    except Exception as e:
        print(f"看板啟動失敗：{e}")
    shown = {"mode": None}

    def show_mode(mode_id, force=False):
        if force or mode_id != shown["mode"]:
            shown["mode"] = mode_id
            display.send(state_message(cfg, mode_id))

    def shutdown(*_):
        print("結束")
        listener.stop()
        display.close()
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
        show_mode(mode_id)
        if not action:
            print(f"[{mode_name}] 鍵 {key}：沒有設定（{exe}）")
            toast.show(mode_name, f"鍵 {key} 沒有設定")
            display.send(press_message(key, "", "沒有設定"))
            return
        label = action.get("label", f"鍵 {key}")
        try:
            result = run_action(action, dry_run=args.dry_run)
        except Exception as e:
            print(f"[{mode_name}] 鍵 {key} {label} 失敗：{e}")
            toast.show(mode_name, f"{label} 失敗", str(e)[:60])
            display.send(press_message(key, label, str(e)[:60], error=True))
            return
        display.send(press_message(key, label, result))
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

    def watch_foreground():
        try:
            exe = foreground_exe()
            if exe:
                show_mode(cfg.mode_for(exe))
        except Exception as e:
            print(f"看板更新失敗：{e}")
        root.after(WATCH_MS, watch_foreground)

    def heartbeat():
        # 定時重送目前模式：看板晚開或漏收也會跟上，太久沒收到就知道電腦離線
        if shown["mode"]:
            show_mode(shown["mode"], force=True)
        root.after(HEARTBEAT_MS, heartbeat)

    root.after(30, poll)
    root.after(200, watch_foreground)
    root.after(1000, heartbeat)  # 第一次早一點送，模擬視窗剛開好就有畫面
    try:
        root.mainloop()
    finally:
        display.close()  # 不管怎麼結束，模擬視窗都一起關


if __name__ == "__main__":
    main()
