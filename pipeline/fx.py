"""Currency normalisation: ISO codes, declared vs uniform (Bank of Taiwan spot mid) exchange rates."""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths
BOT = paths.load(os.path.join(paths.PRICES, 'bot_fx.json'))
STD_DATE = {y: c['fx_date'] for y, c in paths.YEARS.items()}
FILING_DATE = {y: c['fx_filing_date'] for y, c in paths.YEARS.items()}
NAMES = [('人民', 'CNY'), ('南非', 'ZAR'), ('新加坡', 'SGD'), ('紐', 'NZD'), ('瑞士', 'CHF'), ('瑞典', 'SEK'), ('港', 'HKD'), ('英', 'GBP'),
         ('加拿大', 'CAD'), ('加幣', 'CAD'), ('澳', 'AUD'), ('歐', 'EUR'), ('日', 'JPY'), ('泰', 'THB'), ('韓', 'KRW'), ('越南', 'VND'),
         ('馬來', 'MYR'), ('印尼', 'IDR'), ('菲', 'PHP'), ('美', 'USD'), ('新臺', 'TWD'), ('新台', 'TWD'), ('臺幣', 'TWD'), ('台幣', 'TWD')]
COMMON = {'USD', 'JPY', 'CNY', 'EUR', 'AUD', 'ZAR', 'HKD', 'GBP', 'CAD', 'NZD', 'SGD', 'CHF'}
ISO = {'USD', 'JPY', 'CNY', 'RMB', 'EUR', 'AUD', 'ZAR', 'HKD', 'GBP', 'CAD', 'NZD', 'SGD', 'CHF', 'SEK', 'THB', 'KRW', 'VND', 'MYR', 'IDR', 'PHP', 'TWD', 'NTD'}

def iso(cur):
    c = (cur or '').strip().upper()
    if not c: return None
    for k in ISO:
        if k in c: return {'RMB': 'CNY', 'NTD': 'TWD'}.get(k, k)
    for k, v in NAMES:
        if k in cur: return v
    return None

def mid(code, date):
    if code == 'TWD': return 1.0
    r = BOT.get(date, {}).get(code)
    return r['mid'] if r else None

def infer(ratio, date):
    """Guess a currency from an implied TWD-per-unit ratio; returns code or None."""
    if ratio is None or ratio <= 0: return None
    if 0.85 < ratio < 1.15: return 'TWD'
    best = None
    for code, r in BOT.get(date, {}).items():
        if code not in COMMON: continue
        if r['mid'] and abs(ratio / r['mid'] - 1) < 0.08:
            d = abs(ratio / r['mid'] - 1)
            if best is None or d < best[0]: best = (d, code)
    return best[1] if best else None

def normalise(item, foreign_value, year, stated_rate=None):
    """Return FX fields for one item. foreign_value = amount in the item's own currency (deposit amount, or units x price)."""
    twd = item.get('twd') if item.get('twd') is not None else item.get('amount_twd')
    raw = item.get('currency'); code = iso(raw); src = 'declared' if code else None
    ratio = (twd / foreign_value) if (twd and foreign_value) else None
    if code is None:
        name = item.get('name') or ''
        m = re.search(r'(美元|美金|日圓|日幣|日元|歐元|澳幣|人民幣|南非幣|英鎊|港幣|紐幣|紐元|加幣|新幣|瑞郎)', name)
        if m: code, src = iso(m.group(1)), 'name'
        elif re.search(r'[A-Za-z]{3,}', name) and not re.search(r'KY$', name.upper()):
            g = infer(ratio, FILING_DATE[year])
            if g and g != 'TWD': code, src = g, 'inferred'
    if code in (None, 'TWD'):
        return dict(currency='TWD', currency_raw=raw, currency_source=src or 'default', foreign_amount=None,
                    declared_rate=None, rate_source=None, declared_twd=twd, std_rate=1.0, std_twd=twd, std_date=None)
    ref = mid(code, FILING_DATE[year])
    plaus = lambda r: r and ref and abs(r / ref - 1) < 0.15
    if stated_rate and plaus(stated_rate):
        rate, rsrc = stated_rate, 'stated'
        fv = twd / rate if twd else foreign_value
    elif ratio and plaus(ratio):
        rate, rsrc, fv = round(ratio, 6), 'derived', foreign_value
    elif twd:
        # foreign amount column unusable (e.g. holds a total or NAV): assume the filing-period market rate
        rate, rsrc, fv = None, 'unknown', (twd / ref if ref else None)
    else:
        rate, rsrc, fv = None, None, foreign_value
    sdate = STD_DATE[year]; srate = mid(code, sdate)
    std = round(fv * srate, 2) if (fv is not None and srate) else twd
    filled = None
    if twd in (None, 0) and fv and srate:  # foreign amount given but no TWD written
        filled = round(fv * (mid(code, FILING_DATE[year]) or srate), 2)
    return dict(currency=code, currency_raw=raw, currency_source=src, foreign_amount=fv, declared_rate=rate, rate_source=rsrc,
                declared_twd=twd, declared_twd_filled=filled, std_rate=srate, std_twd=std, std_date=sdate)
