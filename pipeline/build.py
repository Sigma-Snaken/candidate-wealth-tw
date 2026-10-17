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
    for tname, items in (('land', d['land']), ('buildings', d['buildings'])):
        for i, x in enumerate(items):
            fr = D.frac(x['share'])
            T.add(tname, **key, idx=i, location=x['location'], area_m2=x['area_m2'], share=x['share'], share_fraction=fr,
                  owned_area_m2=round((x['area_m2'] or 0) * fr, 4) if fr else None, owner=x['owner'], owner_role=R(x['owner']),
                  acquired=x['acquired'], reason=x['reason'], price_as_written=x['price'])
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
            T.add(tname, **key, idx=i, description=x['desc'], owner=x['owner'], owner_role=R(x['owner']), amount_twd=x['amount_twd'], note=x['note'])
    for i in site['issues']:
        T.add('validation_issues', **key, section=i['sec'], kind=i['kind'], items_value=i['items'], declared_value=i['declared'])
    s = site['sum']
    T.add('summary', **key, cash_declared=s['cash'], cash_std=s['cash_std'], deposits_declared=s['deposit'], deposits_std=s['deposit_std'],
          stocks_declared=s['stock_decl'], stocks_market=s['stock_mkt'], funds_declared=s['fund_decl'], funds_market=s['fund_mkt'], bonds=s['bond'],
          other_securities=s['other_sec'], other_assets=s['other_assets'], debt_claims=s['claims'], business_investments=s['biz'], debts=s['debt'],
          net_worth_declared=s['net_decl'], net_worth_market=s['net_mkt'], land_count=s['land_n'], land_owned_m2=s['land_area'],
          building_count=s['bldg_n'], building_owned_m2=s['bldg_area'], vehicle_count=s['car_n'], insurance_count=s['ins_n'],
          foreign_currency_items=s['fx_n'], price_date=paths.YEARS[year]['price_date'])


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--strict', action='store_true'); ap.add_argument('--out', default=paths.DIST)
    ap.add_argument('--no-db', action='store_true')
    a = ap.parse_args()
    meta = paths.candidates_index(); match = paths.match_111(); groups = paths.groups_111()
    corr, load_errors = C.load_all()
    report = {'correction_files': len(corr), 'load_errors': load_errors, 'applied': [], 'apply_errors': [], 'missing': []}
    T = Tables(); site = {'115': {}, '111': {}}; seen = set()
    for year in ('115', '111'):
        pricer = Pricer(year)
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
            c = D.summarize(rec, meta[seq], year, extra, pricer)
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

    out = a.out
    if os.path.isdir(os.path.join(out, 'detail')): shutil.rmtree(os.path.join(out, 'detail'))
    os.makedirs(os.path.join(out, 'detail'), exist_ok=True)
    cities = sorted({c['city'] for c in site['115'].values()}); cid = {c: f'{i:02d}' for i, c in enumerate(cities)}
    shards, summary = {}, []
    for seq, c in site['115'].items():
        c = dict(c); det = {k: c.pop(k) for k in DETAIL}; c.pop('notes', None)
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
    cfg = json.dumps({'repo': paths.CONFIG['repo'], 'download': True}, ensure_ascii=False)
    open(os.path.join(out, 'index.html'), 'w', encoding='utf-8').write(
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
        f'<script>window.SITE_CONFIG={cfg}</script></head><body>\n' + page + '\n</body></html>\n')
    if not a.no_db: write_db(T, os.path.join(out, 'download'))
    report['counts'] = {t: len(r) for t, r in T.t.items()}
    json.dump(report, open(os.path.join(out, 'build_report.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f"115: {len(site['115'])}  111: {len(site['111'])}  corrections: {len(corr)} files, {len(report['applied'])} changes applied")
    for e in load_errors + report['apply_errors']: print('ERROR', e)
    if a.strict and (load_errors or report['apply_errors']): sys.exit(1)


if __name__ == '__main__':
    main()
