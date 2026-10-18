"""Repository paths and configuration shared by every pipeline script."""
import json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
TRANS = os.path.join(DATA, 'transcriptions')
PRICES = os.path.join(DATA, 'prices')
RAW = os.path.join(DATA, 'raw')
CORR = os.path.join(ROOT, 'corrections')
DIST = os.path.join(ROOT, 'dist')
PDFS = os.path.join(DATA, 'source_pdfs')
CONFIG = json.load(open(os.path.join(ROOT, 'config.json'), encoding='utf-8'))
YEARS = CONFIG['years']

def load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)

def candidates_index():
    return {x['SEQNO']: x for x in load(os.path.join(DATA, 'candidates_index.json'))}

def match_111():
    """115 seq -> Wayback record for the same-name, same-city 111 declaration."""
    return load(os.path.join(RAW, 'match.json'))

def parties_115():
    p = os.path.join(DATA, 'parties_115.json')
    return load(p) if os.path.exists(p) else {}

def birth_years_115():
    p = os.path.join(DATA, 'birth_years_115.json')
    return load(p)['rows'] if os.path.exists(p) else {}

def groups_111():
    return load(os.path.join(RAW, 'groups.json'))
