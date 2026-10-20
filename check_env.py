"""智慧快捷鍵盤：電腦環境檢測。跑一次，看這台電腦能做到哪一層。

用法：
    python check_env.py            基本檢測（約 1 分鐘，不會用到 AI 額度）
    python check_env.py --ai       另外實際呼叫一次 Cursor CLI 測試 AI
    python check_env.py --skip-install   不測試安裝套件（網路很慢時用）

結果會存成同資料夾的 check_report.txt。
只記錄「程式名稱」，不記錄視窗標題、檔名或剪貼簿內容。
只用 Python 內建模組，不需要先安裝任何東西。
"""

import argparse
import ctypes
import glob
import os
import platform
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import winreg
from ctypes import wintypes
from pathlib import Path

sys.stdout.reconfigure(errors="replace")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.RegisterHotKey.argtypes = [
    wintypes.HWND,
    ctypes.c_int,
    wintypes.UINT,
    wintypes.UINT,
]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

CF_UNICODETEXT = 13
TAGS = {"ok": "[OK]", "warn": "[!!]", "fail": "[XX]", "info": "[--]"}
results = []
flags = {}


def report(level, title, detail=""):
    results.append((level, title, detail))
    line = f"{TAGS[level]} {title}"
    if detail:
        line += "\n       " + detail.replace("\n", "\n       ")
    print(line, flush=True)


def section(name):
    results.append(("section", name, ""))
    print(f"\n== {name} ==", flush=True)


def run(cmd, timeout=30):
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return None, "找不到指令"
    except subprocess.TimeoutExpired:
        return None, f"超過 {timeout} 秒沒有回應"


def check_python():
    section("Python")
    v = sys.version_info
    level = "ok" if v >= (3, 9) else "warn"
    report(
        level,
        f"Python {v.major}.{v.minor}.{v.micro}（{platform.architecture()[0]}）",
        "" if level == "ok" else "建議 3.9 以上",
    )
    admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    report(
        "info",
        f"系統管理員權限：{'有' if admin else '沒有'}",
        "沒有也沒關係，常駐程式不需要管理員權限",
    )
    uv = shutil.which("uv")
    report("info", f"uv：{'有' if uv else '沒有'}")


def reachable(url, timeout=8):
    """回傳 (能否連到, 說明)。伺服器回 4xx/5xx 也算連得到。"""
    try:
        urllib.request.urlopen(
            urllib.request.Request(url, method="HEAD"), timeout=timeout
        )
        return True, ""
    except urllib.error.HTTPError:
        return True, ""
    except urllib.error.URLError as e:
        if isinstance(e.reason, ssl.SSLError):
            return False, "SSL 憑證錯誤（公司可能有 SSL 攔截）"
        return False, str(e.reason)
    except Exception as e:
        return False, str(e)


def check_network():
    section("網路")
    proxies = urllib.request.getproxies()
    report("info", f"Proxy：{', '.join(proxies) if proxies else '沒有設定'}")
    targets = [
        ("pypi.org", "https://pypi.org/simple/", "裝 Python 套件"),
        ("files.pythonhosted.org", "https://files.pythonhosted.org/", "下載套件檔"),
        ("patents.google.com", "https://patents.google.com/", "查專利"),
        ("api2.cursor.sh", "https://api2.cursor.sh/", "Cursor AI"),
        ("api.anthropic.com", "https://api.anthropic.com/", "Claude API 備案"),
    ]
    for name, url, use in targets:
        ok, why = reachable(url)
        flags[f"net_{name}"] = ok
        report(
            "ok" if ok else "warn",
            f"{name}（{use}）{'連得到' if ok else '連不到'}",
            why,
        )


