"""Check correction files and write a Markdown report for reviewers (CI and local use).

python pipeline/check_corrections.py                      # every correction file
python pipeline/check_corrections.py corrections/115/2517.yaml ...
python pipeline/check_corrections.py --changed origin/main # files changed against a git ref
Options: --report FILE (Markdown output), --pages DIR (render the cited PDF pages if the PDFs are present locally)
Exit code 1 if any file fails to load or any change fails to apply.
"""
import argparse, glob, os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths
import corrections as C
import declaration as D
from pricing import Pricer
from validate import check

SEC_LABEL = {'cash': '現金', 'deposit': '存款', 'stock': '股票', 'securities': '有價證券', 'bond': '債券', 'fund': '基金', 'other_sec': '其他有價證券',
             'other_assets': '其他財產', 'debt_claims': '債權', 'debts': '債務', 'business': '事業投資', 'land': '土地', 'building': '建物'}


def fmt(v):
    return f'{v:,.2f}'.rstrip('0').rstrip('.') if isinstance(v, (int, float)) else str(v)


def pdf_path(year, seq):
    if year == '115':
        p = os.path.join(paths.PDFS, 'y115', f'{seq}.pdf')
        return [p] if os.path.exists(p) else []
    g = paths.groups_111().get(seq, {})
    return [p for p in (os.path.join(paths.PDFS, 'y111', f) for f in g.get('files', [])) if os.path.exists(p)]


def render(year, seq, pages, out):
    made = []
    for pdf in pdf_path(year, seq)[:1]:
        for pg in sorted(set(p for p in pages if p)):
            stem = os.path.join(out, f'{year}-{seq}-p{pg}')
            if subprocess.run(['pdftoppm', '-r', '110', '-png', '-singlefile', '-f', str(pg), '-l', str(pg), pdf, stem], capture_output=True).returncode == 0:
                made.append(stem + '.png')
    return made


def check_file(path, meta, pricers, pages_dir):
    lines, ok = [], True
    try:
        cf = C.load_file(path)
    except Exception as e:
        return False, [f'### ❌ `{os.path.relpath(path, paths.ROOT)}`', '', f'檔案格式錯誤：{e}', '']
    m = meta.get(cf.seq)
    head = f'### {cf.year} 年 {cf.name}（{cf.seq}）'
    if not m: return False, [head, '', f'❌ 找不到申報表編號 {cf.seq}', '']
    if m['NAME'].replace(' ', '') != cf.name.replace(' ', ''): lines.append(f'⚠️ name 寫「{cf.name}」，資料是「{m["NAME"]}」')
    try:
        dd, rec, agree = D.pick(cf.seq, cf.year, cf.base)
    except ValueError as e:
        return False, [head, '', f'❌ {e}', '']
    if rec is None: return False, [head, '', f'❌ {cf.year} 年沒有編號 {cf.seq} 的轉錄', '']
    extra = D.load_extra(cf.seq, rec['data'], cf.year)
    before_iss = {(i[0], i[1]): i for i in check(rec)}
    before = D.summarize(D.copy.deepcopy(rec), m, cf.year, D.copy.deepcopy(extra), pricers[cf.year])['sum']
    log = C.apply(cf, rec['data'], extra)
    after_iss = {(i[0], i[1]): i for i in check(rec)}
    after = D.summarize(rec, m, cf.year, extra, pricers[cf.year])['sum']
    lines += [head, '', f'套用的轉錄版本：`{dd}`' + ('（檔案指定）' if cf.base else '（自動選擇，建議在檔案加上 `base`）'), '']
    lines += ['| # | 結果 | 內容 | 原表頁碼 |', '|---|---|---|---|']
    for i, status, msg in log:
        c = cf.changes[i]
        ok &= status == 'ok'
        page = c.page or ''
        if cf.year == '115' and page: page = f'[第 {page} 頁]({paths.YEARS["115"]["pdf_url"].format(seq=cf.seq)}#page={page})'
        lines.append(f"| {i} | {'✅' if status == 'ok' else '❌'} | {msg.replace('|', '／')}；理由：{c.reason.replace('|', '／')} | {page} |")
    lines += ['', '**明細加總和表上總額的比對**（✅ 表示對得上）', '', '| 分項 | 更正前 | 更正後 |', '|---|---|---|']
    keys = sorted({k for k in list(before_iss) + list(after_iss) if k[1] != 'no declared total'})
    if not keys: lines.append('| 全部 | ✅ | ✅ |')
    for k in keys:
        b, a = before_iss.get(k), after_iss.get(k)
        cell = lambda x: '✅' if x is None else f'❌ 明細 {fmt(x[2])}／表上 {fmt(x[3])}'
        lines.append(f'| {SEC_LABEL.get(k[0], k[0])}{"筆數" if k[1] == "count mismatch" else ""} | {cell(b)} | {cell(a)} |')
    lines += ['', f"淨資產（申報）：{fmt(before['net_decl'])} → {fmt(after['net_decl'])}；淨資產（市值）：{fmt(before['net_mkt'])} → {fmt(after['net_mkt'])}", '']
    if pages_dir:
        imgs = render(cf.year, cf.seq, [c.page for c in cf.changes], pages_dir)
        if imgs: lines += [f'已輸出原表頁面圖檔：{", ".join(os.path.basename(x) for x in imgs)}', '']
    return ok, lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='*'); ap.add_argument('--changed'); ap.add_argument('--report'); ap.add_argument('--pages')
    a = ap.parse_args()
    files = a.files
    if a.changed:
        out = subprocess.run(['git', 'diff', '--name-only', '--diff-filter=ACMR', a.changed, '--', 'corrections/'], capture_output=True, text=True, cwd=paths.ROOT).stdout
        files = [os.path.join(paths.ROOT, f) for f in out.split() if f.endswith('.yaml')]
    elif not files:
        files = sorted(glob.glob(os.path.join(paths.CORR, '*', '*.yaml')))
    if a.pages: os.makedirs(a.pages, exist_ok=True)
    meta = paths.candidates_index(); pricers = {y: Pricer(y) for y in paths.YEARS}
    report, all_ok = ['## 更正檢查結果', ''], True
    if not files: report.append('這次沒有變更任何更正檔。')
    for f in files:
        ok, lines = check_file(f, meta, pricers, a.pages)
        all_ok &= ok; report += lines
    report.append('**全部通過**' if all_ok else '**有更正無法套用，請依上面的 ❌ 修改。**')
    text = '\n'.join(report)
    if a.report: open(a.report, 'w', encoding='utf-8').write(text + '\n')
    print(text)
    sys.exit(0 if all_ok else 1)


if __name__ == '__main__':
    main()
