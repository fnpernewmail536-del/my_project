"""Small, bounded helpers shared by the structured conditional editor."""
import re
import secrets
import unicodedata


class ParseError(ValueError):
    pass


MAX_ITEM_AMOUNTS = dict(catfood=58999, xp=99999999, np=9999,
                       normal_tickets=999, rare_tickets=999,
                       platinum_tickets=99, legend_tickets=99, leadership=999)


def roll(bounds):
    return bounds[0] + secrets.randbelow(bounds[1] - bounds[0] + 1)


def name_key(value):
    value = re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value)).casefold())
    return ''.join(chr(ord(c)-0x60) if '\u30a1' <= c <= '\u30f6' else c for c in value)


class NameIndex:
    def __init__(self, metadata):
        self.characters = {int(c['id']): c for c in metadata.get('characters', []) if c.get('selectable', True)}

    def exact(self, query):
        normalized = unicodedata.normalize('NFKC', query).strip()
        match = re.fullmatch(r'(?:No\.?\s*|#)?(\d+)', normalized, re.I)
        if match:
            cid = int(match[1])
            return {cid: self.characters[cid]} if cid in self.characters else {}
        return {cid: c for cid, c in self.characters.items()
                if name_key(query) in {name_key(n) for n in c.get('names', [])}}


def collaboration_ids(metadata, required_characters=()):
    collab, ordinary = set(), set()
    for group in metadata.get('groups', []):
        if group.get('kind') in ('collab', 'gacha'):
            (collab if group['kind'] == 'collab' else ordinary).update(map(int, group.get('cat_ids', [])))
    if any(type(c.get('collab')) is not bool for c in required_characters) and (not collab or not ordinary):
        raise ParseError('コラボと通常ガチャの所属定義を確認できません。')
    result = collab - ordinary
    for c in metadata.get('characters', []):
        if type(c.get('collab')) is bool:
            (result.add if c['collab'] else result.discard)(int(c['id']))
    return result
