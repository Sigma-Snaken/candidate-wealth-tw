"""Classify a declared debt by purpose from its description + note, keywords only (no API).

purpose(desc, note) -> one of CATS. The note's 取得(發生)原因 decides first; the loan type written before the
creditor in desc (房屋貸款, 擔保放款, 信用貸款 ...) decides when the reason is missing or generic.
"""
import re

CATS = ['房貸', '不動產擔保週轉', '週轉／經營', '保單質借', '證券融資／質押', '車貸', '就學貸款', '信用卡', '私人借款', '其他／未說明']

POLICY = r'保單|保險單|保價|解約金'
NOT_OWN = r'擔保人|保證人|保證債務|保證貸款|作保|連帶保證|背書保證|連保|互保|預收'     # guarantees, prepayments received: not own borrowing
CARD = r'信用卡|卡債|現金卡'
SEC = r'融資買|融資款|融資融券|(?:股票|證券)融資|股票質|股票借|股質|股權質|質押借款|有價證券'
STUDY = r'就學|學貸|助學|留學|學費'
CAR = r'車貸|汽車|機車|購車|買車(?!位)|車輛|車子'
PRIVATE = r'私人|親友|個人借|親屬|親戚|家人|朋友|民間|股東借款|借用'
INST = r'銀|農會|漁會|信用|合作社|一信|二信|三信|六信|郵|人壽|證券|金庫|信託|國泰世華|互助社'
PERSON = r'(?!.*(?:同上|路|街|銀|信|會|社|庫|局|署|部|司|壽|行|所|院|業|廠|店|郵))[一-鿿○]{1,3}'
BUY = (r'購置?(?:不動產|房|屋|地|住|宅|土|建|.{0,4}(?:住宅|用地|房地|土地|不動產|房屋))|購買.{0,6}(?:住宅|房|不動產|土地)|買(?:屋|房|地|土地|住宅)'
       r'|房貸|房屋貸款|房屋借款|房屋購置|房屋分期|購屋|自用住宅|住宅貸款|居住貸款|建屋|建房|自建|興建|新建|建造|建築|蓋屋|增建|合購'
       r'|購置農業生產用|土地貸款|農地貸款|不動產貸款|不動產購置|房地|(?:土地|房屋)買賣|(?<![週周])轉貸')
NOT_BUY = r'非購屋|二胎|增貸|理財|投資|循環|回復|透支'
UNSEC = r'信用|信貸|消費|小額|無擔保|不設定'
SECURED = r'抵押|擔保|長擔|中擔|增貸|設定|信託受益權'
PROPERTY = r'房屋|房子|土地|不動產|房貸'
DEV = r'建築融資|建融|土建融|營建|工程'                                   # developer construction financing: business, not a home loan
FUND = r'週轉|周轉|資金|調度|增資|整合|代償|消費|家用|短放|備用|修繕|整修|裝修|裝潢|投資|理財'     # reasons that make a property-secured loan 週轉 rather than a purchase
INSURER = r'人壽|保險股份|保險公司'
WORK = (r'週轉|周轉|營運|經營|事業|營業|企業|創業|青創|資金|調度|融通|投資|理財|信貸|信用貸款|信用借款|消費|生活|開銷|支出|醫療|稅|償還|代償|整合|協商'
        r'|動產|設備|機具|農機|挖土機|購置(?:器材|物品|貨)|工程|農業|生產|農貸|農家|政策性|專案|紓困|裝修|裝潢|修繕|整修'
        r'|勞工貸款|員貸|職工福利|備用金|短放|中放|短期|分期|小額|貸放|個人貸款|個人借|金融貸款|個人債務|購物')
CREDITOR = r'土地銀行|土銀|信用合作社|信用部|合作金庫'                  # creditor words that look like loan types


def reason(note):
    """The note minus labels, dates and punctuation."""
    s = re.sub(r'(取得|發生)[()（）、]*(取得|發生)?[()（）]*(時間|日期|原因)|時間|原因|[:：；;,，/／.\s()（）]', '', note or '')
    return re.sub(r'\d+年|\d+月|\d+日|\d+', '', s)


def purpose(desc, note):
    d, r = (desc or '').replace(' ', '').replace('／', '/'), reason(note)
    head = d.rsplit('/', 1)[0] if d.count('/') > 1 else re.split(r'[/\-－_（(]', d)[0]
    head = re.sub(CREDITOR, '', head)                                      # loan type written before the creditor
    t = head + ' ' + r
    unsec = re.search(UNSEC, head)
    if re.search(POLICY, d + r): return '保單質借'
    if re.search(NOT_OWN, d + r): return '其他／未說明'
    if re.search(CARD, t): return '信用卡'
    if re.search(SEC, t) or re.search(r'證券', d): return '證券融資／質押'
    if not re.search(INST, d + (note or '')) and (re.search(PRIVATE, t) or '/' in d and re.fullmatch(PERSON, d.split('/')[-1])):
        return '私人借款'
    if re.search(STUDY, t): return '就學貸款'
    if re.search(CAR, t): return '車貸'
    if re.search(DEV, t): return '週轉／經營'
    if (re.search(BUY, r) and not (re.search(FUND, r) and not re.search(r'購|買', r)) or '買賣' in r and not unsec) and not re.search(NOT_BUY, r): return '房貸'
    if re.search(BUY, head): return '不動產擔保週轉' if re.search(NOT_BUY, t) or re.search(FUND, r) else '房貸'
    if re.search(SECURED, re.sub(r'無擔保|不設定', '', t)): return '不動產擔保週轉'
    if not unsec and re.search(PROPERTY, t): return '不動產擔保週轉'
    if re.search(INSURER, d): return '保單質借'
    if re.search(WORK, t): return '週轉／經營'
    return '其他／未說明'


def purposes(rows):
    """purpose() over one declaration's (desc, note) rows in order; a note that only says 同上 reuses the previous note."""
    out, prev = [], None
    for desc, note in rows:
        if prev is not None and re.fullmatch(r'同上|同前|〃', reason(note)): note = prev
        out.append(purpose(desc, note)); prev = note
    return out
