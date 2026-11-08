# CYD 背單字 實作計畫

規則來源：`design/cyd-vocab-plan.md`（「學習演算法 v2」「評分規則」「排程細節」「介面原則」「鍵位」）。本檔只講**怎麼做、做到什麼算完成**；規則有衝突以計畫書為準。
狀態標記：☐ 未做 ◐ 進行中 ☑ 完成。每完成一段就更新這裡。

## 總覽與順序
| 段 | 內容 | 依賴 | 驗收方式 |
|---|---|---|---|
| A | 字表 `vocab/tsl.csv` + 缺字檢查 | — | 檢查腳本 0 缺字、抽查 50 字 |
| B | 排程 `macropad/vocab.py` + 測試 | A 的欄位格式（可先用假資料） | `uv run pytest` 全過，含 30 天模擬 |
| C | 序列埠協定 + 韌體改寫 | 協定定義（本檔） | `tools/cyd_shot.py` 截圖：學習卡、到期卡、暫停、toast |
| D | 整合 macropad 常駐程式 + 鎖定偵測 | B、C | 家裡實測一天 |
| E | 公司 USB 確認、離線安裝包 | D | 公司電腦開 bat 能用 |

B 與 C 互不相依，可平行。A 的字表格式先定（下方），B 用假資料開發。

## 段 A：字表 ☐
- 來源：TSL 1.2（CC BY-SA 4.0），檔頭註明出處與授權。
- 先確認 TSL 是否每個 headword 只出現一次：是 → `id` = 單字（小寫）；否 → `id` = 單字|詞性。
- 欄位（UTF-8、逗號分隔、有標頭）：`id,word,pos,zh,example,topic,rank`
  - `zh` ≤ 12 字；`example` ≤ 70 字元（螢幕 1.5 行，FreeSans 只有 ASCII，不可有彎引號、破折號等非 ASCII）。
  - `topic`：辦公、財務、人事、旅行、採購、製造、行銷、日常（固定 8 類）。
  - `rank`：TSL 頻率序。
- 在家用 Claude 分批產生（每批 100 字），人工抽查 50 字。
- `tools/check_glyphs.py`：把 `zh`、`pos` 所有字元比對韌體字型 efontTW_14／24 是否有字形（從 LovyanGFX 字型表取 code point 清單）；example 非 ASCII 也報錯。**完成標準：0 缺字。**

## 段 B：排程 `macropad/vocab.py` ☐
- 依賴：`fsrs`（py-fsrs）。公司不能連網 → 段 E 處理。若授權/大小允許，也可 vendor 到 `macropad/_vendor/fsrs`。
- 時鐘可注入（`now()` 函式參數），換日 04:00。
- 資料：
  - `vocab/progress.json`：`{"version":1,"day_no":D,"day_key":"YYYY-MM-DD","today":{...計數...},"cards":{id:{"state":"learning|review","fsrs":{...},"grad":0|1,"grad_day":"..."}}}`；未出現的字不存。
  - `vocab/reviews.csv`：`ts,id,rating,kind,undone`。
  - `vocab/flags.csv`：`ts,id,note`。
  - 寫入：暫存檔 → `os.replace`；換日時備份 `vocab/backup/progress-YYYYMMDD.json`，保留 14 份。
- 介面（給 D 呼叫，純邏輯、不碰序列埠）：
  - `next_card() -> Card`（含 kind=learn/due、狀態列字串）
  - `answer(id, good: bool, back_seen: bool)`、`undo()`（5 秒內有效）、`exposed(id)`、`back_shown(id)`、`skip_due(id)`
  - `status() -> (left, right)`，如 `("D12·新3", "答8·剩5")`
- 規則對照（全部要有測試）：
  1. 首見且未看過背面按會 → FSRS Easy，直接 review，不佔新字名額，立即補位。
  2. 學習中按會：不同日子累計 2 次才畢業（FSRS Good）；同日第二次不算。按不會 → grad 歸零、下一輪優先。
  3. review 到期卡：按會 Good、不會 Again → 回 learning。
  4. 每日複習上限 30，依 retrievability 低→高；每 3 張學習卡插 1 張到期卡。
  5. 新字每日上限 10；當天 30 張答完仍有到期 → 新字保底 3。
  6. 到期卡 60 秒無按鍵 → skip，不評分，稍後再出。
  7. 2 秒內同卡重複按鍵忽略；undo 回復 FSRS 狀態與計數並在 reviews.csv 記 undone。
  8. 被動曝光不寫 FSRS。
  9. 換日 04:00：day_no+1（只在當天有作答時才算一天）、計數歸零、備份。
  10. 字表更新：找不到 id 的進度保留並記警告。
- 測試 `tests/test_vocab.py`：每條規則一個測試 + 30 天模擬（隨機作答率 70%、跳過 2 天週末）檢查：不崩潰、新字不會連續多天 0、每日複習 ≤ 30、存檔可重載。

