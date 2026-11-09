# CYD 小遊戲：六鎖保險箱

## 目標
SIKAI 6 鍵＋旋鈕當手把、ESP32 CYD 當畫面，玩一個原創解謎遊戲。完成標準：能在 CYD 上玩一局，鍵盤和旋鈕操作有反應，可重開新局；玩完能燒回看板韌體。

## 架構
- 鍵盤 → 電腦（RegisterHotKey 收 Ctrl+Alt+Shift+1～9）→ COM4 序列埠 `{"k":N}\n` → CYD。
- 遊戲邏輯全在 CYD 上；電腦端只轉送按鍵，不管遊戲狀態。
- 鍵盤只有「按下」事件，沒有長按/放開，玩法不能依賴長按。
- 測試方便：韌體也接受單一字元 `1`～`9` 加換行（可用 pio monitor 或 Python 直接送）。

## 玩法
- 保險箱有 6 個鎖芯，每個藏一個 0～9 的數字（esp_random 產生）。
- 鍵 1～6：選鎖芯（游標移過去）。
- 鍵 7 / 8（旋鈕左/右轉）：目前鎖芯數字 -1 / +1，0～9 循環。
- 鍵 9（按旋鈕）：試開。每個鎖芯依距離上色：
  - 綠：對了 → 鎖芯鎖定（之後不能改，畫面有鎖定記號），游標自動跳到下一個未鎖定的
  - 黃：差 1～2
  - 紅：差 3 以上
- 第 1 關：有 ↑↓ 箭頭提示要往大還是往小；8 次機會。
- 第 2 關：沒有箭頭，只有顏色；8 次機會。
- 第 3 關起：沒有箭頭，黃色範圍放寬為差 1～3；機會每關少 1，最少 5 次。
- 全部綠 → 過關畫面（顯示用了幾次、累計分數 = 各關剩餘次數加總），按旋鈕進下一關。
- 機會用完 → 失敗畫面，揭曉密碼、顯示到第幾關與分數，按旋鈕重開新局（從第 1 關）。
- 開機標題畫面：遊戲名＋操作說明，按任何鍵開始。
- 畫面上方：關卡、剩餘次數；中間：6 個大數字鎖芯；下方：歷史試開紀錄（最近幾次，每格小色塊）。

## 檔案
- `firmware/cyd/src/game/main.cpp`：遊戲韌體（新增）。
- `firmware/cyd/platformio.ini`：新增 env `cyd_game`（ST7789 那組 build flags），用 `build_src_filter` 讓遊戲 env 只編 `src/game/`，既有兩個看板 env 排除 `src/game/`。看板韌體行為不能變。
- `macropad/game_bridge.py`：轉送程式，`uv run --python 3.12 python -m macropad.game_bridge` 執行。重用 `hotkeys.HotkeyListener`、`display.find_serial_port` 與 serial 開埠方式（DTR/RTS 不拉）。熱鍵組合從 config.toml `[hotkeys]` 讀；旋鈕方向若 config 有反轉設定要一致。Ctrl+C 結束。
- `遊戲.bat`（可選）：一鍵啟動轉送程式。

## 決策
- 字型：遊戲 env 不用全套 efontTW。數字用 LovyanGFX 內建大字型（如 Font7 七段或 FreeSansBold），中文只用一個 efontTW 尺寸；編完看 flash 用量，超過就把中文改英文。
- 繪圖沿用 `include/lgfx_cyd.hpp`、整張 320×240 sprite 雙緩衝。
- 觸控不用。

## 操作流程（燒錄）
pio 要在 PowerShell 跑：`& "$env:USERPROFILE\.local\bin\pio.exe" run -d firmware/cyd -e cyd_game -t upload`
1. 停掉常駐程式 `python -m macropad`（佔 COM4、也攔熱鍵）
2. 燒 `cyd_game` → 跑 `game_bridge` → 玩
3. 玩完：停 bridge → 燒回 `cyd_st7789` → 重開常駐程式
