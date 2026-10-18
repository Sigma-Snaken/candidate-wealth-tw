"""Turn one transcribed declaration into the site/database record: declared vs uniform-FX/market values."""
import json, os, re, sys, copy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths
import fx as FX
from pricing import num
from validate import check
from debts import purposes
from collections import Counter

PARTIES = paths.parties_115()


def frac(s, detail=False):
    """Share as written -> fraction: a/b, 'N分之M' (denominator first, digits or 一…萬/壹…拾), 全/全部/100%, a trailing 0<x≤1.
    Several fractions with one denominator separated by ,、 or spaces are summed (a repeated full share counts once). A reversed 'N/1' reads as 1/N;
    any other numerator above the denominator is None. detail=True returns (fraction, reversed)."""
    def cnum(x):
        t = u = n = 0
        for c in re.findall(r'\d+(?:\.\d+)?|.', x):
            if c[0] in '0123456789０１２３４５６７８９': n = float(c)
            elif c in '零〇一壹二貳兩三參四肆五伍六陸七柒八捌九玖': n = int('001122233445566778899'['零〇一壹二貳兩三參四肆五伍六陸七柒八捌九玖'.index(c)])
            elif c in '十拾百佰千仟': u += (n or 1) * 10 ** ('十拾百佰千仟'.index(c) // 2 + 1); n = 0
            elif c == '萬': t += (u + n or 1) * 10000; u = n = 0
        return t + u + n
    out = lambda v, rev=False: (v, rev) if detail else v
    if not s: return out(None)
    s = re.sub(r'\s+', ' ', str(s)).strip().replace('／', '/')
    s = re.sub(r'(?<![\d.])\d{1,3}(?:[,，]\d{3})+(?!\d)', lambda m: m.group() if s[:m.start()].rstrip().endswith('/') and re.match(r' ?/', s[m.end():])
               else re.sub('[,，]', '', m.group()), s)                     # 1/2,100/1000: a denominator then a list comma, not 2100
    N = r'([\d.０-９零〇一二三四五六七八九壹貳參肆伍陸柒捌玖兩十拾百佰千仟萬]+)'
    ms = list(re.finditer(N + ' ?(?:分之|之|分) ?' + N + r'|(\d+(?:\.\d+)?) ?/ ?(\d+(?:\.\d+)?)', s))
    if ms:
        nd = [(cnum(m.group(2)), cnum(m.group(1))) if m.group(1) else (float(m.group(3)), float(m.group(4))) for m in ms]
        (n, d), k = nd[0], 1
        while k < len(ms) and nd[k][1] == d and re.fullmatch(r'[ ,，、]+', s[ms[k - 1].end():ms[k].start()]): n += nd[k][0]; k += 1
        if n > d and all(nd[j] == (d, d) for j in range(k)): n = d                 # 1/1,1/1: the full share repeated
    elif m := re.search(r'(\d+(?:\.\d+)?)[%％]', s): n, d = float(m.group(1)), 100
    elif '全' in s: return out(1.0)
    elif (m := re.search(r'(?:^|[^\d./])(\d*\.?\d+)$', s)) and 0 < float(m.group(1)) <= 1: return out(float(m.group(1)))
    else: return out(None)
    if not (n > 0 and d > 0) or n > d and d != 1: return out(None)
    return out(1 / n, True) if n > d else out(n / d)

def role(owner, d):
    o = (owner or '').replace(' ', '')
    if not o: return '未註明'
    if o == (d.get('declarant') or '').replace(' ', ''): return '本人'
    if d.get('spouse') and o == d['spouse'].replace(' ', ''): return '配偶'
    if '配偶' in o: return '配偶'
    if '本人' in o: return '本人'
    return '子女'

SEC_MAP = {'股票': 'stocks', '基金': 'funds', '存款': 'deposits', '現金': 'cash', '債券': 'bonds', '其他有價證券': 'other_securities'}

def stated_lookup(extra):
    out = {}
    for r in (extra or {}).get('fx_rows', []):
        if r.get('stated_rate') and r.get('twd') is not None:
            out[(SEC_MAP.get(r['section'], r['section']), round(r['twd']))] = r['stated_rate']
    return out

def extra_score(e, d):
    n = 0
    if e['vehicle_count'] is not None and e['vehicle_count'] != len(e['vehicles']): n += 1
    main_fx = [x for x in d['deposits'] + d['cash'] if FX.iso(x.get('currency')) not in (None, 'TWD') and x.get('twd')]
    tw = [round(r['twd']) for r in e['fx_rows'] if r.get('twd') is not None]
    n += sum(1 for x in main_fx if not any(abs(round(x['twd']) - t) <= 1 for t in tw))
    return n

def load_extra(seq, d, year):
    best = None
    for i, dd in enumerate(paths.YEARS[year]['extra']):
        f = os.path.join(paths.TRANS, dd, f'{seq}.json')
        if os.path.exists(f):
            e = json.load(open(f, encoding='utf-8'))['data']; sc = (extra_score(e, d), -i)
            if best is None or sc < best[0]: best = (sc, e)
    return best[1] if best else None

def summarize(rec, meta, year, extra, pricer, est=None):
    """est: a realestate.Estimator for the year (None skips the 實價登錄 estimates)."""
    d = rec['data']; R = lambda o: role(o, d)
    ST = stated_lookup(extra)
    st = lambda sec, x: ST.get((sec, round(num(x.get('twd'))))) or ST.get((sec, round(num(x.get('twd'))) + 1)) or ST.get((sec, round(num(x.get('twd'))) - 1))
    stocks = [dict(pricer.price(s, 'shares', st('stocks', s)), role=R(s['owner'])) for s in d['stocks']]
    funds = [dict(pricer.price(f, 'units', st('funds', f)), role=R(f['owner'])) for f in d['funds']]
    deps = [dict(x, **FX.normalise(x, x.get('foreign_amount'), year, st('deposits', x))) for x in d['deposits']]
    cashes = [dict(x, **FX.normalise(x, x.get('foreign_amount'), year, st('cash', x))) for x in d['cash']]
    stdsum = lambda xs: round(sum(num(x['std_twd']) if x['std_twd'] is not None else num(x.get('declared_twd_filled')) for x in xs), 2)
    for i, x in enumerate(stocks): x.pop('owner', None); x['i'] = i; x['sec'] = 'stocks'
    for i, x in enumerate(funds): x.pop('owner', None); x['i'] = i; x['sec'] = 'funds'
    tot = lambda items, k='twd': round(sum(num(i.get(k)) for i in items), 2)
    cash = tot(d['cash']); dep = tot(d['deposits'])
    stock_decl = round(sum(s['declared'] for s in stocks), 2); stock_mkt = round(sum(s['market'] for s in stocks), 2)
    fund_decl = round(sum(f['declared'] for f in funds), 2); fund_mkt = round(sum(f['market'] for f in funds), 2)
    bonds = tot(d['bonds']); other_sec = tot(d['other_securities'])
    other_assets = tot(d['other_assets'], 'amount_twd'); claims = tot(d['debt_claims'], 'amount_twd')
    biz = tot(d['business_investments'], 'amount_twd'); debts = tot(d['debts'], 'amount_twd')
    assets_decl = cash + dep + stock_decl + fund_decl + bonds + other_sec + other_assets + claims + biz
    cash_std, dep_std = stdsum(cashes), stdsum(deps)
    bonds_std = round(sum(num(FX.normalise(x, None, year)['std_twd']) for x in d['bonds'] + d['other_securities']), 2)
    assets_mkt = cash_std + dep_std + bonds_std - bonds - other_sec + stock_mkt + fund_mkt + bonds + other_sec + other_assets + claims + biz
    land = [{'loc': l['location'][:20], 'area': l['area_m2'], 'share': l['share'], 'own_area': round((l['area_m2'] or 0) * (frac(l['share']) or 0), 2) if frac(l['share']) else None, 'role': R(l['owner']), 'acq': l['acquired'], 'reason': l['reason'], 'i': i} for i, l in enumerate(d['land'])]
    bldg = [{'loc': b['location'][:20], 'area': b['area_m2'], 'share': b['share'], 'own_area': round((b['area_m2'] or 0) * (frac(b['share']) or 0), 2) if frac(b['share']) else None, 'role': R(b['owner']), 'acq': b['acquired'], 'reason': b['reason'], 'i': i} for i, b in enumerate(d['buildings'])]
    lv, bv = est.candidate(d['land'], d['buildings'], meta['City_ID_NM']) if est else ([{} for _ in land], [{} for _ in bldg])
    for x, v in zip(land + bldg, lv + bv):
        x.update(est=v.get('est_twd'), lo=v.get('est_low'), hi=v.get('est_high'), unit=v.get('unit_median'), lvl=v.get('lvr_level'), n=v.get('lvr_n'),
                 src=''.join(v['lvr_key'].split('|')[2:]) if v.get('lvr_key') else None,
                 flag=v.get('flag'), cnt=int(bool(v.get('counted'))))
    ct = [x for x in land + bldg if x['cnt']]
    re_sum = lambda xs, k: round(sum(x[k] or 0 for x in xs))
    purp = purposes([(x['desc'], x['note']) for x in d['debts']])
    dsum = lambda p: round(sum(num(x['amount_twd']) for x, q in zip(d['debts'], purp) if q == p), 2)
    by_role = {}
    def addr(r, k, v):
        by_role.setdefault(r, {}); by_role[r][k] = round(by_role[r].get(k, 0) + v, 2)
    for x in d['deposits']: addr(R(x['owner']), 'deposit', num(x['twd']))
    for x in d['cash']: addr(R(x['owner']), 'cash', num(x['twd']))
    for x in stocks + funds: addr(x['role'], 'sec_market', x['market'])
    for x in d['debts']: addr(R(x['owner']), 'debt', num(x['amount_twd']))
    issues = [{'sec': i[0], 'kind': i[1], 'items': i[2], 'declared': i[3]} for i in check(rec)]
    return {
        'id': rec['seq'], 'name': meta['NAME'], 'type': meta['ElectType_NM'], 'city': meta['City_ID_NM'], 'dist': meta['Dist_NM'],
        'party': (PARTIES.get(rec['seq'], {}).get('party', (meta['Party_NM'] or '').strip()) or '無黨籍'), 'pdf': paths.YEARS[year]['pdf_url'].format(seq=rec['seq']) if paths.YEARS[year]['pdf_url'] else None,
        'date': d['declare_date'], 'has_spouse': bool(d['spouse']), 'kids': d['minor_children_count'],
        'sum': {'cash': cash, 'deposit': dep, 'cash_std': cash_std, 'deposit_std': dep_std, 'fx_n': sum(1 for x in deps + cashes if x['currency'] != 'TWD') + sum(1 for x in stocks + funds if x.get('fx')), 'stock_decl': stock_decl, 'stock_mkt': stock_mkt, 'fund_decl': fund_decl, 'fund_mkt': fund_mkt,
                'bond': bonds, 'other_sec': other_sec, 'other_assets': other_assets, 'claims': claims, 'biz': biz, 'debt': debts,
                'net_decl': round(assets_decl - debts - sum(x['declared'] for x in stocks + funds if x['suspect']), 2), 'net_mkt': round(assets_mkt - debts, 2),
                'land_n': len(land), 'land_area': round(sum(x['own_area'] or 0 for x in land), 2),
                'bldg_n': len(bldg), 'bldg_area': round(sum(x['own_area'] or 0 for x in bldg), 2),
                'land_est': re_sum([x for x in land if x['cnt']], 'est'), 'bldg_est': re_sum([x for x in bldg if x['cnt']], 'est'), 're_est': re_sum(ct, 'est'), 're_lo': re_sum(ct, 'lo'), 're_hi': re_sum(ct, 'hi'),
                're_valued_n': len(ct), 're_flagged_n': sum(1 for x in land + bldg if x['est'] is not None and not x['cnt']), 're_unvalued_n': sum(1 for x in land + bldg if x['est'] is None), 're_base_n': sum(1 for x in land if x['flag'] == 'base'),
                'mortgage': dsum('房貸'), 're_secured': dsum('不動產擔保週轉'),
                'car_n': len(extra['vehicles']) if extra else len(d['vehicles']), 'car_brands': [[b, n] for b, n in Counter((v.get('brand') or v.get('brand_model') or '').strip() or '未註明' for v in (extra or {}).get('vehicles', [])).most_common()], 'car_cc': round(sum(v.get('displacement_cc') or 0 for v in (extra or {}).get('vehicles', []))), 'ins_n': len(d['insurance']),
                'unpriced_n': sum(1 for x in stocks if not x['priced']), 'suspect_n': sum(1 for x in stocks + funds if x['suspect']), 'suspect_amt': round(sum(x['declared'] for x in stocks + funds if x['suspect']), 2)},
        'declared_totals': {'deposit': d['deposit_total_twd'], 'securities': d['securities_total_twd'], 'debt': d['debts_total_twd']},
        'by_role': by_role, 'stocks': stocks, 'funds': funds, 'land': land, 'bldg': bldg,
        'deposits': [{'bank': x['bank'][:30], 'cur': x['currency'], 'cur_raw': x['currency_raw'], 'fx': x['foreign_amount'], 'twd': x['declared_twd'], 'twd_filled': x.get('declared_twd_filled'),
                      'rate': x['declared_rate'], 'rate_src': x['rate_source'], 'std_rate': x['std_rate'], 'std_twd': x['std_twd'], 'role': R(x['owner']), 'i': i} for i, x in enumerate(deps)],
        'cash_items': [{'cur': x['currency'], 'fx': x['foreign_amount'], 'twd': x['declared_twd'], 'rate': x['declared_rate'], 'std_twd': x['std_twd'], 'role': R(x['owner'])} for x in cashes],
        'debts': [{'desc': (x['desc'] or '')[:40], 'twd': x['amount_twd'], 'role': R(x['owner']), 'purpose': purp[i], 'sec': 'debts', 'i': i} for i, x in enumerate(d['debts'])],
        'biz': [{'desc': (x['desc'] or '')[:40], 'twd': x['amount_twd'], 'role': R(x['owner']), 'sec': 'business_investments', 'i': i} for i, x in enumerate(d['business_investments'])],
        'other': [{'desc': (x['desc'] or '')[:40], 'twd': x['amount_twd'], 'role': R(x['owner']), 'sec': sec, 'i': i} for sec in ('other_assets', 'debt_claims') for i, x in enumerate(d[sec])],
        'cars': ([{'brand': v.get('brand'), 'model': v['brand_model'], 'cc': v.get('displacement_cc'), 'cc_raw': v.get('displacement_raw'), 'role': R(v['owner']),
                   'acq': v.get('acquired'), 'reason': v.get('reason'), 'price': v.get('price_raw'), 'price_twd': v.get('price_twd'), 'i': i} for i, v in enumerate(extra['vehicles'])]
                 if extra else [{'model': (x['desc'] or '')[:30], 'role': R(x['owner']), 'acq': x['acquired']} for x in d['vehicles']]),
        'ships': [{'kind': x['kind'], 'size': x.get('size'), 'role': R(x['owner']), 'acq': x.get('acquired'), 'price': x.get('price_raw')} for x in (extra or {}).get('ships', [])],
        'aircraft': [{'model': x['model'], 'maker': x.get('maker'), 'role': R(x['owner']), 'acq': x.get('acquired'), 'price': x.get('price_raw')} for x in (extra or {}).get('aircraft', [])],
        'bonds': [{'name': x['name'], 'twd': x['twd'], 'role': R(x['owner']), 'sec': sec, 'i': i} for sec in ('bonds', 'other_securities') for i, x in enumerate(d[sec])],
        'issues': issues, 'notes': d['notes'], '_re': {'land': lv, 'bldg': bv},
    }

def real(iss): return [i for i in iss if i[1] != 'no declared total']


def transcriptions(seq, year):
    """All available main-pass transcriptions for a declaration, as (source_dir, record)."""
    out = []
    for dd in paths.YEARS[year]['main']:
        f = os.path.join(paths.TRANS, dd, f'{seq}.json')
        if os.path.exists(f): out.append((dd, paths.load(f)))
    return out


def pick(seq, year, base=None):
    """Choose a transcription: the one a correction file pins (base), else the most arithmetically consistent one.
    Returns (source_dir, record, form_inconsistent) where form_inconsistent means every model read the same mismatch."""
    recs = transcriptions(seq, year)
    if not recs: return None, None, None
    if base:
        for dd, r in recs:
            if dd == base: return dd, copy.deepcopy(r), None
        raise ValueError(f'{year}/{seq}: pinned transcription {base} not found')
    scored = sorted(recs, key=lambda x: (len(real(check(x[1]))), 0 if 'pro' in x[1]['model'] else 1))
    dd, best = scored[0]; agree = None
    if len(recs) > 1 and real(check(best)):
        a, b = [{(i[0], i[1]): (round(i[2]), round(i[3] or 0)) for i in real(check(r))} for _, r in recs[:2]]
        agree = a == b
    return dd, copy.deepcopy(best), agree
