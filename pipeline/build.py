"""Build everything that is published from the transcriptions + corrections:

  dist/index.html, dist/data.json, dist/detail/*.json   the website (GitHub Pages)
  dist/download/candidate_wealth.sqlite                  the database
  dist/download/csv/*.csv                                one CSV per table
  dist/build_report.json                                 corrections applied, problems found

Usage: python pipeline/build.py [--strict] [--out DIR]
--strict exits non-zero when any correction file fails to load or apply (used by CI).
"""
import argparse, csv, json, os, shutil, sqlite3, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths
import fx as FX
import corrections as C
import declaration as D
from pricing import Pricer, num
from realestate import Estimator, LEVELS
from lvr import BIG
from lvr import sec0
try: import sections as S                                   # untracked until the geo work is committed: build must still run without it
except ImportError: S = None

LVR_COLS = ['lvr_level', 'lvr_key', 'lvr_n', 'unit_p25', 'unit_median', 'unit_p75', 'est_twd', 'est_low', 'est_high', 'flag']
DETAIL = ['stocks', 'funds', 'land', 'bldg', 'deposits', 'cash_items', 'debts', 'biz', 'other', 'cars', 'ships', 'aircraft', 'bonds']


def seqs_for(year):
    out = set()
    for dd in paths.YEARS[year]['main']:
        p = os.path.join(paths.TRANS, dd)
        if os.path.isdir(p): out |= {f[:-5] for f in os.listdir(p) if f[:-5].isdigit() and f.endswith('.json')}
    return sorted(out)


class Tables:
    def __init__(self): self.t = {}
    def add(self, _table, **row): self.t.setdefault(_table, []).append(row)


