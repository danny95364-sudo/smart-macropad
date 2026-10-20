# smart-macropad

依「目前正在用的軟體」自動切換功能的快捷鍵盤常駐程式（Windows）。

實體小鍵盤每顆鍵送出 `Ctrl+Alt+Shift+1`～`9`，這個程式依前景軟體（PowerPoint、Creo、其他）
決定要做什麼，並在右下角顯示提示。

## 需求

- Windows 10/11
- Python 3.11 以上，不需要安裝任何套件

## 執行

```
python -m macropad
```

- 測試模式（只顯示會做什麼，不真的按鍵）：`python -m macropad --dry-run`
- 結束：`Ctrl+Alt+Shift+0`

沒有實體鍵盤時，可以直接在一般鍵盤按 `Ctrl+Alt+Shift+數字` 測試。

## 設定

- `config.toml`：共用的按鍵設定
- `local_config.toml`：這台電腦專用的設定（不上傳），格式見 `local_config.example.toml`

## 環境檢測

```
python check_env.py
```

檢查熱鍵、剪貼簿、網路、Creo、Cursor CLI 能不能用，結果存成 `check_report.txt`。

開發計畫見 [PLAN.md](PLAN.md)。