def check_pip(skip_install):
    section("安裝套件")
    code, out = run([sys.executable, "-m", "pip", "--version"])
    if code != 0:
        report("fail", "pip 不能用", out[:200])
        flags["pip"] = False
        return
    report("ok", "pip 可以執行")
    if skip_install:
        report("info", "略過實際安裝測試（--skip-install）")
        return
    tmp = Path(tempfile.mkdtemp(prefix="kbcheck_"))
    try:
        print("       建立暫存環境並試裝一個小套件，約 30 秒…", flush=True)
        code, out = run([sys.executable, "-m", "venv", str(tmp / "venv")], timeout=120)
        if code != 0:
            report("fail", "無法建立虛擬環境（venv）", out[:200])
            flags["pip"] = False
            return
        py = tmp / "venv" / "Scripts" / "python.exe"
        code, out = run(
            [
                str(py),
                "-m",
                "pip",
                "install",
                "-q",
                "--disable-pip-version-check",
                "pyperclip",
            ],
            timeout=180,
        )
        flags["pip"] = code == 0
        if code == 0:
            report("ok", "可以從網路安裝套件")
        else:
            hint = (
                "可能需要設定 proxy 或公司憑證"
                if "SSL" in out or "proxy" in out.lower()
                else ""
            )
            report("fail", "無法安裝套件", (hint + "\n" + out[-300:]).strip())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def clipboard_get():
    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False, None
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return True, None
        p = kernel32.GlobalLock(h)
        text = ctypes.wstring_at(p)
        kernel32.GlobalUnlock(h)
        return True, text
    finally:
        user32.CloseClipboard()


def clipboard_set(text):
    data = (text + "\0").encode("utf-16-le")
    h = kernel32.GlobalAlloc(0x0002, len(data))
    p = kernel32.GlobalLock(h)
    ctypes.memmove(p, data, len(data))
    kernel32.GlobalUnlock(h)
    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False
    try:
        user32.EmptyClipboard()
        return bool(user32.SetClipboardData(CF_UNICODETEXT, h))
    finally:
        user32.CloseClipboard()


def foreground_process():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
    finally:
        kernel32.CloseHandle(h)
    return None


def check_keyboard_core():
    section("快捷鍵核心（第 1 層）")
    VK_F13, VK_F24 = 0x7C, 0x87
    ok_keys = []
    for i, vk in enumerate(range(VK_F13, VK_F24 + 1)):
        if user32.RegisterHotKey(None, 9000 + i, 0, vk):
            ok_keys.append(vk)
            user32.UnregisterHotKey(None, 9000 + i)
    flags["hotkey"] = len(ok_keys) >= 6
    report(
        "ok" if flags["hotkey"] else "fail",
        f"全域熱鍵：F13–F24 中有 {len(ok_keys)}/12 個可以註冊",
        "" if flags["hotkey"] else "可能被公司軟體佔用或封鎖",
    )

    name = foreground_process()
    flags["foreground"] = bool(name)
    report(
        "ok" if name else "fail",
        f"偵測目前視窗的程式：{name or '失敗'}",
        "這就是「依軟體切換按鍵」用的功能",
    )

    if (
        not user32.IsClipboardFormatAvailable(CF_UNICODETEXT)
        and user32.CountClipboardFormats() > 0
    ):
        report(
            "info",
            "剪貼簿目前是圖片或檔案，為了不覆蓋它，略過剪貼簿測試",
            "想測的話先複製一段文字再重跑",
        )
        flags["clipboard"] = None
        return
    opened, original = clipboard_get()
    if not opened:
        report("fail", "無法開啟剪貼簿")
        flags["clipboard"] = False
        return
    probe = "kb-check-測試-" + str(time.time())
    wrote = clipboard_set(probe)
    _, back = clipboard_get()
    flags["clipboard"] = wrote and back == probe
    if original is not None:
        clipboard_set(original)
    report(
        "ok" if flags["clipboard"] else "fail",
        "剪貼簿讀寫" + ("正常（已還原你原本的內容）" if flags["clipboard"] else "失敗"),
        "" if flags["clipboard"] else "公司可能有限制剪貼簿，AI 功能會受影響",
    )


def running_processes():
    code, out = run(["tasklist", "/fo", "csv", "/nh"])
    names = set()
    for line in out.splitlines():
        if line.startswith('"'):
            names.add(line.split('","')[0].strip('"').lower())
    return names


def app_path_exists(exe):
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            winreg.CloseKey(
                winreg.OpenKey(
                    root, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
                )
            )
            return True
        except OSError:
            pass
    return False