def db_rows(T, year, seq, meta, rec, agree, extra, site, dd, cf, source):
    d = rec['data']; R = lambda o: D.role(o, d)
    key = dict(year=int(year), seq=seq)
    T.add('candidates', **key, name=meta['NAME'], elect_type=meta['ElectType_NM'] if year == '115' else None, city=meta['City_ID_NM'],
          district=meta['Dist_NM'] if year == '115' else None, party=(paths.parties_115().get(seq, {}).get('party') if year == '115' else None), party_on_declaration_site=(meta['Party_NM'] or '').strip() if year == '115' else None,
          declarant_as_written=d['declarant'], declare_date=d['declare_date'], spouse=d['spouse'], minor_children=d['minor_children_count'],
          total_pages=d['total_pages'], transcription=dd, transcribed_by=rec['model'], form_inconsistent=int(bool(agree)),
          manual_note=cf.note if cf else None, corrections=len(cf.changes) if cf else 0, source=source)
    for sec, val, cnt in (('cash', d['cash_total_twd'], None), ('deposits', d['deposit_total_twd'], d['deposit_count']),
                          ('securities', d['securities_total_twd'], None), ('stocks', d['stock_total_twd'], d['stock_count']),
                          ('bonds', d['bond_total_twd'], None), ('funds', d['fund_total_twd'], None),
                          ('other_securities', d['other_securities_total_twd'], None), ('other_assets', d['other_assets_total_twd'], None),
                          ('insurance', None, d['insurance_count']), ('debt_claims', d['debt_claims_total_twd'], None),
                          ('debts', d['debts_total_twd'], d['debts_count']), ('business_investments', d['business_investments_total_twd'], None),
                          ('land', None, d['land_count']), ('buildings', None, d['building_count']), ('vehicles', None, (extra or {}).get('vehicle_count'))):
        T.add('declared_totals', **key, section=sec, declared_total_twd=val, declared_count=cnt)
    ST = D.stated_lookup(extra)
    st = lambda s, x: ST.get((s, round(num(x.get('twd'))))) or ST.get((s, round(num(x.get('twd'))) + 1)) or ST.get((s, round(num(x.get('twd'))) - 1))
    for tname, items in (('deposits', d['deposits']), ('cash', d['cash'])):
        for i, x in enumerate(items):
            f = FX.normalise(x, x.get('foreign_amount'), year, st(tname, x))
            T.add(tname, **key, idx=i, institution=x.get('bank'), kind=x.get('kind'), owner=x['owner'], owner_role=R(x['owner']),
                  **{k: f[k] for k in ('currency_raw', 'currency', 'currency_source', 'foreign_amount', 'declared_rate', 'rate_source', 'declared_twd')},
                  declared_twd_filled=f.get('declared_twd_filled'), std_rate=f['std_rate'], std_date=f['std_date'], std_twd=f['std_twd'])
    for kind, sec, raw, priced in (('股票', 'stocks', d['stocks'], site['stocks']), ('基金', 'funds', d['funds'], site['funds'])):
        for i, (x, p) in enumerate(zip(raw, priced)):
            fxi = p.get('fx') or {}
            T.add('securities', **key, section=sec, idx=i, kind=kind, name=x['name'], code_as_written=x.get('code'), owner=x['owner'], owner_role=R(x['owner']),
                  units=x.get('shares', x.get('units')), unit_price=x.get('par'), currency_raw=x.get('currency'), currency=fxi.get('currency', 'TWD'),
                  currency_source=fxi.get('currency_source'), foreign_amount=fxi.get('foreign_amount'), declared_rate=fxi.get('declared_rate'),
                  rate_source=fxi.get('rate_source'), std_rate=fxi.get('std_rate'), declared_twd=x.get('twd'),
                  matched_code=p.get('code') if p.get('priced') else None, close_price=p.get('close') if p.get('priced') else None,
                  units_used=p.get('units_used'), market_twd=p['market'], suspect=int(p['suspect']), note=p.get('flag'))
    for kind, sec, items in (('債券', 'bonds', d['bonds']), ('其他有價證券', 'other_securities', d['other_securities'])):
        for i, x in enumerate(items):
            f = FX.normalise(x, None, year, None)
            T.add('securities', **key, section=sec, idx=i, kind=kind, name=x['name'], code_as_written=x.get('code'), owner=x['owner'], owner_role=R(x['owner']),
                  units=x.get('units'), unit_price=x.get('par'), currency_raw=x.get('currency'), currency=f['currency'], currency_source=f['currency_source'],
                  foreign_amount=f['foreign_amount'], declared_rate=f['declared_rate'], rate_source=f['rate_source'],
                  std_rate=f['std_rate'] if f['currency'] != 'TWD' else None, declared_twd=x.get('twd'), matched_code=None, close_price=None,
                  units_used=None, market_twd=f['std_twd'], suspect=0, note=None)
    for tname, items, ests in (('land', d['land'], site['_re']['land']), ('buildings', d['buildings'], site['_re']['bldg'])):
        for i, (x, v) in enumerate(zip(items, ests)):
            fr = D.frac(x['share'])
            T.add(tname, **key, idx=i, location=x['location'], area_m2=x['area_m2'], share=x['share'], share_fraction=fr,
                  owned_area_m2=round((x['area_m2'] or 0) * fr, 4) if fr else None, owner=x['owner'], owner_role=R(x['owner']),
                  acquired=x['acquired'], reason=x['reason'], price_as_written=x['price'], **{k: v.get(k) for k in LVR_COLS},
                  **({'in_building': int(v['in_building'])} if tname == 'land' else {}), counted=int(bool(v.get('counted'))))
    for i, v in enumerate((extra or {}).get('vehicles', [])):
        T.add('vehicles', **key, idx=i, brand_model=v['brand_model'], brand=v.get('brand'), displacement_cc=v.get('displacement_cc'),
              displacement_as_written=v.get('displacement_raw'), plate=v.get('plate'), owner=v['owner'], owner_role=R(v['owner']),
              acquired=v.get('acquired'), reason=v.get('reason'), price_as_written=v.get('price_raw'), price_twd=v.get('price_twd'), note=v.get('note'))
    for i, v in enumerate((extra or {}).get('ships', [])):
        T.add('ships', **key, idx=i, **{k: v.get(k) for k in ('kind', 'size', 'port', 'owner', 'acquired', 'reason', 'price_raw', 'price_twd')}, owner_role=R(v['owner']))
    for i, v in enumerate((extra or {}).get('aircraft', [])):
        T.add('aircraft', **key, idx=i, **{k: v.get(k) for k in ('model', 'maker', 'registration', 'owner', 'acquired', 'reason', 'price_raw', 'price_twd')}, owner_role=R(v['owner']))
    for tname, items in (('insurance', d['insurance']), ('debt_claims', d['debt_claims']), ('debts', d['debts']),
                         ('business_investments', d['business_investments']), ('other_assets', d['other_assets'])):
        for i, x in enumerate(items):
            T.add(tname, **key, idx=i, description=x['desc'], owner=x['owner'], owner_role=R(x['owner']), amount_twd=x['amount_twd'], note=x['note'],
                  **({'purpose': site['debts'][i]['purpose']} if tname == 'debts' else {}))
    for i in site['issues']:
        T.add('validation_issues', **key, section=i['sec'], kind=i['kind'], items_value=i['items'], declared_value=i['declared'])
    s = site['sum']
    T.add('summary', **key, cash_declared=s['cash'], cash_std=s['cash_std'], deposits_declared=s['deposit'], deposits_std=s['deposit_std'],
          stocks_declared=s['stock_decl'], stocks_market=s['stock_mkt'], funds_declared=s['fund_decl'], funds_market=s['fund_mkt'], bonds=s['bond'],
          other_securities=s['other_sec'], other_assets=s['other_assets'], debt_claims=s['claims'], business_investments=s['biz'], debts=s['debt'],
          net_worth_declared=s['net_decl'], net_worth_market=s['net_mkt'], land_count=s['land_n'], land_owned_m2=s['land_area'],
          building_count=s['bldg_n'], building_owned_m2=s['bldg_area'], vehicle_count=s['car_n'], insurance_count=s['ins_n'],
          foreign_currency_items=s['fx_n'], price_date=paths.YEARS[year]['price_date'],
          land_est_twd=s['land_est'], building_est_twd=s['bldg_est'], realestate_est_twd=s['re_est'], realestate_est_low=s['re_lo'], realestate_est_high=s['re_hi'],
          realestate_counted_n=s['re_valued_n'], realestate_flagged_n=s['re_flagged_n'], realestate_unvalued_n=s['re_unvalued_n'], realestate_base_n=s['re_base_n'], mortgage_twd=s['mortgage'], realestate_secured_debt_twd=s['re_secured'],
          lvr_seasons=','.join(paths.YEARS[year]['lvr_seasons']))


