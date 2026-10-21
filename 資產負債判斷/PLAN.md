# 資產負債判斷 — 計畫

富爸爸式個人財務報表 + AI 判斷買的東西是資產、負債還是消費。
做成 claude.ai 私人 Artifact（`app.html`），AI 用 `sample` capability，資料用 `db` 存在
`data/users/<自己的 id>/`（只有本人讀寫）。舊的 `index.html` 是第一版單機計算器，保留不動。

## 資料模型（collection `data/users/<id>`）
- doc `profile`：`{ salary, living }` 每月工資收入、每月生活支出
- doc `item_<時間戳>`：`{ type:"item", name, kind:"asset"|"liability"|"expense", value, debt,
  monthlyIncome, monthlyCost, reason, createdAt }`
  - value：現值；debt：剩餘欠款；monthlyIncome：每月帶進的錢；monthlyCost：每月要付的錢

## 報表
- 收入表：工資 + 被動收入（各項 monthlyIncome）− 生活支出 − 各項 monthlyCost = 每月淨現金流
- 資產負債表：資產欄（kind=asset 的 value）、負債欄（各項 debt）、淨值 = 總 value − 總 debt
- 指標：被動收入 ÷ 總支出（財務自由度 %）

## AI 判斷
- 自由輸入「想買什麼、怎麼付、拿來幹嘛」
- `sample.json` 回 `{ name, kind, reason, value, debt, monthlyIncome, monthlyCost, missing[] }`
- 送進去的 prompt 附上目前報表摘要；結果填進可編輯表單，按「加進報表」才存
- 只分類和試算，不給買不買的建議（見 memory no-investment-advice）

## 驗證
- 發佈後 `ArtifactData list` 看 collection；AI 判斷需在 claude.ai 上實際點才會跑