def check_apps():
    section("常用軟體")
    procs = running_processes()
    apps = [
        ("PowerPoint", ["powerpnt.exe"], "POWERPNT.EXE"),
        ("Excel", ["excel.exe"], "EXCEL.EXE"),
        ("Creo Parametric", ["xtop.exe", "parametric.exe"], None),
        ("新版 Outlook", ["olk.exe"], None),
        ("舊版 Outlook", ["outlook.exe"], "OUTLOOK.EXE"),
        ("Cursor", ["cursor.exe"], None),
        ("Teams", ["ms-teams.exe", "teams.exe"], None),
    ]
    for label, exes, app_path in apps:
        running = any(e in procs for e in exes)
        installed = running or (app_path and app_path_exists(app_path))
        state = "執行中" if running else ("已安裝" if installed else "沒偵測到")
        report("info", f"{label}：{state}")
    flags["outlook_new"] = "olk.exe" in procs
    flags["outlook_classic"] = "outlook.exe" in procs


def writable(folder):
    try:
        fd, p = tempfile.mkstemp(dir=folder, prefix="kbcheck_")
        os.close(fd)
        os.remove(p)
        return True
    except OSError:
        return False


def count_mapkeys(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return sum(1 for line in f if line.lower().startswith("mapkey "))
    except OSError:
        return None


def check_creo():
    section("Creo Mapkey")
    roots = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        r"C:\PTC",
        r"D:\PTC",
        r"D:\Program Files",
    ]
    found = set()
    for r in roots:
        for pat in (
            r"PTC\Creo *\Common Files\text\config.pro",
            r"PTC\Creo *\Common Files\*\text\config.pro",
            r"Creo *\Common Files\text\config.pro",
        ):
            found.update(glob.glob(os.path.join(r, pat)))
    code, out = run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "(Get-Process xtop -ErrorAction SilentlyContinue | Select-Object -First 1).Path",
        ],
        timeout=20,
    )
    flags["creo_found"] = bool(found) or (code == 0 and bool(out))
    if code == 0 and out:
        report("info", "偵測到 Creo 正在執行")
    if found:
        for p in sorted(found):
            n = count_mapkeys(p)
            can = writable(os.path.dirname(p))
            report(
                "info",
                f"系統 config.pro：{p}",
                f"裡面有 {n} 個 mapkey；{'可以修改' if can else '沒有權限修改（正常，用個人設定檔即可）'}",
            )
    else:
        report(
            "info",
            "沒在常見位置找到 Creo 安裝",
            "Creo 可能裝在別的地方，或這台電腦沒有 Creo",
        )

    home = Path(os.environ.get("HOME") or os.environ.get("USERPROFILE", ""))
    personal = [home / "config.pro", home / "Documents" / "config.pro"]
    existing = [p for p in personal if p.exists()]
    for p in existing:
        report("info", f"個人 config.pro：{p}", f"裡面有 {count_mapkeys(p)} 個 mapkey")
    flags["creo_writable"] = writable(home)
    report(
        "ok" if flags["creo_writable"] else "warn",
        f"個人資料夾{'可以' if flags['creo_writable'] else '不能'}寫入（{home}）",
        "Creo 啟動時會讀這裡的 config.pro，mapkey 可以放這裡"
        if flags["creo_writable"]
        else "改放 Creo 的啟動工作目錄試試",
    )


def check_ai(do_call):
    section("AI（Cursor CLI）")
    agent = shutil.which("cursor-agent") or shutil.which("agent")
    editor = shutil.which("cursor")
    if editor:
        code, out = run([editor, "--version"], timeout=20)
        report(
            "info",
            f"Cursor 編輯器指令：有（{out.splitlines()[0] if out else '版本不明'}）",
        )
    else:
        report("info", "Cursor 編輯器指令：沒有加到 PATH")
    wsl = shutil.which("wsl")
    report(
        "info", f"WSL：{'有' if wsl else '沒有'}", "部分 Cursor CLI 版本需要透過 WSL"
    )
    if not agent:
        flags["ai"] = False
        report(
            "warn",
            "找不到 Cursor CLI（cursor-agent）",
            "安裝方式見 https://cursor.com/cli ，裝完重跑一次這個檢測",
        )
        return
    code, out = run([agent, "--version"], timeout=20)
    report("ok", f"Cursor CLI：{agent}", out.splitlines()[0] if out else "")
    if not do_call:
        report("info", "沒有實際呼叫 AI（要測的話加上 --ai）")
        flags["ai"] = None
        return
    print("       呼叫 Cursor CLI 中，最多等 90 秒…", flush=True)
    t0 = time.time()
    code, out = run([agent, "-p", "只回覆 OK 兩個英文字母，不要其他內容"], timeout=90)
    dt = time.time() - t0
    flags["ai"] = code == 0 and "OK" in out.upper()
    if flags["ai"]:
        report(
            "ok",
            f"AI 呼叫成功，花了 {dt:.1f} 秒",
            "超過 8 秒的話，翻譯、摘要會有點卡，但還能用",
        )
    else:
        report(
            "fail",
            "AI 呼叫失敗",
            (out or "沒有輸出")[:300]
            + "\n如果是要登入，先執行 cursor-agent login 再重跑",
        )