def write_db(T, out):
    os.makedirs(os.path.join(out, 'csv'), exist_ok=True)
    db = os.path.join(out, 'candidate_wealth.sqlite')
    if os.path.exists(db): os.remove(db)
    con = sqlite3.connect(db)
    for t, rows in T.t.items():
        cols = list(dict.fromkeys(k for r in rows for k in r))
        con.execute(f'CREATE TABLE "{t}" ({", ".join(chr(34) + c + chr(34) for c in cols)})')
        con.executemany(f'INSERT INTO "{t}" VALUES ({",".join("?" * len(cols))})', [[r.get(c) for c in cols] for r in rows])
        with open(os.path.join(out, 'csv', f'{t}.csv'), 'w', newline='', encoding='utf-8-sig') as fh:
            w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(rows)
    con.commit(); con.close()

GEO_SRC = os.path.join(paths.DATA, 'geo')
COMPANIES = os.path.join(paths.DATA, 'companies')      # written by `python3 -I pipeline/companies.py --out data/companies/` (network; cached)
GEO_INPUTS = [os.path.join(paths.DATA, 'raw', 'sections.json'), os.path.join(GEO_SRC, 'sections'), os.path.join(GEO_SRC, 'towns.geojson'), os.path.join(paths.ROOT, 'site', 'map.html')] + \
             [os.path.join(COMPANIES, t + '.csv') for t in ('companies', 'company_links', 'company_land')]    # any missing: geo step skipped


def sect(code, o, c):
    """(city, town, 段名, member keys) of a section code; a parent code '0101+0102' (resolve match 'parent': the declaration names
    only the 段) is that 段 over all its 小段."""
    ms = [(code[(o, x)], x) for x in c.split('+')]
    city, town, name = ms[0][0]
    return city, town, name.split('段')[0] + '段' if '+' in c else name, ['%s|%s|%s%s' % (ci, tw, o, x) for (ci, tw, _), x in ms]


def add_sections(T, ests, meta):
    """land rows -> section_key/office/code via sections.resolve, parsed the way realestate.Estimator.candidate parses them
    (same city-hint from that candidate's land + building rows)."""
    by = {}
    for t in ('land', 'buildings'):
        for r in T.t.get(t, []): by.setdefault((str(r['year']), r['seq']), []).append((t, r))
    for (year, seq), rows in by.items():
        E, city = ests[year], meta[seq]['City_ID_NM']
        hint = {(p['city'], p['town']) for p in (E.parse(r['location'], city) for _, r in rows) if p['town'] and 'section' not in p['how']}
        for t, r in rows:
            if t != 'land': continue
            p = E.parse(r['location'], city, hint); h = S.resolve(p['city'], p['town'], p['sec'], p['sec0'])
            r.update(section_key=h[2] if h else None, section_office=h[0] if h else None, section_code=h[1] if h else None,
                     section_match=(h[3] if len(h) > 3 else 'exact') if h else None)
            r['_p'] = p


