"""常駐程式入口。

    python -m macropad            正常執行
    python -m macropad --dry-run  只顯示「會做什麼」，不真的送出按鍵（測試用）
    python -m macropad --vocab    背單字開啟（蓋過 config 的 [vocab] enabled）；--no-vocab 關閉

背單字開啟時 CYD 給背單字用（macropad/vocab_app.py），看板 display 不啟動；
鍵 1、2、旋鈕轉給背單字，3～6 照原本的動作。

結束：按 Ctrl+Alt+Shift+0，或在視窗按 Ctrl+C。
"""

import argparse
import os
import queue
import signal
import sys
import tkinter as tk
from pathlib import Path

from . import config as config_mod
from .actions import run_action
from .display import (
    HEARTBEAT_MS,
    Display,
    make_display,
    press_message,
    state_message,
)
from .hotkeys import HotkeyListener
from .toast import Toast
from .vocab_app import drain_session_events
from .win32 import foreground_exe

QUIT_KEY = "quit"
WATCH_MS = 300  # 多久看一次前景程式，換模式時更新看板
VOCAB_TICK_MS = 100  # 背單字讀序列埠、計時


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="不真的執行動作")
    ap.add_argument(
        "--vocab",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="背單字開／關（預設讀 config [vocab] enabled）",
    )
    ap.add_argument("--vocab-dir", default="", help="背單字進度資料夾（預設 vocab/）")
    args = ap.parse_args()
    if sys.stdout is None:
        # 用 pythonw 背景執行時沒有主控台，把訊息和錯誤寫進 log 檔
        log = open(config_mod.ROOT / "macropad.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
    else:
        sys.stdout.reconfigure(errors="replace")

    cfg = config_mod.load()
    vocab_cfg = cfg.raw.get("vocab", {}) or {}
    vocab_on = (
        bool(vocab_cfg.get("enabled", False)) if args.vocab is None else args.vocab
    )
    bindings = dict(cfg.hotkeys)
    bindings[QUIT_KEY] = cfg.raw.get("quit_hotkey", "ctrl+alt+shift+0")

    listener = HotkeyListener(bindings)
    listener.start()
    listener.ready.wait(3)
    if listener.failed:
        print(
            f"這些熱鍵註冊失敗（可能程式已經在執行，或被其他軟體佔用）：{listener.failed}"
        )
        if vocab_on or len(listener.failed) == len(bindings):
            # 背單字靠 9 組熱鍵全部到齊，少一組就不能用：直接結束，不是只警告
            print("熱鍵註冊不完整，結束。請先關掉其他 macropad／game_bridge 再啟動。")
            listener.stop()
            sys.exit(1)

    root = tk.Tk()
    root.withdraw()
    toast = Toast(root)
    print(
        f"智慧快捷鍵盤已啟動{'（測試模式）' if args.dry_run else ''}，按 Ctrl+Alt+Shift+0 結束"
    )
    toast.show("智慧快捷鍵盤", "已啟動", "按 Ctrl+Alt+Shift+0 結束")

    # 看板（模擬視窗或 CYD）：任何錯誤都不能讓常駐程式停掉
    # 背單字開啟時 CYD 給背單字，看板不開（同一個 COM 埠只能一方用）
    display = Display() if vocab_on else make_display(cfg)
    vocab_ctl = None
    session_events = queue.Queue()
    if vocab_on:
        try:
            vocab_ctl = start_vocab(
                cfg, vocab_cfg, args.vocab_dir, root, session_events
            )
        except Exception as e:
            print(f"背單字啟動失敗：{e}")
            toast.show("背單字", "啟動失敗", str(e)[:60])
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
        if vocab_ctl:
            vocab_ctl.close()
        toast.show("智慧快捷鍵盤", "已關閉")
        root.after(1200, root.quit)

    # tkinter 的 mainloop 收不到 Ctrl+C，改用 signal 處理
    signal.signal(signal.SIGINT, shutdown)

    def handle(key, exe):
        if key == QUIT_KEY:
            shutdown()
            return
        if vocab_ctl is not None:
            try:
                if vocab_ctl.on_key(key):
                    return
            except Exception as e:
                print(f"背單字 鍵 {key} 失敗：{e}")
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

    def vocab_tick():
        try:
            drain_session_events(session_events, vocab_ctl)
            vocab_ctl.tick()
        except Exception as e:
            print(f"背單字錯誤：{e}")
        root.after(VOCAB_TICK_MS, vocab_tick)

    if vocab_ctl is not None:
        root.after(VOCAB_TICK_MS, vocab_tick)
    root.after(30, poll)
    root.after(200, watch_foreground)
    root.after(1000, heartbeat)  # 第一次早一點送，模擬視窗剛開好就有畫面
    try:
        root.mainloop()
    finally:
        display.close()  # 不管怎麼結束，模擬視窗都一起關
        if vocab_ctl:
            vocab_ctl.close()


def start_vocab(cfg, vocab_cfg, data_dir, root, session_events):
    """建 Vocab＋序列埠＋控制器，掛上鎖定通知，送第一張卡。"""
    from .vocab import DEFAULT_DATA_DIR, Vocab
    from .vocab_app import SerialLink, VocabController, watch_session

    data_dir = data_dir or vocab_cfg.get("data_dir") or DEFAULT_DATA_DIR
    data_dir = Path(os.path.expandvars(str(data_dir)))  # 支援 %OneDrive%
    if "%" in str(data_dir):  # 環境變數不存在（例如這台沒裝 OneDrive）→ 退回專案資料夾
        print(f"背單字：找不到 {data_dir}，改用 {DEFAULT_DATA_DIR}")
        data_dir = DEFAULT_DATA_DIR
    words = Path(data_dir) / "words.csv"
    if not words.exists():
        words = DEFAULT_DATA_DIR / "words.csv"  # 進度放別處時，字表仍用專案裡的
    v = Vocab(data_dir=data_dir, words_path=words)
    for w in v.warnings:
        print(f"背單字：{w}")
    port = str(cfg.raw.get("display_serial_port", "") or "")
    ctl = VocabController(
        v, SerialLink(port), knob_reverse=bool(cfg.raw.get("knob_reverse", False))
    )
    if not watch_session(root, session_events):
        print(
            "背單字：鎖定通知掛不上（WTSRegisterSessionNotification 失敗），鎖定時不會自動暫停"
        )
    ctl.start()
    print(f"背單字已開啟（進度：{data_dir}）")
    return ctl


if __name__ == "__main__":
    main()
