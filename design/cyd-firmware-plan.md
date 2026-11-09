# CYD 看板韌體（取代模擬視窗，接 USB 序列埠）

## 目標
ESP32 CYD 2.8 吋（Type-C 版，CH340，COM4）收常駐程式從序列埠送的「一行一個 JSON」，照方向 B 色塊畫出，和 `macropad/display_sim.py` 看起來一樣。

## 已確認
- 2026-11-06 接上，esptool 偵測：ESP32-D0WD-V3、CH340、COM4，燒錄通訊正常。
- 資料格式：`macropad/display.py` 檔頭（t=s/p/o），不改格式。
- 畫面規格：`design/screen-design.html` 方向 B（約 254–360 行）；電腦版實作 `macropad/display_sim.py` 可當畫法參考。

## 決策
- 工具鏈：PlatformIO，用 `uv tool install platformio`（不用 pip、不裝 Arduino IDE）。專案放 `firmware/cyd/`。
- 繪圖：LovyanGFX，中文用內建 `fonts::efontTW_*`（繁中）。用 sprite 雙緩衝避免閃爍。
- 面板驅動：Type-C 版 CYD 常見 ST7789（也有 ILI9341）。用 build flag 切（兩個 env：`cyd_ili9341`、`cyd_st7789`），實機確認哪個正確。SPI 腳位：MOSI 13、SCLK 14、CS 15、DC 2、RST -1、MISO 12、背光 21。橫放 320×240。
- 序列埠 115200。開機畫面顯示「等待連線」，10 秒沒收到訊息顯示離線（跟模擬視窗一致）。
- JSON 解析：ArduinoJson 7。
- 電腦端：`display.py` 實作 serial 輸出（pyserial），`config.toml` 加 `serial_port`（空字串 = 自動找 CH340/CP210x）。拔掉、找不到埠都只記 log，常駐程式照常運作，並定期重試連線。

## 限制
- 不碰按鍵 AI 功能，不加 Claude 呼叫。
- 本機 Python 3.10 不能用；用 `uv run --python 3.12`。
- 不改 `keyboard-mapping.yaml`、`config.toml` 的 `[hotkeys]`。

## 完成標準
1. `pio run -e <env> -t upload` 可燒到 COM4
2. 用測試腳本送 state / press / off 三種訊息，螢幕正確顯示中文、模式色、按鍵回饋
3. `config.toml` 設 `display = "serial"` 後，`啟動.bat` 啟動常駐程式，看板跟著前景視窗切換