def geo_outputs(T, out):
    """sections + company tables, summary 111->115 land deltas, dist/geo/*.geojson + holdings.json, dist/map.html. Returns report counts.
    115 filings with the same (name, city, district, 申報人) are one person on the map: keyed by the primary filing (has a 申報日期, then most
    pages), land rows deduplicated across filings by (year, location, area_m2, share, acquired, owner)."""
    feats = [f for fn in sorted(os.listdir(os.path.join(GEO_SRC, 'sections'))) if fn.endswith('.geojson')
             for f in paths.load(os.path.join(GEO_SRC, 'sections', fn))['features']]
    geo = {f['properties']['key'] for f in feats}
    for (o, c), (city, town, name) in sorted(S.load_codes()['code'].items()):
        k = '%s|%s|%s%s' % (city, town, o, c)
        T.add('sections', key=k, city=city, town=town, office=o, sectcode=c, name=name, has_geometry=int(k in geo), is_parent=0)
    for t in ('companies', 'company_links', 'company_land'):          # company_links_unverified (同名推論) is never published
        for r in csv.DictReader(open(os.path.join(COMPANIES, t + '.csv'), encoding='utf-8-sig')):
            if t == 'company_land':
                h = S.resolve(r['city'], r['town'], r['sec'], sec0(r['sec'])) if r['sec'] else None; r['section_key'] = h[2] if h else None
            T.add(t, **r)
    code = S.load_codes()['code']; oc = lambda k: (k.split('|')[2][:2], k.split('|')[2][2:]); fk = {f['properties']['key']: f for f in feats}
    for k in sorted({r['section_key'] for r in T.t['land'] + T.t['company_land'] if r['section_key'] and '+' in r['section_key']}):
        city, town, name, members = sect(code, *oc(k)); ok = all(m in fk for m in members)
        T.add('sections', key=k, city=k.split('|')[0], town=k.split('|')[1], office=oc(k)[0], sectcode=oc(k)[1], name=name, has_geometry=int(ok), is_parent=1)
        if ok:                                                    # union of the 小段 = their polygons in one MultiPolygon
            feats.append(dict(type='Feature', properties=dict(fk[members[0]]['properties'], key=k, sectcode=oc(k)[1], name=name, members=members),
                              geometry=dict(type='MultiPolygon', coordinates=[p for m in members for p in fk[m]['geometry']['coordinates']])))
            geo.add(k)
    cand = {r['seq']: r for r in T.t['candidates'] if r['year'] == 115}
    grp, primary, filings = {}, {}, {}
    for r in cand.values():
        d = (r['declarant_as_written'] or r['name']).strip()
        same = len(set(d) & set(r['name'])) >= len(r['name']) - 1   # 楊啟邦/楊啓邦 is one person; a filing under another name (范揚廣 for 洪英雄) is not
        grp.setdefault((r['name'], r['city'], r['district'], '' if same else r['seq']), []).append(r)
    for rs in grp.values():
        rs = sorted(rs, key=lambda r: (not (r['declare_date'] or '').strip(), -(r['total_pages'] or 0), r['seq']))
        for r in rs: primary[r['seq']] = rs[0]['seq']
        if len(rs) > 1: filings[rs[0]['seq']] = [r['seq'] for r in rs]
    matched = {primary.get(r['seq_115'], r['seq_115']) for r in T.t.get('match_111_115', [])}
    held = {}                                                     # (primary seq, group) -> {year: [land rows]}
    for r in T.t['land']:
        p = r.pop('_p'); g = r['section_key'] or ('|'.join((p['city'] or '', p['town'] or '', p['sec'] or '')))
        h = held.setdefault((primary.get(r['seq'], r['seq']), g), {'p': p, 115: [], 111: [], 'from': {}})
        d = (r['year'], r['location'], r['area_m2'], r['share'], r['acquired'], r['owner'])
        if h['from'].setdefault(d, r['seq']) == r['seq']: h[r['year']].append(r)    # same row in another filing: counted once
    area = lambda rows: round(sum(x['owned_area_m2'] or 0 for x in rows), 2)
    status = lambda seq, h: None if seq not in matched else 'kept' if h[115] and h[111] else 'new' if h[115] else 'lost'
    for r in T.t['summary']:
        ps = primary.get(r['seq'], r['seq'])
        if r['year'] != 115 or ps not in matched: continue
        hs = [h for (seq, g), h in held.items() if seq == ps]
        keyed = [(h, status(ps, h)) for h in hs if (h[115] or h[111])[0]['section_key']]
        r.update(land_area_delta_m2=round(sum(area(h[115]) - area(h[111]) for h in hs), 2),
                 sections_gained=sum(s == 'new' for _, s in keyed), sections_lost=sum(s == 'lost' for _, s in keyed))
    holdings = {seq: [] for seq in cand if primary[seq] == seq}
    for (seq, g), h in held.items():
        if seq not in cand: continue
        r0 = (h[115] or h[111])[0]; k = r0['section_key']
        city, town, name = sect(code, r0['section_office'], r0['section_code'])[:3] if k else (h['p']['city'], h['p']['town'], h['p']['sec'])
        holdings[seq].append(dict(key=k, city=city, town=town, name=name, has_geometry=k in geo, section_match=r0['section_match'], owned_m2=area(h[115]), owned_m2_111=area(h[111]),
            status=status(seq, h), **({'filings': filings[seq]} if seq in filings else {}),
            rows=[{f: x[f] for f in ('year', 'idx', 'location', 'area_m2', 'share', 'owned_area_m2', 'owner', 'owner_role', 'acquired', 'reason')}
                  for x in h[115] + h[111]]))
    renames = 0
    for seq, hs in holdings.items():                              # same town, lost and new 段 with equal (rows, area): likely a 段 rename
        tw = {}
        for h in hs:
            if h['status'] in ('new', 'lost'): tw.setdefault((h['city'], h['town']), {'new': [], 'lost': []})[h['status']].append(h)
        sig = lambda xs: (sum(len(h['rows']) for h in xs), round(sum(x['area_m2'] or 0 for h in xs for x in h['rows']), 2))
        for t in tw.values():
            if t['new'] and t['lost'] and sig(t['new']) == sig(t['lost']):
                for h in t['new'] + t['lost']: h['rename_suspect'] = True; renames += 1
    links = {}
    for l in T.t['company_links']:
        links.setdefault(l['company_no'] or l['stock_code'], {}).setdefault(primary.get(l['seq'], l['seq']), []).append(l)
    for cl in T.t['company_land']:
        for seq, ls in links.get(cl['company_no'] or cl['stock_code'], {}).items():
            if seq not in cand: continue
            k = cl['section_key']; city, town, name = sect(code, *oc(k))[:3] if k else (cl['city'], cl['town'], cl['sec'])
            holdings[seq].append(dict(key=k, city=city, town=town, name=name, has_geometry=k in geo, indirect=True, company=cl['company'],
                company_no=cl['company_no'], land_text=cl['land_text'], source=cl['source'], date=cl['date'], source_url=cl['source_url'],
                evidence=sorted({(l['person'], l['role'], l['title'], l['evidence'], l['source_url']) for l in ls})))
    os.makedirs(os.path.join(out, 'geo'), exist_ok=True)
    by_key = {}
    for seq, hs in holdings.items():
        for h in hs:
            if h['key']: by_key.setdefault(h['key'], []).append((seq, h))
    keep = []
    for f in feats:
        hs = by_key.get(f['properties']['key'])
        if not hs: continue
        direct = [(seq, h) for seq, h in hs if not h.get('indirect')]
        y115 = [(s, h) for s, h in direct if any(x['year'] == 115 for x in h['rows'])]
        f = dict(f, properties=dict(f['properties'], n_115=len(y115), n_new=sum(h['status'] == 'new' for _, h in direct),
            n_lost=sum(h['status'] == 'lost' for _, h in direct),
            owned_m2=round(sum(h['owned_m2'] for _, h in direct), 2), indirect=int(any(h.get('indirect') for _, h in hs)),
            candidates=[dict(seq=s, name=cand[s]['name'], party=cand[s]['party'], owned_m2=h['owned_m2'], status=h['status']) for s, h in direct],
            companies=[dict(company=h['company'], seq=s) for s, h in hs if h.get('indirect')]))
        keep.append(f)
    gj = os.path.join(out, 'geo', 'sections_held.geojson')
    json.dump(dict(type='FeatureCollection', features=keep), open(gj, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    shutil.copy(os.path.join(GEO_SRC, 'towns.geojson'), os.path.join(out, 'geo', 'towns.geojson'))
    hj = os.path.join(out, 'geo', 'holdings.json')
    json.dump({s: h for s, h in holdings.items() if h}, open(hj, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    page = '\n'.join(l for l in open(os.path.join(paths.ROOT, 'site', 'map.html'), encoding='utf-8').read().splitlines() if 'name="claude:' not in l)
    open(os.path.join(out, 'map.html'), 'w', encoding='utf-8').write('<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"></head><body>\n' + page + '\n</body></html>\n')
    land = T.t['land']; keyed = {r['section_key'] for r in land if r['section_key']}; sm = {}
    for r in land: sm[r['section_match'] or 'none'] = sm.get(r['section_match'] or 'none', 0) + 1
    check = S.check_summary() if hasattr(S, 'check_summary') else {fn[7:-5]: dict(points=c['points'], agree=c['agree'], ratio=c['rate'])
             for fn in sorted(os.listdir(GEO_SRC)) if fn.startswith('report_') for c in [paths.load(os.path.join(GEO_SRC, fn)).get('check')] if c}
    return dict(land_rows=len(land), land_with_section=sum(1 for r in land if r['section_key']),
                land_with_section_pct=round(100 * sum(1 for r in land if r['section_key']) / max(1, len(land)), 1),
                sections_with_geometry=len(geo), sections_held=len(keyed), sections_held_with_geometry=len(keyed & geo),
                company_links=len(T.t['company_links']), company_land=len(T.t['company_land']),
                company_land_with_section=sum(1 for r in T.t['company_land'] if r['section_key']),
                holdings_people=len([s for s, h in holdings.items() if h]), duplicate_filing_groups=len(filings), rename_suspect_entries=renames,
                section_match={k: dict(rows=v, ratio=round(v / max(1, len(land)), 4)) for k, v in sorted(sm.items())}, check=check,
                bytes={os.path.basename(p): os.path.getsize(p) for p in (gj, hj, os.path.join(out, 'geo', 'towns.geojson'))})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--strict', action='store_true'); ap.add_argument('--out', default=paths.DIST)
    ap.add_argument('--no-db', action='store_true')
    a = ap.parse_args()
    meta = paths.candidates_index(); match = paths.match_111(); groups = paths.groups_111()
    corr, load_errors = C.load_all()
    report = {'correction_files': len(corr), 'load_errors': load_errors, 'applied': [], 'apply_errors': [], 'missing': []}
    T = Tables(); site = {'115': {}, '111': {}}; seen = set()
    ests = {}
    for year in ('115', '111'):
        pricer, est = Pricer(year), Estimator(year)
        ests[year] = est
        for seq in seqs_for(year):
            if seq not in meta: report['missing'].append(f'{year}/{seq}: not in candidates_index'); continue
            cf = corr.get((year, seq)); seen.add((year, seq))
            try:
                dd, rec, agree = D.pick(seq, year, cf.base if cf else None)
            except ValueError as e:
                load_errors.append((f'corrections/{year}/{seq}.yaml', str(e))); cf = None
                dd, rec, agree = D.pick(seq, year)
            extra = D.load_extra(seq, rec['data'], year)
            if cf:
                for i, status, msg in C.apply(cf, rec['data'], extra):
                    (report['applied'] if status == 'ok' else report['apply_errors']).append(f'{year}/{seq} #{i}: {msg}')
                if cf.form_inconsistent is not None: agree = cf.form_inconsistent
            c = D.summarize(rec, meta[seq], year, extra, pricer, est)
            c.update(model=rec['model'], transcription=dd, form_inconsistent=bool(agree), year=year)
            if cf and cf.note: c['manual_note'] = cf.note
            if cf: c['corrections'] = len(cf.changes)
            source = paths.YEARS[year]['pdf_url'].format(seq=seq) if year == '115' else f"https://web.archive.org/web/{match[seq]['ts']}id_/{match[seq]['url']}"
            db_rows(T, year, seq, meta[seq], rec, agree, extra, c, dd, cf, source)
            if year == '111':
                T.add('match_111_115', seq_115=seq, path_111='/'.join(groups[seq]['paths']), files=len(groups[seq]['files']), rule='same name + same city')
            site[year][seq] = c
    for (year, seq) in sorted(set(corr) - seen):
        load_errors.append((f'corrections/{year}/{seq}.yaml', f'{year} 年沒有編號 {seq} 的轉錄，這個更正檔不會被套用'))
    for dt, cur in FX.BOT.items():
        for code, r in cur.items(): T.add('fx_rates', date=dt, currency=code, **r, source='臺灣銀行牌告匯率')
    for year, cfg in paths.YEARS.items():
        for code, v in paths.load(os.path.join(paths.PRICES, cfg['prices'])).items():
            T.add('stock_prices', date=cfg['price_date'], code=code, name=v['name'], close=v['close'], market=v['market'], source=v['src'])
        lvr = paths.load(os.path.join(paths.PRICES, cfg['lvr_file'])); lvl = {c: n for kk in LEVELS.values() for c, _, n in kk}
        for kind in ('land', 'house'):
            for k, (n, lo, med, hi) in lvr[kind].items():
                T.add('lvr_unit_prices', year=int(year), kind=kind, level=lvl[k.split('|')[0]], key=k, n=n, p25=lo, median=med, p75=hi)

    missing = [os.path.relpath(p, paths.ROOT) for p in GEO_INPUTS if not os.path.exists(p)]
    if missing or S is None:
        print('WARNING geo step skipped (no map, section_* left empty); missing:', ', '.join(missing))
        for r in T.t.get('land', []): r.update(section_key=None, section_office=None, section_code=None, section_match=None)
    else: add_sections(T, ests, meta)
    out = a.out
    if os.path.isdir(os.path.join(out, 'detail')): shutil.rmtree(os.path.join(out, 'detail'))
    os.makedirs(os.path.join(out, 'detail'), exist_ok=True)
    cities = sorted({c['city'] for c in site['115'].values()}); cid = {c: f'{i:02d}' for i, c in enumerate(cities)}
    shards, summary = {}, []
    for seq, c in site['115'].items():
        c = dict(c); det = {k: c.pop(k) for k in DETAIL}; c.pop('notes', None); c.pop('_re', None)
        p = site['111'].get(seq)
        if p:
            det['prev_stocks'] = [{k: s.get(k) for k in ('name', 'code', 'role', 'units', 'declared', 'close', 'market', 'priced')} for s in p['stocks'] + p['funds']]
            c['prev'] = {'sum': p['sum'], 'date': p['date'], 'folder': groups[seq]['folder'], 'n_files': len(groups[seq]['files']),
                         'issues': p['issues'], 'form_inconsistent': p['form_inconsistent'], 'transcription': p['transcription'],
                         'manual_note': p.get('manual_note')}
        c['id'] = seq; c['shard'] = cid[c['city']]
        shards.setdefault(c['shard'], {})[seq] = det; summary.append(c)
    for k, v in shards.items():
        json.dump(v, open(os.path.join(out, 'detail', f'{k}.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    json.dump(summary, open(os.path.join(out, 'data.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    page = open(os.path.join(paths.ROOT, 'site', 'index.html'), encoding='utf-8').read()
    page = '\n'.join(l for l in page.splitlines() if 'name="claude:' not in l)
    cfg = json.dumps({'repo': paths.CONFIG['repo'], 'download': True, 'big': BIG, 'lvr': {y: v['lvr_seasons'] for y, v in paths.YEARS.items()}}, ensure_ascii=False)
    open(os.path.join(out, 'index.html'), 'w', encoding='utf-8').write(
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
        f'<script>window.SITE_CONFIG={cfg}</script></head><body>\n' + page + '\n</body></html>\n')
    report['geo'] = {'skipped': True, 'missing': missing} if missing else geo_outputs(T, out)
    if not a.no_db: write_db(T, os.path.join(out, 'download'))
    report['counts'] = {t: len(r) for t, r in T.t.items()}
    json.dump(report, open(os.path.join(out, 'build_report.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f"115: {len(site['115'])}  111: {len(site['111'])}  corrections: {len(corr)} files, {len(report['applied'])} changes applied")
    for e in load_errors + report['apply_errors']: print('ERROR', e)
    if a.strict and (load_errors or report['apply_errors']): sys.exit(1)


if __name__ == '__main__':
    main()
