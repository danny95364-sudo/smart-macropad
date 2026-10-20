"""執行按鍵對應的動作。

設定檔裡每顆鍵寫 do = "動作種類"，目前支援：
    keys    送出組合鍵           keys = "ctrl+shift+c"
    seq     依序送出多組按鍵     seq = ["alt", "h", "g"]
    scroll  滑鼠滾輪             clicks = 3（負數往下）
    run     執行指令             cmd = ["notepad.exe"]
    toast   只顯示訊息           （測試用）
"""

import subprocess

from . import win32


def run_action(action, dry_run=False):
    """執行動作，回傳要顯示給使用者的結果文字。"""
    kind = action.get("do", "toast")
    if dry_run:
        return f"（測試模式，未執行 {kind}）"

    if kind in ("keys", "seq"):
        win32.wait_modifiers_released()
    if kind == "keys":
        win32.send_combo(action["keys"])
    elif kind == "seq":
        win32.send_sequence(action["seq"], action.get("delay", 0.05))
    elif kind == "scroll":
        win32.scroll(int(action.get("clicks", 1)))
    elif kind == "run":
        subprocess.Popen(action["cmd"], creationflags=subprocess.CREATE_NO_WINDOW)
    elif kind == "toast":
        pass
    else:
        raise ValueError(f"不認得的動作種類：{kind}")
    return action.get("result", "")
