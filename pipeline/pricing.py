"""Match declared securities to exchange tickers and reprice them at the year's base-date close."""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths
import fx as FX

# Hand-verified aliases for renamed securities (same ticker, same share count).
ALIAS = {'元大寶來台灣卓越50證券投資信託基金': '0050', '元大台灣卓越50證券投資信託基金': '0050', '台新金': '2887'}
COMPANY_FILES = (('co_L.json', '公司代號', '公司名稱'), ('co_O.json', 'SecuritiesCompanyCode', 'CompanyName'),
                 ('co_R.json', 'SecuritiesCompanyCode', 'CompanyName'))


def norm(s):
    s = (s or '').strip().replace('臺', '台').replace('　', '').replace(' ', '').replace('*', '')
    s = s.replace('（', '(').replace('）', ')').replace('－', '-').replace('—', '-')
    s = re.sub(r'\((上市|上櫃|興櫃|未上市|未上櫃|普通股|特別股)\)$', '', s)
    s = re.sub(r'(上市|上櫃|興櫃)$', '', s)
    return s.upper()


def strip_corp(n):
    return re.sub(r'(股份有限公司|\(股\)公司|\(股\)|公司)$', '', n)


def num(x):
    return float(x) if isinstance(x, (int, float)) else 0.0


class Pricer:
    """Exact-name matching only (after normalisation); no fuzzy matching, so a wrong match never misprices silently."""

    def __init__(self, year):
        self.year = year
        self.prices = paths.load(os.path.join(paths.PRICES, paths.YEARS[year]['prices']))
        isin = paths.load(os.path.join(paths.PRICES, 'isin_index.json'))
        self.name2code = {}
        for code, v in self.prices.items(): self._add(v['name'], code)
        for code, v in isin.items(): self._add(v['name'], code)
        for f, kc, kn in COMPANY_FILES:
            for r in paths.load(os.path.join(paths.RAW, f)):
                self._add(r[kn], r[kc]); self._add(r[kn].replace('股份有限公司', ''), r[kc])
        for k, v in ALIAS.items(): self.name2code[norm(k)] = v

    def _add(self, name, code):
        k = norm(name)
        if k and k not in self.name2code: self.name2code[k] = code

    def resolve(self, code, name):
        c = (code or '').strip().upper()
        n = norm(name)
        if n in self.name2code: return self.name2code[n], 'name'
        n2 = strip_corp(n)
        if n2 and n2 in self.name2code: return self.name2code[n2], 'name'
        if c and c in self.prices and norm(self.prices[c]['name']) == n: return c, 'code'
        if c and c in self.prices: return c, 'code-only'
        return None, None

    def price(self, item, units_key, stated=None):
        """Declared value, base-date market value and how it was matched, for one stock or fund row."""
        P = self.prices
        declared = num(item.get('twd'))
        u0, p0 = item.get(units_key), item.get('par')
        fxi = FX.normalise(item, (u0 * p0) if (u0 and p0) else None, self.year, stated)
        out = {'name': item.get('name'), 'owner': item.get('owner'), 'units': item.get(units_key), 'par': item.get('par'),
               'declared': declared, 'market': declared, 'code': None, 'close': None, 'priced': False, 'flag': None, 'suspect': False}
        u = item.get(units_key)
        if declared > 1e8 and u and declared / u > 50000:
            out.update(market=0.0, suspect=True, flag='單位價值超過 5 萬元且總額逾 1 億，疑似申報填寫錯誤，市值估算不計入')
            return out
        out['fx'] = {k: fxi[k] for k in ('currency', 'currency_source', 'foreign_amount', 'declared_rate', 'rate_source', 'std_rate')} if fxi['currency'] != 'TWD' else None
        if fxi['currency'] != 'TWD':
            out['market'] = fxi['std_twd'] if fxi['std_twd'] is not None else declared
            out['flag'] = f"外幣（{fxi['currency']}）計價，以統一匯率 {fxi['std_rate']} 換算" if fxi['std_rate'] else '外幣計價，維持申報值'
            return out
        code, how = self.resolve(item.get('code'), item.get('name'))
        if not code or code not in P or P[code]['close'] is None:
            out['flag'] = '未上市櫃或查無收盤價，維持申報值' if not code else '當日無收盤價，維持申報值'
            out['code'] = code; return out
        close = P[code]['close']; units = item.get(units_key); par = item.get('par')
        out.update(code=code, close=close, market_name=P[code]['name'], market_kind=P[code]['market'])
        if how == 'code-only': out['flag'] = '依代碼比對（名稱不同）'
        plaus = lambda v: v and (abs(v - 10) < 0.5 or 1 / 3.2 <= v / close <= 3.2)
        if not units:
            if par and declared: units = declared / par; out['flag'] = '股數缺漏，以金額/面額推算'
            else: out['flag'] = '缺股數，維持申報值'; return out
        elif par and not plaus(par) and plaus(units) and abs(units * par - (declared or units * par)) <= max(1, 0.02 * units * par):
            units = par; out['flag'] = '股數與單價欄位疑似對調，已更正'
        elif declared:
            implied = declared / units
            if not (plaus(implied) or (par and abs(implied - par) / par < 0.02 and plaus(par))):
                if par and plaus(par): units = declared / par; out['flag'] = '股數與金額不一致，以金額/面額推算'
                else: out['flag'] = '股數與金額無法對應收盤價，維持申報值'; out['code'] = code; return out
        out['units_used'] = round(units, 4)
        out['market'] = round(units * close, 2); out['priced'] = True
        return out
