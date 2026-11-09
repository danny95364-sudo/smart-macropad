# CYD 循環 GIF 播放器

## 目標
CYD（ST7789，320×240 橫放）開機後從內建 flash（LittleFS）循環播放 GIF，不需要 SD 卡、不需要電腦連線。點螢幕切換下一張。

## 做法
- 新 env `[env:cyd_gif]`（`firmware/cyd/platformio.ini`），原始碼放 `firmware/cyd/src/gif/`。其他 env 的 `build_src_filter` 要排除 `gif/`，cyd_gif 只編 `gif/`。
- 顯示沿用 `include/lgfx_cyd.hpp`（ST7789 設定 + `CYD_TOUCH` 觸控）。
- 解碼用 bitbank2 `AnimatedGIF` 函式庫，逐行畫到 LovyanGFX。
- GIF 檔放 `firmware/cyd/data/`，用 `pio run -e cyd_gif -t uploadfs` 燒進 LittleFS。
- 分割表要讓 LittleFS 盡量大（例如 app 1.5MB + FS 約 2.4MB 的自訂 csv 或現成 `huge_app` 以外的方案），在計畫實作時確認。
- GIF 置中；小於 320×240 補黑邊。

## 轉檔腳本
`tools/make_gif.bat`（或 .py，用 ffmpeg）：輸入任意 gif/mp4 → 輸出 320×240 以內、15 fps、優化調色盤的 GIF 到 `firmware/cyd/data/`，並印出檔案大小。目標每檔 < 1MB。

## 素材
使用者自己提供 GIF（例如 aespa），不由 Claude 下載。測試用 ffmpeg `testsrc` 產生。

## 燒錄
pio 只能在 PowerShell 跑：`& "$env:USERPROFILE\.local\bin\pio.exe" run -d firmware/cyd -e cyd_gif -t upload`，再 `-t uploadfs`。COM4 若被 game_bridge 佔用要先停掉。
燒回看板：`-e cyd_st7789 -t upload`。
