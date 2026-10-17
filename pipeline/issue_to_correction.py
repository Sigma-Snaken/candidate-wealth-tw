"""Turn an accepted「資料更正」issue (GitHub issue form) into an entry in corrections/<year>/<seq>.yaml.

python pipeline/issue_to_correction.py --event $GITHUB_EVENT_PATH --out result.json
python pipeline/issue_to_correction.py --body-file body.md --number 12 --user someone --out result.json

The issue text is untrusted input: it is only parsed here, never passed to a shell.
result.json: {"ok": bool, "path": ..., "message": ..., "title": ...}
"""
import argparse, json, os, re, sys, typing
import yaml
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths
import corrections as C
import schema as S
import declaration as D

LABELS = {'年度': 'year', '申報表編號': 'seq', '候選人姓名': 'candidate', '分項': 'section', '列號': 'row', '欄位': 'field',
          '目前的值': 'current', '正確的值': 'correct', '原表頁碼': 'page', '說明與依據': 'evidence'}
SECTIONS = {'存款': 'deposits', '現金': 'cash', '股票': 'stocks', '基金': 'funds', '債券': 'bonds', '其他有價證券': 'other_securities',
            '土地': 'land', '建物': 'buildings', '汽車': 'vehicles', '船舶': 'ships', '航空器': 'aircraft', '保險': 'insurance',
            '債權': 'debt_claims', '債務': 'debts', '事業投資': 'business_investments', '其他財產': 'other_assets', '表頭或總額': 'form'}
AMOUNT = {**dict.fromkeys(['deposits', 'cash', 'stocks', 'funds', 'bonds', 'other_securities'], 'twd'),
          **dict.fromkeys(['insurance', 'debt_claims', 'debts', 'business_investments', 'other_assets', 'trust_items'], 'amount_twd'),
          **dict.fromkeys(['vehicles', 'ships', 'aircraft'], 'price_twd')}
NAME = {'deposits': 'bank', 'land': 'location', 'buildings': 'location', 'vehicles': 'brand_model', 'ships': 'kind', 'aircraft': 'model',
        **dict.fromkeys(['insurance', 'debt_claims', 'debts', 'business_investments', 'other_assets', 'trust_items'], 'desc')}
FIELDS = {'股數': lambda s: 'shares' if s == 'stocks' else 'units', '單位數': lambda s: 'units', '單價': lambda s: 'par', '面額': lambda s: 'par',
          '淨值': lambda s: 'par', '外幣金額': lambda s: 'foreign_amount', '幣別': lambda s: 'currency', '面積': lambda s: 'area_m2',
          '權利範圍': lambda s: 'share', '所有人': lambda s: 'owner', '汽缸容量': lambda s: 'displacement_cc', '牌照': lambda s: 'plate',
          '廠牌型號': lambda s: 'brand_model', '廠牌': lambda s: 'brand', '取得價額': lambda s: 'price_raw' if s in ('vehicles', 'ships', 'aircraft') else 'price',
          '取得時間': lambda s: 'acquired', '取得原因': lambda s: 'reason', '代碼': lambda s: 'code',
          '金額': lambda s: AMOUNT.get(s), '名稱': lambda s: NAME.get(s, 'name')}
FORM_FIELDS = {'存款總額': 'deposit_total_twd', '存款筆數': 'deposit_count', '現金總額': 'cash_total_twd', '股票總額': 'stock_total_twd',
               '股票筆數': 'stock_count', '基金總額': 'fund_total_twd', '債券總額': 'bond_total_twd', '有價證券總額': 'securities_total_twd',
               '債務總額': 'debts_total_twd', '債務筆數': 'debts_count', '債權總額': 'debt_claims_total_twd', '其他有價證券總額': 'other_securities_total_twd', '保險筆數': 'insurance_count', '事業投資總額': 'business_investments_total_twd',
               '其他財產總額': 'other_assets_total_twd', '土地筆數': 'land_count', '建物筆數': 'building_count', '汽車筆數': 'vehicle_count',
               '申報人': 'declarant', '配偶': 'spouse', '未成年子女人數': 'minor_children_count', '申報日': 'declare_date'}
SECTIONS_ZH = {v: k for k, v in SECTIONS.items()}
ID_KEYS = ('name', 'bank', 'location', 'brand_model', 'desc', 'kind', 'model')


def parse_form(body):
    out, cur = {}, None
    for line in (body or '').splitlines():
        m = re.match(r'^###\s+(.+?)\s*$', line)
        if m:
            cur = LABELS.get(m.group(1).strip()); out.setdefault(cur, []) if cur else None; continue
        if cur: out[cur].append(line)
    res = {k: '\n'.join(v).strip() for k, v in out.items()}
    return {k: ('' if v == '_No response_' else v) for k, v in res.items()}


def token(s, table):
    s = (s or '').strip()
    if s in table.values(): return s
    for k, v in sorted(table.items(), key=lambda kv: -len(kv[0])):  # longest label first
        if k in s: return v
    m = re.search(r'[a-z_]+', s)
    return m.group(0) if m and m.group(0) in table.values() else None


def value(s, model=None, field=None):
    """Numbers only for numeric fields, so codes like 0050 keep their leading zeros."""
    raw = (s or '').strip()
    if model is not None and field in model.model_fields:
        ann = model.model_fields[field].annotation
        base = [a for a in (typing.get_args(ann) or (ann,)) if a is not type(None)][0]
        if base not in (int, float): return raw
    t = raw.replace(',', '').replace('，', '').replace('元', '').replace(' ', '')
    if re.fullmatch(r'-?\d+', t): return int(t)
    if re.fullmatch(r'-?\d+\.\d+', t): return float(t)
    return raw