## 段 C：序列埠協定 + 韌體 ☐
序列埠 115200，UTF-8，一行一則，欄位用 Tab 分隔（不用 JSON，免裝 ArduinoJson）。韌體收行緩衝放大到 512 bytes。

PC → 板子：
| 指令 | 欄位 | 板子行為 |
|---|---|---|
| `card` | id, kind(learn/due), word, pos, zh, example, statusL, statusR | 顯示正面；learn 依 6 s／8 s 自動翻面，翻到背面回報 `back`，背面時間到回報 `done`；due 不自動翻面 |
| `flip` | — | 翻面（翻到背面同樣回報 `back`） |
| `toast` | 文字, 色系(ok/bad/info) | 顯示 900 ms |
| `status` | statusL, statusR | 只更新狀態列 |
| `pause` / `resume` | — | 暫停計時、顯示暫停色塊 |
| `shot` | — | 截圖（已實作） |

板子 → PC：`hello cyd_vocab <版本>`（開機）、`back <id>`、`done <id>`、`tap`、`hold`、`shot ...`（截圖資料）。
- 板子不再處理鍵盤（鍵由 PC 熱鍵處理）；觸控短按 → `tap`（PC 回 `flip`），長按 1 秒 → `hold`（PC 切換暫停）。
- 畫面：due 卡左側 6 px 紫色直條、狀態列左「複習」色塊；底部按鍵說明改「2 會 · 1 不會 · 旋鈕 換字/翻面」。
- 拿掉寫死的 8 張卡；沒收到 `card` 前顯示「等待電腦連線」。
- 驗收：用一支小腳本 `tools/cyd_send.py` 送假卡片，`tools/cyd_shot.py` 截圖確認學習卡正反面、due 卡、暫停、toast、最長 zh/example；狀態列「D12·新3」「答8·剩5」不重疊。

## 段 D：整合常駐程式 ☐
- `config.toml` 加 `[vocab] enabled = true`；啟用時 CYD 由 vocab 使用，看板 display 不啟動（同一個 COM 埠只能一方用）。
- 熱鍵：沿用 `HotkeyListener` 的 9 組；鍵 1、2、旋鈕（7 左、8 右、9 按）轉給 vocab，3～6 照原 actions。任何一組註冊失敗 → 啟動時報錯並結束（不是只印警告）。
- 注意 `display_flip_keys`：game_bridge 送 7−實體鍵號，整合後在 PC 端統一換算成實體鍵號再分派。
- 計時：learn 卡由板子計時（`done` 後 PC 送下一張）；due 卡 60 秒由 PC 計時。
- 鎖定／休眠：用 `WTSRegisterSessionNotification` 掛在 tk root 的 hwnd 收 `WM_WTSSESSION_CHANGE`；鎖定 → `pause`、不送卡；解鎖 → 重連序列埠、`resume`。休眠後序列埠斷線 → 每 5 秒重連。
- 撤銷：旋鈕左轉在作答後 5 秒內 = undo，否則 = 上一張。
- 標錯：電腦端小指令 `uv run python -m macropad.vocab flag <id> <note>`。
- `背單字.bat` 改成啟動常駐程式（vocab 開啟）；`遊戲.bat` 不動。
- 驗收：家裡實測一天，檢查 reviews.csv 筆數、換日備份、鎖定再解鎖能續播、拔插 USB 能恢復。

## 段 E：公司環境 ☐
- 離線安裝：在家 `uv sync` 後把整個專案資料夾（含 `.venv`）帶去；或 vendor fsrs。公司不跑 `uv add`。
- 確認 CYD 插上不需裝驅動（CH340）；需要 → 停，問 IT。
- 公司不燒韌體、不呼叫 AI。

## 分工（依 CLAUDE.md）
- A 的字表產生：general-purpose（需要判斷）；缺字檢查腳本：grunt。
- B：general-purpose（實作＋測試）；驗收跑測試：grunt。
- C：general-purpose 改韌體；燒錄與截圖由主 agent 或 grunt（pio 只能在 PowerShell：`& "$env:USERPROFILE\.local\bin\pio.exe" run -d firmware/cyd -e cyd_vocab -t upload`；燒錄前停掉佔 COM4 的程式）。
- D：general-purpose。
- 派工時附本檔路徑＋計畫書路徑＋對應段落，回報只要摘要。

## 已知的坑（從 HANDOFF.md 摘）
- vocab 韌體要 `PANEL_INVERT=0`；分段 sprite 每次 pushSprite 後要 `lcd.waitDMA()`。
- 觸控 Y 軸顛倒：`-DTOUCH_Y_MIN=3900 -DTOUCH_Y_MAX=200`。
- `partitions_gif.csv` 只能 ASCII。
- 在 bash heredoc 裡用 Python 產生 C 字串時 `\n` 會被轉成真的換行（踩過一次），改 C 原始碼用 Edit 工具。
