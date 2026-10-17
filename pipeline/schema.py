from pydantic import BaseModel, Field
from typing import List, Optional

class LandItem(BaseModel):
    location: str = Field(description="土地坐落（縣市鄉鎮區段），不含地號")
    area_m2: Optional[float] = Field(description="面積（平方公尺）")
    share: Optional[str] = Field(description="權利範圍/持分，例如 1/2、502/1911、全部")
    owner: str = Field(description="所有權人")
    acquired: Optional[str] = Field(description="登記(取得)時間")
    reason: Optional[str] = Field(description="登記(取得)原因")
    price: Optional[str] = Field(description="取得價額欄位原文（數字或「超過五年」等）")

class BuildingItem(BaseModel):
    location: str = Field(description="建物標示（縣市鄉鎮區段），不含建號、門牌")
    area_m2: Optional[float]
    share: Optional[str]
    owner: str
    acquired: Optional[str]
    reason: Optional[str]
    price: Optional[str]

class VehicleItem(BaseModel):
    desc: str = Field(description="廠牌型號（汽車）或種類（船舶/航空器）")
    owner: str
    acquired: Optional[str]
    price: Optional[str] = Field(description="取得價額原文")

class CashItem(BaseModel):
    currency: str
    owner: str
    foreign_amount: Optional[float]
    twd: Optional[float] = Field(description="新臺幣總額或折合新臺幣總額")

class DepositItem(BaseModel):
    bank: str = Field(description="存放機構（含分行），不含帳號")
    kind: Optional[str] = Field(description="種類，如活期儲蓄存款/定期存款/綜合存款")
    currency: str
    owner: str
    foreign_amount: Optional[float]
    twd: Optional[float] = Field(description="新臺幣總額或折合新臺幣總額（數字）")

class StockItem(BaseModel):
    code: Optional[str] = Field(description="股票代碼（名稱欄「代碼/名稱」斜線前），無則 null")
    name: str = Field(description="股票名稱（斜線後）")
    owner: str
    shares: Optional[float] = Field(description="股數")
    par: Optional[float] = Field(description="票面價額（每股）")
    currency: Optional[str] = Field(description="外幣幣別，新臺幣則 null")
    twd: Optional[float] = Field(description="新臺幣總額或折合新臺幣總額")

class BondItem(BaseModel):
    name: str
    code: Optional[str]
    owner: str
    units: Optional[float]
    par: Optional[float]
    currency: Optional[str]
    twd: Optional[float]

class FundItem(BaseModel):
    name: str = Field(description="基金/ETF 名稱")
    code: Optional[str] = Field(description="代碼，若有")
    owner: str
    units: Optional[float] = Field(description="單位數")
    par: Optional[float] = Field(description="票面價額/每單位")
    currency: Optional[str]
    twd: Optional[float]

class OtherSecurityItem(BaseModel):
    name: str
    owner: str
    units: Optional[float]
    twd: Optional[float]

class ValuedItem(BaseModel):
    desc: str = Field(description="名稱/種類/機構（不含地址、帳號）")
    owner: str
    amount_twd: Optional[float] = Field(description="新臺幣金額")
    note: Optional[str] = Field(description="時間/原因等補充，不含地址")

class Declaration(BaseModel):
    declarant: str = Field(description="申報人姓名")
    declare_date: Optional[str] = Field(description="申報日，如 民國115年9月1日")
    spouse: Optional[str] = Field(description="配偶姓名，無則 null")
    minor_children_count: int = Field(description="未成年子女人數")
    total_pages: Optional[int]
    land: List[LandItem]
    land_count: Optional[int] = Field(description="土地 總申報筆數（表上數字，手寫更正優先）")
    buildings: List[BuildingItem]
    building_count: Optional[int]
    ships: List[VehicleItem]
    vehicles: List[VehicleItem]
    vehicle_count: Optional[int]
    aircraft: List[VehicleItem]
    cash: List[CashItem]
    cash_total_twd: Optional[float] = Field(description="(六)現金 總金額")
    deposits: List[DepositItem]
    deposit_count: Optional[int] = Field(description="存款 總申報筆數")
    deposit_total_twd: Optional[float] = Field(description="(七)存款 總金額（手寫更正優先）")
    securities_total_twd: Optional[float] = Field(description="(八)有價證券 總價額")
    stocks: List[StockItem]
    stock_count: Optional[int]
    stock_total_twd: Optional[float] = Field(description="1.股票 總價額")
    bonds: List[BondItem]
    bond_total_twd: Optional[float]
    funds: List[FundItem]
    fund_total_twd: Optional[float]
    other_securities: List[OtherSecurityItem]
    other_securities_total_twd: Optional[float]
    other_assets: List[ValuedItem] = Field(description="(九)其他具有相當價值之財產：珠寶、古董、字畫、信託專戶等")
    other_assets_total_twd: Optional[float]
    insurance: List[ValuedItem] = Field(description="(十)保險：desc=保險公司+保險名稱")
    insurance_count: Optional[int]
    debt_claims: List[ValuedItem] = Field(description="(十一)債權")
    debt_claims_total_twd: Optional[float]
    debts: List[ValuedItem] = Field(description="(十二)債務：desc=種類/債權人名稱（不含地址）")
    debts_count: Optional[int]
    debts_total_twd: Optional[float]
    business_investments: List[ValuedItem] = Field(description="(十三)事業投資：desc=事業名稱")
    business_investments_total_twd: Optional[float]
    trust_items: List[ValuedItem] = Field(description="信託財產附表，若有")
    notes: Optional[str] = Field(description="判讀疑問、手寫更正、模糊處、備註欄聲明")

PROMPT = """這是一份台灣「公職候選人財產申報表」的掃描 PDF。請逐頁仔細閱讀，把所有填寫內容轉成結構化 JSON。
規則：
- 每個分項（土地、建物、船舶、汽車、航空器、現金、存款、有價證券(股票/債券/基金受益憑證/其他)、其他財產、保險、債權、債務、事業投資、信託）都要完整列出每一筆，空白列略過；被劃線刪除的列也略過。
- 金額一律轉成純數字（去掉逗號、「元」）。外幣要同時填外幣金額與折合新臺幣金額。
- 各分項的「總申報筆數」與「總金額/總價額」請逐字照表上數字填（數字很長時逐位確認，不要漏位數）；若印刷數字旁有手寫更正，以手寫更正後為準並在 notes 說明。
- 股票名稱欄格式通常為「代碼/名稱」，請分開填 code 與 name；斜線前空白則 code 為 null。
- 嚴禁輸出身分證字號、居留證號、出生日期、地址（含債權人地址、不動產門牌/地號/建號）、電話、帳號（含帳戶後四碼）。未成年子女只填人數，不填姓名。
- 看不清楚時填最可能的判讀並在 notes 註明。"""
