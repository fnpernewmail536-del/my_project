"""The user's numbered sales catalogue, independent of BCSFE menu numbering."""
import json
from pathlib import Path

CATALOGUE = json.loads(Path(__file__).with_name('menu_definitions.json').read_text(encoding='utf-8'))
MENU_BY_NUMBER = {item['number']: item for item in CATALOGUE['items']}
MAX_ITEM_AMOUNTS = CATALOGUE['max_defaults']['items']
MAX_CHARACTER_BASE = CATALOGUE['max_defaults']['character']['base_level']
MAX_CHARACTER_PLUS = CATALOGUE['max_defaults']['character']['plus_level']
if sorted(MENU_BY_NUMBER) != list(range(1,85)):
    raise ValueError('menu_definitions.json は1〜84番を重複なく定義してください。')
