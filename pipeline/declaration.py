"""Turn one transcribed declaration into the site/database record: declared vs uniform-FX/market values."""
import json, os, re, sys, copy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths
import fx as FX
from pricing import num
from validate import check
from collections import Counter

PARTIES = paths.parties_115()


def frac(s):
    if not s: return None
    s = str(s).replace(' ', '').replace('／', '/')
    if '全部' in s or s in ('1', '1/1'): return 1.0
    m = re.search(r'(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)', s)
    if m and float(m.group(2)) > 0: return min(1.0, float(m.group(1)) / float(m.group(2)))
    return None

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

def summarize(rec, meta, year, extra, pricer):
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
                'car_n': len(extra['vehicles']) if extra else len(d['vehicles']), 'car_brands': [[b, n] for b, n in Counter((v.get('brand') or v.get('brand_model') or '').strip() or '未註明' for v in (extra or {}).get('vehicles', [])).most_common()], 'car_cc': round(sum(v.get('displacement_cc') or 0 for v in (extra or {}).get('vehicles', []))), 'ins_n': len(d['insurance']),
                'unpriced_n': sum(1 for x in stocks if not x['priced']), 'suspect_n': sum(1 for x in stocks + funds if x['suspect']), 'suspect_amt': round(sum(x['declared'] for x in stocks + funds if x['suspect']), 2)},
        'declared_totals': {'deposit': d['deposit_total_twd'], 'securities': d['securities_total_twd'], 'debt': d['debts_total_twd']},
        'by_role': by_role, 'stocks': stocks, 'funds': funds, 'land': land, 'bldg': bldg,
        'deposits': [{'bank': x['bank'][:30], 'cur': x['currency'], 'cur_raw': x['currency_raw'], 'fx': x['foreign_amount'], 'twd': x['declared_twd'], 'twd_filled': x.get('declared_twd_filled'),
                      'rate': x['declared_rate'], 'rate_src': x['rate_source'], 'std_rate': x['std_rate'], 'std_twd': x['std_twd'], 'role': R(x['owner']), 'i': i} for i, x in enumerate(deps)],
        'cash_items': [{'cur': x['currency'], 'fx': x['foreign_amount'], 'twd': x['declared_twd'], 'rate': x['declared_rate'], 'std_twd': x['std_twd'], 'role': R(x['owner'])} for x in cashes],
        'debts': [{'desc': (x['desc'] or '')[:40], 'twd': x['amount_twd'], 'role': R(x['owner']), 'sec': 'debts', 'i': i} for i, x in enumerate(d['debts'])],
        'biz': [{'desc': (x['desc'] or '')[:40], 'twd': x['amount_twd'], 'role': R(x['owner']), 'sec': 'business_investments', 'i': i} for i, x in enumerate(d['business_investments'])],
        'other': [{'desc': (x['desc'] or '')[:40], 'twd': x['amount_twd'], 'role': R(x['owner']), 'sec': sec, 'i': i} for sec in ('other_assets', 'debt_claims') for i, x in enumerate(d[sec])],
        'cars': ([{'brand': v.get('brand'), 'model': v['brand_model'], 'cc': v.get('displacement_cc'), 'cc_raw': v.get('displacement_raw'), 'role': R(v['owner']),
                   'acq': v.get('acquired'), 'reason': v.get('reason'), 'price': v.get('price_raw'), 'price_twd': v.get('price_twd'), 'i': i} for i, v in enumerate(extra['vehicles'])]
                 if extra else [{'model': (x['desc'] or '')[:30], 'role': R(x['owner']), 'acq': x['acquired']} for x in d['vehicles']]),
        'ships': [{'kind': x['kind'], 'size': x.get('size'), 'role': R(x['owner']), 'acq': x.get('acquired'), 'price': x.get('price_raw')} for x in (extra or {}).get('ships', [])],
        'aircraft': [{'model': x['model'], 'maker': x.get('maker'), 'role': R(x['owner']), 'acq': x.get('acquired'), 'price': x.get('price_raw')} for x in (extra or {}).get('aircraft', [])],
        'bonds': [{'name': x['name'], 'twd': x['twd'], 'role': R(x['owner']), 'sec': sec, 'i': i} for sec in ('bonds', 'other_securities') for i, x in enumerate(d[sec])],
        'issues': issues, 'notes': d['notes'],
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
