"""Separate collaboration characters from common units in collaboration banners."""
from .parser import ParseError


def collaboration_ids(metadata, required_characters=()):
    """A banner's common fillers are not collaboration characters.

    Per-character boolean flags take priority. Otherwise a character must be in
    a collaboration pool and absent from ordinary pools. Missing comparison
    data is an error when an exception depends on it.
    """
    characters = metadata.get('characters', [])
    required_characters = list(required_characters)
    requires_groups = any(not isinstance(info.get('collab'), bool) for info in required_characters)
    collab_members, ordinary_members = set(), set()
    for group in metadata.get('groups', []):
        if group.get('kind') not in {'collab', 'gacha'}:
            continue
        try:
            members = {int(cid) for cid in group.get('cat_ids', [])}
        except (TypeError, ValueError) as exc:
            raise ParseError('コラボの所属定義が不正です。引き継ぎ取得前に停止しました。') from exc
        if any(cid <= 0 for cid in members):
            raise ParseError('コラボのキャラ番号が不正です。引き継ぎ取得前に停止しました。')
        (collab_members if group['kind'] == 'collab' else ordinary_members).update(members)
    if requires_groups and (not collab_members or not ordinary_members):
        raise ParseError('コラボ例外の判定に必要なコラボ・通常ガチャの所属定義が不足しています。引き継ぎ取得前に停止しました。')
    result = collab_members - ordinary_members
    for info in characters:
        if isinstance(info.get('collab'), bool):
            cid = int(info['id'])
            if info['collab']:
                result.add(cid)
            else:
                result.discard(cid)
    return result
