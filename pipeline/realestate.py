"""Value declared land/buildings from 實價登錄 unit-price quantiles (data/prices/<lvr_file>, built by lvr.py).

Each row gets a flag; only rows with an estimate and no flag are counted in totals (estimates stay filled for 參考 where computable):
  base          land under a counted building of the same candidate (same 段, or town if the building has no 段, and same acquisition
                date), smallest land first; or the 自建 pass below; each building lends 1.5× its owned area, shared by both passes
  large-land    land with ≥ 1,000 m² owned priced only at 鄉鎮市區(大面積) (estimate kept for 參考), or with no large-parcel price at all
  town-only     land priced only at 鄉鎮市區 (estimate kept for 參考)
  mixed-zone    land on a key whose p75 > 5×p25 (farmland and urban/industrial sales mixed; estimate kept for 參考)
  joint         公同共有/公開共有/共同共有 (share, location or price text), or 公同 in the owner field: the share is not the candidate's own
  share-check   share written reversed (N/1), read as 1/N
  large-bldg    building with > 1,000 m² owned (非住宅 or a share problem; the 房地 unit price doesn't apply)
  garage-share  共同使用部分 row whose share is the garage portion of the whole common area, not the holding; or a common-part row
                (共同使用部分 written, or a < 1/1 share of ≥ 300 m² with no price of its own beside a smaller 1/1 unit of the same day)
                owning > 2.5× all the candidate's other (unit) rows of its 段 together
  no-share      priced place but no owned area (share unreadable or no area)
  no-match      place not found or no level with enough sales
  ambiguous-town  the 段 exists in several towns and nothing written picks one
A land price key whose p75 ≤ 1.01×p25 (one seller's slices) is treated as too thin and the next level is tried.
"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths
from lvr import clean, road, sec0, norm_sec, BIG
from declaration import frac
JOINT = re.compile('公同共有|公開共有|共同共有')
COMMON = '共同使用部分'
FAIR = (None, 'town-only', 'mixed-zone')                                        # joint/share-check outrank a price-quality flag

LEGACY = {'臺北縣': '新北市', '桃園縣': '桃園市', '臺中縣': '臺中市', '臺南縣': '臺南市', '高雄縣': '高雄市',
          '北市': '臺北市', '中市': '臺中市', '南市': '臺南市', '高市': '高雄市'}                # abbreviations: 高市鼓山區…
LEVELS = {'land': [('S', 3, '段'), ('S0', 5, '段(不分小段)'), ('T', 5, '鄉鎮市區')],
          'land_large': [('SL', 3, '段(大面積)'), ('S0L', 5, '段(不分小段,大面積)'), ('TL', 5, '鄉鎮市區(大面積)')],
          'bldg': [('S', 3, '段'), ('S0', 5, '段(不分小段)'), ('R', 3, '路'), ('T', 5, '鄉鎮市區')]}
EMPTY = dict(lvr_level=None, lvr_key=None, lvr_n=None, unit_p25=None, unit_median=None, unit_p75=None, est_twd=None, est_low=None, est_high=None)
SUBS = {'新竹市': ('東區', '北區', '香山區'), '嘉義市': ('東區', '西區')}          # LVR lists these two cities as one town each
ROADSEC = re.compile(r'(路|街|大道)[一二三四五六七八九十\d]*段')
BUILT = re.compile('自建|新建|第一次登記|建造')


def date(s):
    """Acquisition date as written (079/03/10, 79.3.10, 0790310, 民國79年3月10日…) -> (y, m, d) ROC, or None."""
    n = re.findall(r'\d+', re.sub(r'(中華)?民國', '', s or ''))
    if len(n) == 1 and len(n[0]) == 7: n = [n[0][:3], n[0][3:5], n[0][5:]]
    if len(n) < 3: return None
    y = int(n[0]); return (y - 1911 if y > 1911 else y, int(n[1]), int(n[2]))


def numden(s):
    """Raw (numerator, denominator) of a share written a/b or b分之a, for the pool-row check."""
    s = re.sub(r'[\s,，]', '', s or '')
    m = re.search(r'(\d+)/(\d+)', s); n = re.search(r'(\d+)分?之(\d+)', s)
    return (m.group(1), m.group(2)) if m else (n.group(2), n.group(1)) if n else None


class Estimator:
    def __init__(self, year):
        d = paths.load(os.path.join(paths.PRICES, paths.YEARS[year]['lvr_file']))
        self.stats = {'land': d['land'], 'bldg': d['house']}; self.meta = d['meta']
        self.towns, self.secs, self.roads = {}, {}, {}       # city -> {towns}; (city, town) -> {sections}; (city, road) -> {towns}, from any year's 實價登錄
        for cfg in paths.YEARS.values():
            o = d if cfg['lvr_file'] == paths.YEARS[year]['lvr_file'] else paths.load(os.path.join(paths.PRICES, cfg['lvr_file']))
            for k in list(o['land']) + list(o['house']):
                p = k.split('|'); self.towns.setdefault(p[1], set()).add(p[2])
                if p[0] in ('S', 'S0'): self.secs.setdefault((p[1], p[2]), set()).add(p[3])
                if p[0] == 'R': self.roads.setdefault((p[1], p[3]), set()).add(p[2])

    def _has(self, city, town, sec, s0):
        S = self.secs.get((city, town), ()); return sec in S or s0 in S

    def _towns(self, city, rest):
        """[(town, remainder, swapped)] for the LVR town at the start of rest: an exact name, else a 市/鄉/鎮/區 suffix swap
        whose written 段 exists in the swapped town (屏東鎮玉映段 is not 屏東市)."""
        ts = sorted(self.towns.get(city, ()), key=len, reverse=True)
        for t in ts:
            if rest.startswith(t): return [(t, rest[len(t):], False)]
        for t in ts:
            if len(t) > 2 and re.match(re.escape(t[:-1]) + '[市鄉鎮區]', rest):
                sec, s0 = self._sec(rest[len(t):], city, t)
                return [(t, rest[len(t):], True)] if not s0 or self._has(city, t, sec, s0) else []
        return []

    def _sec(self, rest, city=None, town=None):
        m = re.match(r'^(.*?段(?:[^段]*?小段)?)', rest)
        if not m or re.search(r'待判讀|\([^)]*$', m.group(1)): return None, None
        s = norm_sec(m.group(1)); v = re.sub(r'^\D{1,4}?[村里](?:\d+鄰)?(?=.+段)', '', s)    # 龍水里青海段
        if v != s and town and not self._has(city, town, s, sec0(s)) and self._has(city, town, v, sec0(v)): s = v
        if ROADSEC.search(s) and not (town and self._has(city, town, s, sec0(s))): return None, None   # road, unless a real 中路段
        if town: s = self._alias(city, town, s, rest[m.end():])
        return s, sec0(s)

    def _alias(self, c, t, s, after):
        """A written section missing from 實價登錄 under another known spelling; first unique existing hit wins."""
        S = self.secs.get((c, t), set())
        if s in S or sec0(s) in S: return s
        y, n = re.match(r'([^段]{1,3})段', after), re.fullmatch(r'(.+)段([一二三四五六七八九十]+)小段', s)
        for alt in ([s.translate(str.maketrans('後后', '后後'))],
                    [x for x in S if x.endswith('段' + s)] if re.fullmatch(r'[^段]+小段', s) else [],                  # 車店小段
                    [x for x in S if re.fullmatch(re.escape(s[:-1]) + '[劃測]{1,2}段', x)] if c == '金門縣' and re.fullmatch(r'[^段]+段', s) else [],
                    [norm_sec(s[:-1] + y.group(1) + '段')] if y and re.fullmatch(r'[^段]+段', s) else [],          # 新厝段南段 -> 新厝南段
                    [n.group(1) + n.group(2) + '段'] if c == '宜蘭縣' and n else []):                               # 凱旋段一小段 -> 凱旋一段
            if len(alt) == 1 and (alt[0] in S or sec0(alt[0]) in S): return alt[0]
        return s

    def parse(self, location, cand_city, hint=()):
        """hint: (city, town) pairs the same candidate wrote explicitly, to break a tie between towns sharing a 段 name."""
        s, how, amb = re.sub(r'^坐落', '', clean(location)), [], False
        for o, n in LEGACY.items():
            if s.startswith(o): s, how = n + s[len(o):], ['legacy']; break
        city = next((c for c in self.towns if s.startswith(c)), None)
        rest = re.sub('^(?:%s)+' % city, '', s) if city else s                 # 臺中市臺中市北區…
        town = None
        if city and city in self.towns[city]:                                 # 新竹市/嘉義市: LVR has one town named after the city
            rest = re.sub('^(?:%s)?[市區]?(?:(?:東|西|北|香山)區)?' % city[:-1], '', rest)
            town, how = city, how + ['city-as-town']
        elif city:
            h = self._towns(city, rest)
            if h: (town, rest, alt), how = h[0], how + (['town-suffix'] if h[0][2] else [])
        else:
            hits = [(c, *h) for c in self.towns for h in self._towns(c, s)]
            hits += [(c, c, s[len(d):], False) for c, ds in SUBS.items() for d in ds if s.startswith(d)]
            hits = [h for h in hits if not h[3]] or hits                       # exact town names outrank suffix swaps
            ok = [h for h in hits if (p := self._sec(h[2], h[0], h[1]))[1] and self._has(h[0], h[1], *p)]
            pref = [h for h in hits if h[0] == cand_city]
            pick = [h for h in pref if h in ok] or (ok if len(ok) == 1 else []) or pref or ok or hits
            if len(pick) > 1: pick = [h for h in pick if h[:2] in hint] or pick
            if len(pick) == 1:
                city, town, rest, alt = pick[0]
                how += (['cand-city'] if city == cand_city else ['section-unique'] if pick[0] in ok else []) * (len(hits) > 1) + ['town-suffix'] * alt
            amb = len(pick) > 1
        sec, s0 = self._sec(rest, city, town)
        if not town and not amb:                                              # town not written: the 段 decides, if it exists in one town only
            for r in dict.fromkeys((rest, re.sub(r'^[^段]{1,3}?[市鄉鎮區]', '', rest))):     # 屏東鎮玉映段: an unknown town name in front
                sec, s0 = self._sec(r, city)
                for scope, tag in (([city] if city else [cand_city], 'section-in-city'), ([] if city else list(self.towns), 'section-unique')):
                    ts = [(c, t) for c in scope if c for t in self.towns.get(c, ()) if sec in self.secs.get((c, t), ())] or \
                         [(c, t) for c in scope if c for t in self.towns.get(c, ()) if s0 and self._has(c, t, sec, s0)]   # full 段+小段 first
                    if ts: break
                if ts: break
            if len(ts) > 1: ts = [x for x in ts if x in hint] or ts
            if len(ts) == 1: (city, town), how = ts[0], how + [tag]; sec = self._alias(city, town, sec, ''); s0 = sec0(sec)
            amb = len(ts) > 1
        if not town and not amb and not sec and (r := road(rest)):            # 台北市長安西路: the road decides, if it runs in one town (or one the candidate wrote)
            c = city or cand_city; ts = sorted(self.roads.get((c, r), ()))
            if len(ts) > 1: ts = [t for t in ts if (c, t) in hint] or ts
            if len(ts) == 1: city, town, how = c, ts[0], how + ['road-in-city' if c == city else 'road-in-cand-city']
            amb = len(ts) > 1
        return dict(city=city, town=town, sec=sec, sec0=s0, road=None if sec else road(rest), amb=amb,
                    how='+'.join(how) or ('exact' if town else None))

    def value(self, kind, location, owned_m2, cand_city, p=None):
        p = p or self.parse(location, cand_city); st = self.stats[kind]
        if not (p['city'] and p['town']): return dict(EMPTY, flag='ambiguous-town' if p['amb'] else 'no-match')
        val = {'S': p['sec'], 'S0': p['sec0'], 'R': p['road'], 'T': ''}
        lvls = LEVELS['land_large' if kind == 'land' and (owned_m2 or 0) >= BIG else kind]
        for lv, mn, name in lvls:
            b = lv.rstrip('L')
            if val[b] is None: continue
            k = '|'.join([lv, p['city'], p['town']] + ([val[b]] if b != 'T' else []))
            v = st.get(k)
            if v and v[0] >= mn and not (kind == 'land' and v[3] <= 1.01 * v[1]):
                n, lo, med, hi = v; est = (lambda u: round(u * owned_m2)) if owned_m2 else (lambda u: None)
                return dict(lvr_level=name, lvr_key=k, lvr_n=n, unit_p25=lo, unit_median=med, unit_p75=hi, est_twd=est(med), est_low=est(lo), est_high=est(hi),
                            flag='large-land' if lv == 'TL' else 'no-share' if not owned_m2 else None if kind != 'land' else
                            'town-only' if lv == 'T' else 'mixed-zone' if hi > 5 * lo else None)
        return dict(EMPTY, flag='large-land' if lvls is LEVELS['land_large'] else 'no-match')

    def candidate(self, land_items, bldg_items, cand_city):
        own = lambda x: round(x['area_m2'] * f, 2) if x.get('area_m2') and (f := frac(x.get('share'))) else None
        P = [self.parse(x['location'], cand_city) for x in land_items + bldg_items]
        hint = {(p['city'], p['town']) for p in P if p['town'] and 'section' not in p['how']}
        PL, PB = [self.parse(x['location'], cand_city, hint) for x in land_items], [self.parse(x['location'], cand_city, hint) for x in bldg_items]
        lv = [dict(self.value('land', x['location'], own(x), cand_city, p), owned_m2=own(x)) for x, p in zip(land_items, PL)]
        bv = [dict(self.value('bldg', x['location'], own(x), cand_city, p), owned_m2=own(x)) for x, p in zip(bldg_items, PB)]
        for x, v in zip(land_items + bldg_items, lv + bv):
            if v['flag'] in FAIR and (any(JOINT.search(x.get(k) or '') for k in ('share', 'location', 'price')) or '公同' in (x.get('owner') or '')): v['flag'] = 'joint'
            elif v['flag'] in FAIR and frac(x.get('share'), detail=True)[1]: v['flag'] = 'share-check'
        # garage portion of a 共同使用部分: same large area listed with several shares, or the pool a sibling row's share is a fraction of
        fr, nd = [frac(x.get('share')) for x in bldg_items], [numden(x.get('share')) for x in bldg_items]
        garage = lambda i: bv[i]['flag'] is None and fr[i] and fr[i] >= 0.05 and (bv[i]['owned_m2'] or 0) >= 300
        same = {}
        for i, x in enumerate(bldg_items):
            if (x.get('area_m2') or 0) >= 1000 and fr[i]: same.setdefault(x['area_m2'], []).append(i)
        for g in same.values():
            i = max(g, key=lambda i: fr[i])
            if len({fr[j] for j in g}) > 1 and garage(i): bv[i]['flag'] = 'garage-share'
        for i in range(len(bldg_items)):
            if nd[i] and int(nd[i][0]) > 1 and nd[i][0] != nd[i][1] and any(j != i and nd[j] and nd[j][1] == nd[i][0] for j in range(len(bldg_items))) and (bldg_items[i].get('area_m2') or 0) >= 1000 and garage(i):
                bv[i]['flag'] = 'garage-share'
        # a common part owning > 2.5× the candidate's units of the same 段: the share is of the whole common area, not the candidate's part
        sk = lambda i: PB[i]['sec0'] and (PB[i]['city'], PB[i]['town'], PB[i]['sec0'])
        oa = lambda i: bv[i]['owned_m2'] or 0
        amt = lambda i: set(re.findall(r'\d{5,}', (bldg_items[i].get('price') or '').replace(',', '')))
        sib = lambda i: [j for j in range(len(bldg_items)) if j != i and sk(j) == sk(i)]
        common = [COMMON in ''.join(x.get(k) or '' for k in ('location', 'price', 'reason')) or bool(
                  sk(i) and fr[i] and fr[i] < 1 and (x.get('area_m2') or 0) >= 300 and any(fr[j] == 1 and oa(j) < oa(i) and amt(i) <= amt(j)
                  and date(bldg_items[j].get('acquired')) == date(x.get('acquired')) for j in sib(i))) for i, x in enumerate(bldg_items)]
        for i in range(len(bldg_items)):
            u = sum(oa(j) for j in sib(i) if not common[j]) if sk(i) and common[i] and bv[i]['flag'] is None else 0
            if u and oa(i) > 2.5 * u: bv[i]['flag'] = 'garage-share'
        for v in bv:
            if v['flag'] is None and (v['owned_m2'] or 0) > 1000: v['flag'] = 'large-bldg'
        # 基地: land of the same 段 (or town, for a building with no 段) acquired the same day as a counted building, smallest first;
        # then land acquired on or before a 自建/新建/第一次登記/建造 building of the same 段. Each counted building lends 1.5× its owned
        # area across both passes (land with no owned area adds nothing and is always base); joint/share-check rows stay visible
        bkey = lambda p: (p['city'], p['town'], p['sec0']) if p['sec0'] else (p['city'], p['town'])
        ok = lambda v: v['flag'] not in ('base', 'joint', 'share-check')
        B = [(j, bkey(p), date(x.get('acquired')), bool(p['sec0'] and BUILT.search(x.get('reason') or '')))
             for j, (x, v, p) in enumerate(zip(bldg_items, bv, PB)) if v['flag'] is None and v['est_twd'] is not None]
        rem = {j: 1.5 * bv[j]['owned_m2'] for j, *_ in B}
        def take(i, bs):
            a = lv[i]['owned_m2'] or 0
            if not bs or sum(rem[j] for j in bs) < a: return False
            for j in bs: t = min(rem[j], a); rem[j] -= t; a -= t
            lv[i]['flag'] = 'base'; return True
        order = sorted(range(len(land_items)), key=lambda i: lv[i]['owned_m2'] or 0)
        for i in order:
            p, d = PL[i], date(land_items[i].get('acquired'))
            if p['town'] and d and ok(lv[i]):
                any(take(i, [j for j, k, bd, _ in B if k == key and bd == d]) for key in ((p['city'], p['town'], p['sec0']), (p['city'], p['town'])))
        for i in order:
            p, d = PL[i], date(land_items[i].get('acquired'))
            if p['sec0'] and d and lv[i]['owned_m2'] and ok(lv[i]): take(i, [j for j, k, bd, b in B if b and k == bkey(p) and bd and d <= bd])
        for v in lv: v['in_building'] = v['flag'] == 'base'
        for v in lv + bv: v['counted'] = v['est_twd'] is not None and v['flag'] is None
        return lv, bv
