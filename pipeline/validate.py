"""Arithmetic validation of OCR output: compare item sums/counts to declared totals."""
import json, os, sys, glob
def s(items, key='twd'):
    return round(sum((i.get(key) or 0) for i in items), 2)
def check(rec):
    d = rec['data']; issues = []
    def cmp(label, items_sum, declared, tol=1.0):
        if declared is None: 
            if items_sum: issues.append((label, 'no declared total', items_sum, None))
            return
        if abs(items_sum - declared) > tol: issues.append((label, 'sum mismatch', items_sum, declared))
    def cnt(label, n, declared):
        if declared is not None and n != declared: issues.append((label, 'count mismatch', n, declared))
    cmp('cash', s(d['cash']), d['cash_total_twd'])
    cmp('deposit', s(d['deposits']), d['deposit_total_twd'])
    cnt('deposit', len(d['deposits']), d['deposit_count'])
    cmp('stock', s(d['stocks']), d['stock_total_twd'])
    cnt('stock', len(d['stocks']), d['stock_count'])
    cmp('bond', s(d['bonds']), d['bond_total_twd'])
    cmp('fund', s(d['funds']), d['fund_total_twd'])
    cmp('other_sec', s(d['other_securities']), d['other_securities_total_twd'])
    sec = s(d['stocks'])+s(d['bonds'])+s(d['funds'])+s(d['other_securities'])
    cmp('securities', sec, d['securities_total_twd'])
    cmp('other_assets', s(d['other_assets'],'amount_twd'), d['other_assets_total_twd'])
    cmp('debt_claims', s(d['debt_claims'],'amount_twd'), d['debt_claims_total_twd'])
    cmp('debts', s(d['debts'],'amount_twd'), d['debts_total_twd'])
    cnt('debts', len(d['debts']), d['debts_count'])
    cmp('business', s(d['business_investments'],'amount_twd'), d['business_investments_total_twd'])
    cnt('land', len(d['land']), d['land_count'])
    cnt('building', len(d['buildings']), d['building_count'])
    return issues
if __name__ == '__main__':
    out_dir = sys.argv[1] if len(sys.argv) > 1 else 'work/ocr'
    files = sorted(glob.glob(os.path.join(out_dir, '[0-9]*.json')))
    bad = {}; kinds = {}
    for f in files:
        rec = json.load(open(f, encoding='utf-8'))
        iss = check(rec)
        if iss:
            bad[rec['seq']] = iss
            for i in iss: kinds[(i[0], i[1])] = kinds.get((i[0], i[1]), 0) + 1
    print(f"{len(files)} files, {len(bad)} with issues")
    for k, v in sorted(kinds.items(), key=lambda x: -x[1]): print(k, v)
    json.dump(bad, open(os.path.join(out_dir, '_issues.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    for seq, iss in list(bad.items())[:15]: print(seq, iss)
