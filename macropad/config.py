"""讀取按鍵設定：config.toml（共用、會上傳）＋ local_config.toml（這台電腦專用、不上傳）。"""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _merge(base, extra):
    """把 extra 疊到 base 上，字典會往下逐層合併。"""
    out = dict(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(root=ROOT):
    with open(root / "config.toml", "rb") as f:
        cfg = tomllib.load(f)
    local = root / "local_config.toml"
    if local.exists():
        with open(local, "rb") as f:
            cfg = _merge(cfg, tomllib.load(f))
    return Config(cfg)


class Config:
    def __init__(self, raw):
        self.raw = raw
        self.hotkeys = {str(k): v for k, v in raw["hotkeys"].items()}
        self.modes = raw["modes"]
        self._app_to_mode = {}
        for mode_id, mode in self.modes.items():
            for exe in mode.get("apps", []):
                self._app_to_mode[exe.lower()] = mode_id

    def mode_for(self, exe):
        """依前景程式檔名找模式，找不到就用 default。"""
        return self._app_to_mode.get(exe.lower(), "default")

    def action_for(self, mode_id, key):
        """先找該模式的設定，沒有的話退回 default 模式的同一顆鍵。"""
        for m in (mode_id, "default"):
            action = self.modes.get(m, {}).get("keys", {}).get(key)
            if action:
                return m, action
        return mode_id, None
