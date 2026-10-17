"""Human corrections layered on top of the AI transcriptions.

One YAML file per declaration: corrections/<year>/<seq>.yaml. The AI transcription files are never edited;
corrections are re-applied on every build, and each change carries the value it expects to replace (`old`),
so a correction that no longer matches the data is reported instead of being silently applied or lost.
"""
import glob, os, sys, typing
from typing import Any, Dict, List, Literal, Optional
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths
import schema as S
import schema_extra as SX

EXTRA_SECTIONS = {'vehicles': SX.Vehicle, 'ships': SX.Ship, 'aircraft': SX.Aircraft}


def _item_model(section):
    ann = S.Declaration.model_fields[section].annotation
    args = typing.get_args(ann)
    return args[0] if args and isinstance(args[0], type) and issubclass(args[0], BaseModel) else None


MAIN_SECTIONS = {k: _item_model(k) for k in S.Declaration.model_fields if _item_model(k)}
FORM_FIELDS = {k for k, f in S.Declaration.model_fields.items() if k not in MAIN_SECTIONS}
TARGETS = sorted(set(MAIN_SECTIONS) | set(EXTRA_SECTIONS) | {'form'})


class Change(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target: str = Field(description="分項：deposits、stocks、funds、land、vehicles… 或 form（表頭與各分項總額）")
    op: Literal['set', 'add', 'delete'] = 'set'
    row: Optional[int] = Field(None, description="0 起算的列號，指套用本檔前面各筆更正之後的列（與網站明細、資料庫 idx 相同）")
    match: Dict[str, Any] = Field(default_factory=dict, description="用來確認列號沒有對錯的欄位值，例如 {name: 台嘉碩}")
    field: Optional[str] = None
    old: Any = None
    new: Any = None
    values: Optional[Dict[str, Any]] = Field(None, description="op=add 時的整列內容")
    page: Optional[int] = Field(None, description="原始申報表頁碼（必填，除非 op=delete 且列明顯重複）")
    reason: str
    source: Optional[str] = Field(None, description="Issue 編號或回報者")

    @model_validator(mode='after')
    def _shape(self):
        if self.target not in TARGETS: raise ValueError(f'未知的 target：{self.target}；可用：{", ".join(TARGETS)}')
        if self.target == 'form' and self.op != 'set': raise ValueError('target=form 只能用 op=set')
        if self.op == 'set':
            if self.field is None: raise ValueError('op=set 需要 field')
            if 'new' not in self.model_fields_set: raise ValueError('op=set 需要 new（要清空請明寫 new: null）')
            if isinstance(self.new, (list, dict)): raise ValueError('new 必須是單一值')
            if self.target != 'form' and self.row is None: raise ValueError('op=set 需要 row')
            if self.target == 'form' and self.field not in FORM_FIELDS: raise ValueError(f'form 沒有欄位 {self.field}')
        if self.op == 'delete' and (self.row is None or not self.match): raise ValueError('op=delete 需要 row 與 match')
        if self.op == 'add' and not self.values: raise ValueError('op=add 需要 values')
        if self.op != 'delete' and self.page is None: raise ValueError('請填原始申報表的頁碼 page')
        return self


class CorrectionFile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    year: Literal['115', '111']
    seq: str
    name: str
    base: Optional[str] = Field(None, description="釘選要套用的轉錄版本，例如 y115_pro")
    note: Optional[str] = Field(None, description="顯示在網站明細頁的人工核對說明")
    form_inconsistent: Optional[bool] = Field(None, description="人工確認申報表本身加總就不一致")
    changes: List[Change] = Field(default_factory=list)


def load_file(path):
    with open(path, encoding='utf-8') as f:
        raw = yaml.safe_load(f) or {}
    for k in ('year', 'seq'):
        if k in raw and raw[k] is not None: raw[k] = str(raw[k])
    cf = CorrectionFile.model_validate(raw)
    exp = os.path.join(str(cf.year), f'{cf.seq}.yaml')
    if not os.path.normpath(path).endswith(exp): raise ValueError(f'{path}: 檔名應為 corrections/{exp}')
    return cf


def load_all(root=None):
    out, errors = {}, []
    for p in sorted(glob.glob(os.path.join(root or paths.CORR, '*', '*.yaml'))):
        try:
            cf = load_file(p); out[(cf.year, cf.seq)] = cf
        except (ValidationError, ValueError, yaml.YAMLError) as e:
            errors.append((p, str(e)))
    return out, errors


def _same(a, b):
    if a is None or b is None: return a is None and b is None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)): return abs(float(a) - float(b)) < 0.005
    return str(a).strip() == str(b).strip()


def _coerce(model, field, value):
    if model is None or value is None: return value
    if field not in model.model_fields: raise ValueError(f'{model.__name__} 沒有欄位 {field}')
    ann = model.model_fields[field].annotation
    base = [a for a in (typing.get_args(ann) or (ann,)) if a is not type(None)][0]
    if base is float: return float(value)
    if base is int: return int(value)
    if base is str: return str(value)
    return value


def apply(cf, data, extra):
    """Apply one correction file in place. Returns a list of (change_index, status, message); status is ok|error."""
    log = []
    def rows_of(target):
        if target in EXTRA_SECTIONS and extra is not None: return extra[target], EXTRA_SECTIONS[target]
        return data[target], MAIN_SECTIONS.get(target)
    for i, c in enumerate(cf.changes):  # file order; each row number refers to the rows after the earlier changes
        try:
            if c.target == 'form':
                cur = data.get(c.field)
                if not _same(cur, c.old): raise ValueError(f'old 不符：目前是 {cur!r}，檔案寫 {c.old!r}')
                data[c.field] = _coerce(S.Declaration, c.field, c.new)
                if c.field == 'vehicle_count' and extra is not None: extra['vehicle_count'] = data[c.field]
                log.append((i, 'ok', f'form.{c.field}: {cur!r} → {c.new!r}')); continue
            rows, model = rows_of(c.target)
            if c.op == 'add':
                item = model.model_validate(c.values).model_dump() if model else dict(c.values)
                rows.append(item); log.append((i, 'ok', f'{c.target}: 新增第 {len(rows) - 1} 列')); continue
            if not (0 <= c.row < len(rows)): raise ValueError(f'{c.target} 只有 {len(rows)} 列，沒有第 {c.row} 列')
            row = rows[c.row]
            for k, v in c.match.items():
                if not _same(row.get(k), v): raise ValueError(f'{c.target}[{c.row}].{k} 是 {row.get(k)!r}，不是 {v!r}；列號可能錯了')
            if c.op == 'delete':
                rows.pop(c.row); log.append((i, 'ok', f'{c.target}: 刪除第 {c.row} 列')); continue
            if c.field not in row and (model is None or c.field not in model.model_fields): raise ValueError(f'{c.target} 沒有欄位 {c.field}')
            cur = row.get(c.field)
            if not _same(cur, c.old): raise ValueError(f'old 不符：{c.target}[{c.row}].{c.field} 目前是 {cur!r}，檔案寫 {c.old!r}')
            row[c.field] = _coerce(model, c.field, c.new)
            log.append((i, 'ok', f'{c.target}[{c.row}].{c.field}: {cur!r} → {c.new!r}'))
        except (ValueError, TypeError, ValidationError) as e:
            log.append((i, 'error', str(e)))
    return log
