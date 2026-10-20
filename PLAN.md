# 智慧快捷鍵盤 + 桌上看板

## 目標
6 鍵 + 旋鈕的實體快捷鍵盤，依前景軟體（PPT / Creo / 新版 Outlook / 專利 / 數學）切換功能；
ESP32 小螢幕當輸出看板。公司不行就當私人用。

## 已知環境（公司）
- Python 可用；USB 裝置可用（藍牙滑鼠接收器沒被擋）
- AI：只有企業版 Cursor → 嘗試 Cursor CLI（`cursor-agent -p`）；備案 M365 Copilot
- Outlook 是新版（olk.exe）→ 沒有 COM，信件功能走「選取 → 複製 → AI」

## 開發分工（兩台電腦）
- 個人電腦（這台）：寫程式、測 PPT（這台有裝）、數學工具、常駐程式骨架；鍵盤用 SIKAI 軟體在這台設定
- 公司電腦：實際使用環境；有 Claude Code（只用來開發與除錯）和企業版 Cursor
- 按鍵觸發的 AI 一律走 Cursor CLI，不接 Claude
- 只能在公司測的：Creo mapkey、新版 Outlook、Cursor CLI、看板 USB 驅動（CH340）
- 程式要可攜：同一份程式碼搬到公司，用 check_env.py 先確認環境

## 架構
鍵盤送 F13–F24 → 電腦常駐 Python（偵測前景程式 → 查表 → 執行動作）→ 結果經 USB 序列埠送到看板

## 階段
0. [家裡已跑，等公司報告] `check_env.py`：在公司電腦跑一次，確認能做到哪一層
1. [骨架完成 2026-10-20] 常駐程式骨架：F13–F24 全域熱鍵、前景程式偵測、設定檔（YAML/JSON）對應按鍵
   - 沒硬體前用一般鍵盤組合鍵模擬
2. 不用 AI 的功能：Creo mapkey、PPT 對齊/格式刷、單位換算、公差疊加、加總
3. AI 功能：翻譯、改寫、摘要、專利白話與特徵拆解（接 Cursor CLI 或其他）
4. 硬體：現成 macropad 或 ESP32-S3 自製；CYD 看板顯示（中文字型子集）

## 已知限制
- Creo 量測值無法讀出（需 Toolkit 授權）→ 只做開啟量測
- 旋鈕只做縮放，不做旋轉
- Mapkey 不能動態檔名 → 匯出後由 Python 改名
