from pydantic import BaseModel, Field
from typing import List, Optional

class Vehicle(BaseModel):
    brand_model: str = Field(description="廠牌型號欄全文，例如 TOYOTA RAV4、BMW X5、光陽機車")
    brand: Optional[str] = Field(description="廠牌（從廠牌型號欄拆出，例如 TOYOTA、BMW、福特）")
    displacement_cc: Optional[float] = Field(description="汽缸容量（cc），純數字；電動車填 null 並在 note 註明馬力或電動")
    displacement_raw: Optional[str] = Field(description="汽缸容量欄原文")
    plate: Optional[str] = Field(description="牌照號碼欄原文")
    owner: str = Field(description="所有人")
    acquired: Optional[str] = Field(description="登記(取得)時間原文")
    reason: Optional[str] = Field(description="登記(取得)原因")
    price_raw: Optional[str] = Field(description="取得價額欄原文")
    price_twd: Optional[float] = Field(description="取得價額的數字（新臺幣），無數字則 null")
    note: Optional[str]

class Ship(BaseModel):
    kind: str = Field(description="種類")
    size: Optional[str] = Field(description="總噸數（長度、管數）原文")
    port: Optional[str] = Field(description="船籍港")
    owner: str
    acquired: Optional[str]
    reason: Optional[str]
    price_raw: Optional[str]
    price_twd: Optional[float]

class Aircraft(BaseModel):
    model: str = Field(description="型式")
    maker: Optional[str] = Field(description="製造廠名稱")
    registration: Optional[str] = Field(description="國籍標誌及登記號碼")
    owner: str
    acquired: Optional[str]
    reason: Optional[str]
    price_raw: Optional[str]
    price_twd: Optional[float]

class FxRow(BaseModel):
    section: str = Field(description="所在分項：現金、存款、股票、債券、基金、其他有價證券、其他財產、債權、債務、事業投資 之一")
    name: str = Field(description="存放機構／證券名稱／債權債務人等該列的名稱欄")
    owner: str = Field(description="所有人／持有人")
    currency: str = Field(description="幣別欄原文，例如 美元、日圓、人民幣、澳幣")
    foreign_amount: Optional[float] = Field(description="外幣金額（外幣總額或外幣計價金額），純數字")
    stated_rate: Optional[float] = Field(description="表上明寫的匯率數字（例如「匯率:31.690」則填 31.69），表上沒寫就填 null，不要自己計算")
    twd: Optional[float] = Field(description="折合新臺幣金額，純數字；表上空白填 null")

class Extra(BaseModel):
    vehicles: List[Vehicle]
    vehicle_count: Optional[int] = Field(description="(四)汽車 總申報筆數（表上數字）")
    ships: List[Ship]
    aircraft: List[Aircraft]
    fx_rows: List[FxRow] = Field(description="表上所有以外幣計價的列（幣別不是新臺幣者），不論在哪一個分項")
    notes: Optional[str]

PROMPT_EXTRA = """這是一份台灣「公職候選人財產申報表」的掃描 PDF。只需要轉錄以下兩類資料，其餘分項不用輸出：
1. (三)船舶、(四)汽車（含大型重型機器腳踏車）、(五)航空器：每一列的所有欄位都要逐字轉錄，包括廠牌型號、汽缸容量、牌照號碼、所有人、登記(取得)時間、原因、取得價額。汽車可能跨頁，請全部列出；也填上汽車「總申報筆數」。
2. 所有以外幣計價的列（存款、現金、股票、基金、債券、債權、債務等任何分項中幣別不是新臺幣的列）：轉錄幣別、外幣金額、表上明寫的匯率（例如「(匯率:31.690)」；沒寫就填 null，不要自己計算）、折合新臺幣金額。
規則：數字一律去掉逗號；看不清楚的填最可能判讀並在 note/notes 註明；被劃線刪除的列略過。若有多份 PDF，第一份是原表，其後是補正，補正取代原表的對應項目。"""
