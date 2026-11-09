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

## 交接狀態（2026-11-09）

### 背單字（CYD 多益單字卡）— 新增的模式，不是換掉用途
- 背單字開啟時：CYD 螢幕改成輪播多益單字（看板不顯示），鍵 1＝不會、鍵 2＝會、旋鈕左轉上一張／右轉下一張／按下翻面；**鍵 3～6 照原本的快捷鍵動作**。關掉就回到原本的看板。
- 開關：`config.toml` `[vocab] enabled`（目前 `true`，開機就輪播）；`背單字.bat` 強制開；`python -m macropad --no-vocab` 強制關。只想在某台關掉，就在那台的 `local_config.toml` 寫 `[vocab]` `enabled = false`。
- 排程：FSRS（py-fsrs），04:00 換日；新字一天最多 10 個（開頭 3 個，之後每專心 20 分鐘補 1 個）；要在兩個不同日子答「會」才算學會。規則細節見 `design/cyd-vocab-plan.md`「2026-11-09 試用後修改」。
- 字表：`vocab/words.csv` 3051 字（TOEIC Service List 1250 ＋ NGSL 第 1001–2809 名穿插，CC BY-SA 4.0，出處見 `vocab/README.md`）。
- 進度：`config.toml` `data_dir = "%OneDrive%/背單字進度"`，家裡與公司靠 OneDrive 同步；找不到 `%OneDrive%` 時改存 `vocab/`。進度檔不進 git。**兩台不要同時開**（會互相覆蓋）。
- 做到哪：計畫 `design/cyd-vocab-impl-plan.md` 段 A 字表 ◐、B 排程 ☑、C 韌體 ◐（觸控沒實測）、D 整合 ◐（家裡已開機自動跑，待用滿一天）、E 公司 ☐。`uv run pytest` 39 個全過。
- 待決定：在學習卡（新字）翻面後按「會」，要不要改成今天就不再出現（目前只是暫停兩輪，使用者覺得重複太多）。

新檔案：
- 程式：`macropad/vocab.py`（排程與進度）、`macropad/vocab_app.py`（按鍵、序列埠、計時、鎖定暫停）；修改 `macropad/__main__.py`（`--vocab`、OneDrive 路徑）、`config.toml`、`pyproject.toml`（fsrs、pytest）、`uv.lock`
- 測試：`tests/test_vocab.py`、`tests/test_vocab_app.py`
- 字表：`vocab/tsl.csv`、`vocab/ngsl_a.csv`、`vocab/ngsl_b.csv`、`vocab/words.csv`、`vocab/README.md`、`vocab/sample50.md`（抽查用）；工具 `tools/build_words.py`（重建字表）、`tools/check_glyphs.py`（檢查字型缺字）
- 韌體：`firmware/cyd/`（PlatformIO；`src/vocab/main.cpp` 是背單字，板子已燒好這版）；工具 `tools/cyd_send.py`、`tools/cyd_shot.py`
- 啟動：`背單字.bat`（手動）、`啟動.bat`（背景執行，可放進「啟動」資料夾開機自動跑）
- 文件：`design/cyd-vocab-plan.md`、`design/cyd-vocab-impl-plan.md`、`design/cyd-vocab-flow.png`（流程圖）、`design/cyd-firmware-plan.md`
- 同批一起上傳的其他實驗（與背單字無關、可先不管）：遊戲模式 `macropad/game_bridge.py`、`遊戲.bat`、`design/cyd-game-plan.md`；GIF 播放 `design/cyd-gif-plan.md`（GIF 素材不上傳）

公司電腦接手（照順序）：
1. 家裡那台先關掉背單字（工作列沒有視窗，用 Ctrl+Alt+Shift+0 結束），避免兩台同時寫進度。
2. `git pull` → `uv sync` → `uv run pytest`（應該 39 個全過；不要用系統 Python 跑 pytest）。
3. 確認 `%OneDrive%` 是**個人** OneDrive：PowerShell 跑 `echo $env:OneDrive`，看裡面有沒有家裡同步過來的 `背單字進度` 資料夾。如果是公司帳號或沒有，就在 `local_config.toml` 加 `[vocab]` `data_dir = "..."` 指到個人 OneDrive 的那個資料夾（或先用預設 `vocab/`，進度就不會跟家裡同步）。
4. CYD 插 USB，裝置管理員看有沒有 COM 埠（CH340／CP210x；沒有就要驅動，公司可能擋）。
5. 雙擊 `背單字.bat`：螢幕應先顯示「等待電腦連線」再出第一張卡，狀態列「已學會N」「會X 不會Y」的數字要接著家裡的。
6. 實測：鍵 1／2、旋鈕左右轉與按下、鍵 3～6 在 PPT／Creo 照常、鎖定電腦後暫停解鎖續播、拔插 USB 自動重連、觸控短按／長按。
7. 要開機自動跑：Win+R `shell:startup`，放一個 `啟動.bat` 的捷徑。
8. 改了 Python 要重開程式；改韌體才需要燒錄，一律在家裡那台用 PlatformIO（`pio run -d firmware/cyd -e cyd_vocab -t upload`），公司不裝。

### 原本的快捷鍵盤
- 完成：常駐程式骨架（`python -m macropad`，或雙擊 `啟動.bat`），家裡實測記事本模式正常；PPT 模式未實測
- 完成：看板 UI 設計稿 `design/screen-design.html`（A 側軌 / B 色塊 / C 圖紙，推薦 A＋C 的公差表格），**使用者還沒選定**
- 硬體：SIKAI 鍵盤（1189:8840）已用 ch57x-keyboard-tool 寫入 `keyboard-mapping.yaml`，9 個輸入實測正常（2026-10-27）；Windows 寫入需先裝 UsbDk 並指定 `--product-id 34880`；CYD 2.8 吋 Type-C 待到貨
- 下一步：
  1. 公司電腦 `git clone https://github.com/danny95364-sudo/smart-macropad.git` → `python check_env.py --ai` → 雙擊 `啟動.bat` 實測
  2. [完成 2026-10-27] 看板選定方向 B 色塊；模擬視窗 `macropad/display_sim.py`，資料格式見 `macropad/display.py` 檔頭，CYD 到貨後實作 SerialDisplay
  3. 數學小工具（單位換算、公差、加總）
  4. PPT 改用 COM 控制（對齊、分布、存 PDF）
- 注意：本機 Python 3.10，程式需 3.11+（`啟動.bat` 會優先用 uv 的 3.12）

## 已知限制
- Creo 量測值無法讀出（需 Toolkit 授權）→ 只做開啟量測
- 旋鈕只做縮放，不做旋轉
- Mapkey 不能動態檔名 → 匯出後由 Python 改名