def check_hardware():
    section("硬體（之後接板子用）")
    ports = []
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM")
        i = 0
        while True:
            try:
                ports.append(winreg.EnumValue(k, i)[1])
                i += 1
            except OSError:
                break
    except OSError:
        pass
    report(
        "info",
        f"序列埠：{', '.join(ports) if ports else '目前沒有'}",
        "插上 ESP32 後重跑，多出來的那個就是板子",
    )
    smi = shutil.which("nvidia-smi")
    if smi:
        code, out = run([smi, "--query-gpu=name", "--format=csv,noheader"], timeout=15)
        report(
            "info",
            f"NVIDIA 顯示卡：{out.splitlines()[0] if code == 0 and out else '有'}",
            "語音轉文字會比較快",
        )
    else:
        report("info", "沒有 NVIDIA 顯示卡", "語音轉文字可用，但每句要多等幾秒")


def summary():
    section("結論")
    core = (
        flags.get("hotkey")
        and flags.get("foreground")
        and flags.get("clipboard") is not False
    )
    tiers = [
        ("第 1 層：快捷鍵、依軟體切換、PPT 對齊格式", core),
        (
            "第 2 層：Python 計算（單位換算、公差、加總）",
            core and flags.get("pip") is not False,
        ),
        ("第 3 層：Creo mapkey", flags.get("creo_writable")),
        ("第 4 層：AI 翻譯、摘要、專利白話", flags.get("ai")),
    ]
    for name, ok in tiers:
        if name.startswith("第 3 層") and not flags.get("creo_found"):
            report("info", f"{name}：這台電腦沒偵測到 Creo（有的話先開著 Creo 再重跑）")
        elif ok is None:
            report("info", f"{name}：還沒測（加 --ai 重跑）")
        else:
            report(
                "ok" if ok else "warn", f"{name}：{'可以' if ok else '有問題，見上方'}"
            )
    if flags.get("outlook_new") and not flags.get("outlook_classic"):
        report("info", "Outlook 是新版：信件功能會用「選取文字 → 複製 → 處理」的方式")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    ap.add_argument("--ai", action="store_true", help="實際呼叫一次 Cursor CLI")
    ap.add_argument("--skip-install", action="store_true", help="不測試安裝套件")
    ap.add_argument("--no-pause", action="store_true", help="結束時不等待按 Enter")
    args = ap.parse_args()

    print("智慧快捷鍵盤：環境檢測")
    print(f"Windows {platform.release()}（{platform.version()}）")
    for step in (
        check_python,
        check_network,
        lambda: check_pip(args.skip_install),
        check_keyboard_core,
        check_apps,
        check_creo,
        lambda: check_ai(args.ai),
        check_hardware,
        summary,
    ):
        try:
            step()
        except Exception as e:
            report("fail", f"檢測過程出錯：{type(e).__name__}", str(e)[:300])

    out = Path(__file__).with_name("check_report.txt")
    lines = [f"Windows {platform.release()} / Python {platform.python_version()}"]
    for level, title, detail in results:
        if level == "section":
            lines.append(f"\n== {title} ==")
        else:
            lines.append(
                f"{TAGS[level]} {title}" + (f"\n       {detail}" if detail else "")
            )
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n報告已存到：{out}")
    print("把這個檔案的內容貼給 Claude 就可以了。")
    if not args.no_pause:
        input("\n按 Enter 結束")


if __name__ == "__main__":
    main()
