# 候選人財產申報資料庫

資料庫由 `pipeline/build.py` 產生，不放在 repo 裡：網站的「下載」連結有最新版的 `candidate_wealth.sqlite` 與每張表的 CSV（內容相同）。每張表都以 `year`（115 或 111）＋ `seq`（115 年中選會申報表編號）識別一位候選人；111 年資料以「同縣市、同姓名」對到 115 年的 `seq`（見 `match_111_115`）。

## 金額欄位的三種口徑
- `declared_*`：候選人在申報表上自己寫的數字（含自己用的匯率換算結果），原樣保留。
- `declared_totals`：申報表上印的各分項「總金額／總申報筆數」，也就是候選人自己認知的加總。
- `std_*`：外幣一律以臺灣銀行牌告「即期買入與賣出的中價」重新換算。115 年用 2026-10-16，111 年用 2022-09-02（與股價基準日相同），匯率表在 `fx_rates`。
- `market_twd`：證券以基準日收盤價重算（台股），外幣證券以統一匯率重算；查不到收盤價者等於申報值。

## 外幣欄位
| 欄位 | 說明 |
|---|---|
| currency_raw | 表上幣別原文 |
| currency | ISO 代碼；表上未寫時由名稱或金額反推 |
| currency_source | declared（表上寫明）、name（由證券名稱判斷）、inferred（由金額比例推得）、default（視為新臺幣） |
| foreign_amount | 外幣金額 |
| declared_rate | 候選人使用的匯率 |
| rate_source | stated（表上明寫）、derived（新臺幣金額 ÷ 外幣金額反推）、unknown（外幣欄無法使用，改用申報期間臺銀中價推算外幣金額） |
| declared_twd_filled | 表上只寫外幣、新臺幣欄空白時，本站以申報期間臺銀中價補算的值 |
| std_rate / std_date / std_twd | 統一匯率、其日期與換算結果 |

## 主要資料表
candidates、summary、declared_totals、deposits、cash、securities（股票、基金、債券、其他）、land、buildings、vehicles（廠牌型號、汽缸容量、牌照、取得資料）、ships、aircraft、insurance、debt_claims、debts、business_investments、other_assets、validation_issues（明細加總與表上總額不符）、fx_rates、stock_prices、match_111_115。

## 政黨
`candidates.party` 取自中選會「115 年地方公職人員選舉候選人登記名冊」（製表日期 115/09/07，`data/raw/cec_roster_115.json`，由 Gemini 3.5 Flash-Lite 轉錄），比對結果在 `data/parties_115.json`。`party_on_declaration_site` 是財產申報系統上的政黨欄，多數空白且有錯，只供參照。

## 轉錄來源
- 115 年主表：Gemini 3.5 Flash，加總不符者以 Gemini 3.1 Pro 重讀，取加總較一致的版本（`candidates.transcribed_by`）。
- 111 年主表：Gemini 3.5 Flash 705 份、Claude Haiku 166 份。
- 汽車、船舶、航空器與外幣列：Gemini 3.5 Flash-Lite，與主表筆數或外幣列對不上者以 Gemini 3.8 Flash 重讀。
- 人工更正：`corrections/<年度>/<編號>.yaml`，每次重建時套用；`candidates.corrections` 是套用的筆數，`candidates.manual_note` 是說明。