def one_int(s):
    m = re.fullmatch(r'\s*第?\s*(\d+)\s*[頁列]?\s*', s or '')
    return int(m.group(1)) if m else None


def fail(msg):
    return {'ok': False, 'message': msg}


def build_change(f, number, user):
    year = re.sub(r'\D', '', f.get('year', ''))
    seq = re.sub(r'\D', '', f.get('seq', ''))
    if year not in paths.YEARS: return fail('「年度」請填 115 或 111。'), None
    meta = paths.candidates_index()
    if seq not in meta: return fail(f'找不到申報表編號 {seq!r}。編號可在網站明細頁的「回報錯誤」連結或中選會 PDF 檔名找到。'), None
    section = token(f.get('section'), SECTIONS)
    if not section: return fail(f'看不懂「分項」{f.get("section")!r}。可填：{"、".join(SECTIONS)}。'), None
    fs = (f.get('field') or '').strip()
    if section == 'form':
        field = token(fs, FORM_FIELDS) or (fs if fs in C.FORM_FIELDS else None)
    else:
        field = next((fn(section) for k, fn in FIELDS.items() if k in fs), None) or (fs if re.fullmatch(r'[a-z_]+', fs) else None)
    if not field: return fail(f'看不懂「欄位」{fs!r}。'), None
    model = S.Declaration if section == 'form' else (C.EXTRA_SECTIONS.get(section) or C.MAIN_SECTIONS.get(section))
    if model is not None and field not in model.model_fields: return fail(f'「{SECTIONS_ZH.get(section, section)}」沒有欄位「{fs}」。'), None
    page = one_int(f.get('page'))
    if page is None: return fail('「原表頁碼」請只填一個數字，審查者需要對照原始申報表。'), None
    path = os.path.join(paths.CORR, year, f'{seq}.yaml')
    existing = C.load_file(path) if os.path.exists(path) else None
    dd, rec, _ = D.pick(seq, year, existing.base if existing else None)
    if rec is None: return fail(f'{year} 年沒有編號 {seq} 的轉錄資料。'), None
    extra = D.load_extra(seq, rec['data'], year)
    if existing:
        errs = [m for _, s, m in C.apply(existing, rec['data'], extra) if s != 'ok']
        if errs: return fail('這份申報表既有的更正檔目前無法套用，請維護者先處理：' + '；'.join(errs)), None
    new = value(f.get('correct'), model, field)
    current = value(f.get('current'), model, field)
    change = {'target': section}
    warn = []
    if section == 'form':
        old = rec['data'].get(field)
        change.update(field=field, old=old, new=new)
    else:
        rows = extra[section] if (section in C.EXTRA_SECTIONS and extra is not None) else rec['data'][section]
        r = (f.get('row') or '').strip()
        if r:
            row = one_int(r)
            if row is None: return fail(f'「列號」請只填一個數字（從 0 起算），收到 {r!r}。'), None
            if row >= len(rows): return fail(f'{section} 只有 {len(rows)} 列，沒有第 {row} 列（列號從 0 起算）。'), None
        else:
            hits = [i for i, x in enumerate(rows) if C._same(x.get(field), current)]
            if len(hits) != 1: return fail(f'沒有填列號，而 {section} 中「{field}」等於 {current!r} 的列有 {len(hits)} 列，無法判斷要改哪一列。請補上列號。'), None
            row = hits[0]
        item = rows[row]
        old = item.get(field)
        idk = next((k for k in ID_KEYS if item.get(k)), None)
        change.update(row=row, match={idk: item[idk]} if idk else {}, field=field, old=old, new=new)
    if current not in ('', None) and not C._same(old, current):
        warn.append(f'回報者寫的「目前的值」是 {current!r}，資料中實際是 {old!r}；更正檔以實際值為準。')
    change.update(page=page, reason=(f.get('evidence') or '').strip().splitlines()[0][:120] if f.get('evidence') else '見 issue', source=f'#{number} @{user}')
    doc = yaml.safe_load(open(path, encoding='utf-8')) if existing else {
        'year': year, 'seq': seq, 'name': meta[seq]['NAME'], 'base': dd, 'changes': []}
    doc.setdefault('changes', []).append(change)
    return {'ok': True, 'path': os.path.relpath(path, paths.ROOT), 'warnings': warn,
            'title': f'更正 {year} 年 {meta[seq]["NAME"]}（{seq}）{section}.{field}', 'year': year, 'seq': seq}, doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--event'); ap.add_argument('--body-file'); ap.add_argument('--number', default='0'); ap.add_argument('--user', default='unknown')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    if a.event:
        ev = json.load(open(a.event, encoding='utf-8'))
        body, number, user = ev['issue'].get('body') or '', ev['issue']['number'], ev['issue']['user']['login']
    else:
        body, number, user = open(a.body_file, encoding='utf-8').read(), a.number, a.user
    try:
        res, doc = build_change(parse_form(body), number, user)
    except Exception as e:  # always leave a result for the workflow to report
        res, doc = fail(f'轉換時發生錯誤，請維護者處理：{type(e).__name__}: {e}'), None
    if res['ok']:
        full = os.path.join(paths.ROOT, res['path'])
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w', encoding='utf-8') as fh:
            yaml.safe_dump(doc, fh, allow_unicode=True, sort_keys=False, width=200)
        C.load_file(full)  # must still be a valid correction file
    json.dump(res, open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False))
    sys.exit(0 if res['ok'] else 2)


if __name__ == '__main__':
    main()
