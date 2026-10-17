# 協助更正資料

資料是 AI 從掃描的申報表轉錄的，難免讀錯。歡迎對照原始申報表，把錯誤回報給我們。

只更正「轉錄錯誤」，也就是資料和原始申報表不一致。如果申報表本身填錯、加總不合，請在說明裡寫出來，資料原則上仍以申報表上的內容為準。

例外是申報人明顯的筆誤，例如淨值漏打小數點、金額欄多打一串數字，造成金額差上千倍。這類更正必須在 `note` 寫出原表的數字與推算方式，並需要兩位審查者核准。

## 從哪裡開始

- **網站上看到錯誤**：打開候選人明細，每一列最右邊有「回報」。點下去會開一張 GitHub Issue，年度、編號、分項、列號都已填好，只要補上正確的值、頁碼和說明。
- **想主動幫忙核對**：Issues 裡標著 `needs-review` 的是加總對不上、或不同模型讀法不一致的申報表，挑你熟悉的縣市開始。
- **原始申報表**：115 年在中選會網站，網址就是明細頁的「開啟中選會原始申報表 PDF」；111 年在 [Releases](https://github.com/Sigma-Snaken/candidate-wealth-tw/releases) 的壓縮檔，對照表在 `data/pdf_manifest.csv`。

## 回報之後會發生什麼

1. **維護者對照原表**：沒問題就加上 `accepted` 標籤。
2. **系統自動開 PR**：把你的回報轉成 `corrections/` 裡的一筆更正，並附上更正前後的加總比對。
3. **審查者核准後合併**：網站與資料庫在幾分鐘內自動重建。
4. **轉換失敗**：例如找不到那一列，系統會在 issue 留言說明，修改內容後再加一次標籤即可。

## 直接改更正檔（熟悉 GitHub 的人）

一份申報表一個檔案：`corrections/<年度>/<申報表編號>.yaml`。

```yaml
year: '115'
seq: '3229'
name: 林政賢
base: y115_pro            # 套用在哪一份轉錄上；新檔請照網站資料庫 candidates.transcription 填
note: 顯示在網站明細頁的人工核對說明（可省略）
changes:
  - target: stocks         # 分項，見下表
    row: 55                # 0 起算，與資料庫 idx、網站「回報」連結的列號相同
    match: {name: 台嘉碩}   # 用來確認列號沒有對錯
    field: twd
    old: 10000010000       # 目前資料中的值，必須完全相同
    new: 10000
    page: 15               # 原表頁碼，必填
    reason: 原表金額欄誤植，依股數 × 面額更正
    source: '#12 @你的帳號'
```

- **`op`**：預設 `set` 改一個欄位。`add` 用 `values` 新增整列；`delete` 用 `row` 加 `match` 刪除一列。
- **`target: form`**：改表頭或各分項總額，`field` 例如 `deposit_total_twd`、`declarant`、`minor_children_count`。
- **`form_inconsistent: true`**：表示人工確認申報表本身的加總就不一致。

| target | 分項 | 常用欄位 |
|---|---|---|
| deposits | 存款 | bank、currency、foreign_amount、twd、owner |
| cash | 現金 | currency、foreign_amount、twd |
| stocks | 股票 | name、code、shares、par、twd |
| funds | 基金 | name、units、par、currency、twd |
| bonds、other_securities | 債券、其他有價證券 | name、units、twd |
| land、buildings | 土地、建物 | location、area_m2、share、owner、acquired、reason、price |
| vehicles | 汽車 | brand_model、brand、displacement_cc、plate、owner、acquired、price_raw |
| insurance、debt_claims、debts、business_investments、other_assets | 保險、債權、債務、事業投資、其他財產 | desc、amount_twd、owner、note |

送出 PR 前可以在本機檢查：

```bash
pip install -r requirements.txt
python pipeline/check_corrections.py corrections/115/3229.yaml
```

PR 的自動檢查會跑同樣的程式，把報告放在 Checks 的 Summary。所有更正都必須能套用，否則無法合併。

## 審查原則

- **每筆更正都要對照原表那一頁**：PR 報告裡有中選會 PDF 的頁面連結。
- **加總對得上的更正**：一位審查者核准即可。
- **改完加總仍對不上的更正**：需要兩位審查者。
- **不接受推論或外部資訊**：例如「這家公司應該已經改名」。資料只反映申報表上的內容。

## 維護者設定（新 repo 只需做一次）

1. 建立標籤：`correction`、`accepted`、`needs-review`。
2. Settings → Actions → General → Workflow permissions：勾選「Allow GitHub Actions to create and approve pull requests」，自動開 PR 才會成功。
3. Settings → Pages → Source 選「GitHub Actions」。
4. Settings → Branches：保護 `main`，要求「檢查更正」通過並至少一位審查者核准。
5. 機器人開的 PR 不會自動觸發「檢查更正」，但轉換時已跑過同樣的檢查與完整重建，失敗就不會開 PR。

提交 issue 或 PR，即表示同意你的貢獻依 [LICENSE-DATA.md](LICENSE-DATA.md) 以 CC BY 4.0 釋出。
