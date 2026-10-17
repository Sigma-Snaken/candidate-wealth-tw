"""Attach the nominating party (推薦之政黨) from the CEC candidate registration roster to each declaration.

The Party field on the property-declaration site is blank for most candidates and sometimes wrong, so the
roster (data/raw/cec_roster_115.json, transcribed from https://web.cec.gov.tw/central/article/64709) is the
authority. Output: data/parties_115.json  {seq: {party, roster_name, match}}.
"""
import difflib, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths

def cjk(s):
    s = (s or '').replace('臺', '台').replace('温', '溫').replace('粱', '梁').replace('啓', '啟').replace('黄', '黃')
    return re.sub(r'[^一-鿿]', '', s)

def main():
    roster = paths.load(os.path.join(paths.RAW, 'cec_roster_115.json'))['rows']
    idx = paths.candidates_index()
    by = {}
    for r in roster: by.setdefault((r['office'], cjk(r['district'])), []).append(r)
    out, miss = {}, []
    for seq, x in sorted(idx.items()):
        pool = by.get((x['ElectType_NM'], cjk(x['Dist_NM'])), [])
        name = cjk(x['NAME'])
        hit = [r for r in pool if cjk(r['name']) == name or cjk(r['name']).startswith(name) and len(name) >= 2]
        how = 'exact'
        if len(hit) != 1:
            sc = sorted(((difflib.SequenceMatcher(None, name, cjk(r['name'])).ratio(), i) for i, r in enumerate(pool)), reverse=True)
            if sc and sc[0][0] >= 0.6 and (len(sc) == 1 or sc[0][0] - sc[1][0] >= 0.2): hit, how = [pool[sc[0][1]]], f'similar {sc[0][0]:.2f}'
            else: miss.append((seq, x['NAME'], x['Dist_NM'])); continue
        p = hit[0]['party'].strip()
        out[seq] = {'party': '' if p == '無' else p, 'roster_name': hit[0]['name'], 'match': how}
    json.dump(out, open(os.path.join(paths.DATA, 'parties_115.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    print(f'{len(out)}/{len(idx)} matched; not in roster: {miss}')

if __name__ == '__main__':
    main()
