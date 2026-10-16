"""Compile detailed edits before account transfer, then apply under engine lock."""
import secrets
from .parser import ParseError, RARITY_LABELS
from .menus import MAX_CHARACTER_BASE, MAX_CHARACTER_PLUS
from .range_input import roll, GROUPS
from .characters import collaboration_ids
from .named_characters import reviewed_unlocks
from .level_exclusions import reviewed_level_exclusions


def has_details(plan):
    return bool(plan.get('quantity_ranges') or plan.get('level_rules') or plan.get('plus_only_rules') or plan.get('talents_max') or
                plan.get('helpers_legend') or plan.get('orb_s_amount') is not None or
                plan.get('unlock_rarities') or plan.get('unlock_queries') or plan.get('level_exclusion_queries') or plan.get('fruit_filters') or
                plan.get('labyrinth_amount') is not None or plan.get('gamatoto_level') is not None)


def compile_details(module, plan):
    """Resolve metadata and roll ranges without consuming an account's codes."""
    if not has_details(plan): return {}
    if not hasattr(module, '_apply_all_segments_unlocked'):
        raise ParseError('このエンジンは詳細指定に対応していません。my_project(4).zipのmain13.pyを使用してください。')
    result = {'levels': [], 'talents': [], 'orbs': {}, 'helper_ids': [], 'unlock_ids': [], 'fruits': {}}
    metadata = module.get_character_metadata() if plan.get('level_rules') or plan.get('plus_only_rules') or plan.get('talents_max') or plan.get('unlock_rarities') or plan.get('unlock_queries') or plan.get('level_exclusion_queries') else {}
    characters = [c for c in metadata.get('characters', []) if c.get('selectable', True)]
    result['level_exclusions']=reviewed_level_exclusions(metadata,plan) if plan.get('level_exclusion_queries') else []
    excluded_ids={match['id'] for match in result['level_exclusions']}
    getter = None

    def data(local, filename):
        nonlocal getter
        if getter is None: getter = module._latest_jp_game_data_getter()
        contents = getter.download(local, filename)
        if contents is None: raise ParseError('詳細指定に必要なゲーム定義を取得できません：' + filename + '。引き継ぎ取得前に停止しました。')
        return contents

    if plan.get('unlock_rarities'):
        for rarity in plan['unlock_rarities']:
            targets = [int(info['id']) for info in characters if int(info['rarity']) == rarity]
            if not targets: raise ParseError('対象レア度のキャラ定義がありません。引き継ぎ取得前に停止しました。')
            result['unlock_ids'].extend(targets)
    if plan.get('unlock_queries'):
        matches=reviewed_unlocks(metadata,plan)
        result['named_unlocks']=matches
        result['unlock_ids'].extend(match['id'] for match in matches)
    result['unlock_ids']=list(dict.fromkeys(result['unlock_ids']))
    ranges=plan.get('quantity_ranges',{})
    result['quantity_ranges']={n:b for n,b in ranges.items() if int(n) in GROUPS}
    if plan.get('labyrinth_amount') is not None:
        result['labyrinth_amount'] = int(plan['labyrinth_amount'])
    if plan.get('fruit_filters'):
        names = module.get_legend_metadata().get('vip_item_groups', {}).get('catfruit', [])
        if not names: raise ParseError('マタタビ・獣石の種類定義がありません。引き継ぎ取得前に停止しました。')
        filters = sorted(plan['fruit_filters'].items(),key=lambda item:item[0]!='other_fruit')
        for kind, amount in filters:
            if kind == 'other_fruit':
                targets = [index for index,name in enumerate(names) if 'マタタビ' not in name]
            else:
                substring = 'マタタビ' if kind == 'matatabi' else '獣石'
                targets = [index for index, name in enumerate(names) if substring in name]
            if not targets: raise ParseError('指定された素材の種類定義がありません。引き継ぎ取得前に停止しました。')
            for index in targets: result['fruits'][index] = roll(ranges[str(12 if kind=='matatabi' else 14)]) if str(12 if kind=='matatabi' else 14) in ranges else amount
    if plan.get('gamatoto_level') is not None:
        rows = list(module.core.CSV(data('DataLocal', 'GamatotoExpedition.csv')))
        limit = list(module.core.CSV(data('DataLocal', 'GamatotoExpedition_Limit.csv')))
        level = plan['gamatoto_level']
        if not limit or not 1 <= level <= limit[0][0].to_int() or level > len(rows):
            raise ParseError('ガマトトの指定レベルがゲーム定義の範囲外です。引き継ぎ取得前に停止しました。')
        result['gamatoto_xp'] = rows[level - 1][0].to_int() if level > 1 else 0
        if result['gamatoto_xp'] < 0: raise ParseError('指定ガマトトレベルの経験値が未定義です。引き継ぎ取得前に停止しました。')

    if plan.get('level_rules') or plan.get('plus_only_rules'):
        if not characters: raise ParseError('キャラ定義が空です。引き継ぎ取得前に停止しました。')
        needs_plus_support = any(rule.get('plus_only_supported') for rule in plan['level_rules'].values())
        unit_rows = list(module.core.CSV(data('DataLocal', 'unitbuy.csv'))) if needs_plus_support else None
        exception_characters = [info for info in characters
                                if plan.get('plus_only_rules',{}).get('collab') or plan.get('plus_only_rules',{}).get('collab:'+str(info['rarity'])) or plan['level_rules'].get(str(info['rarity']), {}).get('collab_plus')]
        collab_ids = collaboration_ids(metadata, exception_characters)
        for info in characters:
            if int(info['id']) in excluded_ids:continue
            rule = plan['level_rules'].get(str(info['rarity']))
            collab = int(info['id']) in collab_ids
            overrides = plan.get('plus_only_rules',{})
            override = overrides.get('collab:'+str(info['rarity'])) if collab else None
            if override is None:override=overrides.get('rarity:' + str(info['rarity']))
            if override is None and collab and int(info['rarity']) not in plan.get('collab_plus_excluded_rarities',[]): override = overrides.get('collab')
            if override is None and int(info['rarity']) not in plan.get('all_plus_excluded_rarities',[]):override=overrides.get('all')
            if override is not None:
                rule = {**(rule or {'base':'keep'}),'plus':override}
                rule.pop('collab_plus',None)
            if rule is None: continue
            display_id = int(info['id'])
            plus_supported = True
            if rule.get('plus_only_supported'):
                if display_id < 1 or display_id > len(unit_rows) or len(unit_rows[display_id - 1]) <= 51:
                    raise ParseError(f'No.{display_id}の＋対応定義がありません。引き継ぎ取得前に停止しました。')
                row = unit_rows[display_id - 1]
                if row[13].to_int() != int(info['rarity']):
                    raise ParseError(f'No.{display_id}のレア度定義が一致しません。引き継ぎ取得前に停止しました。')
                plus_supported = row[51].to_int() > 0
            base = None if rule['base'] == 'keep' else MAX_CHARACTER_BASE if rule['base'] == 'max' else roll(rule['base']) if isinstance(rule['base'],list) else int(rule['base'])
            plus_rule = rule['plus']
            # Ordinary gacha fillers in collaboration banners keep the default rule.
            if collab and rule.get('collab_plus'): plus_rule = rule['collab_plus']
            if plus_rule == 'keep':
                plus = None
            elif plus_rule == 'max':
                plus = MAX_CHARACTER_PLUS
            else:
                lo, hi = plus_rule
                plus = lo + secrets.randbelow(hi - lo + 1)
            if not plus_supported: plus = 0
            result['levels'].append({'id': display_id, 'rarity': int(info['rarity']),
                                     'base': base, 'plus': plus, 'collab': collab})
        missing = [RARITY_LABELS[int(r)] for r in plan['level_rules'] if not any(int(x['rarity']) == int(r) for x in characters)]
        if missing: raise ParseError('対象キャラの定義がありません：' + '・'.join(missing))
        for scope in plan.get('plus_only_rules',{}):
            if scope=='all':found=bool(characters)
            elif scope=='collab':found=any(int(x['id']) in collab_ids for x in characters)
            elif scope.startswith('collab:'):found=any(int(x['id']) in collab_ids and int(x['rarity'])==int(scope.split(':')[1]) for x in characters)
            else:found=any(int(x['rarity'])==int(scope.split(':')[1]) for x in characters)
            if not found: raise ParseError('＋値変更の対象定義がありません：' + scope + '。引き継ぎ取得前に停止しました。')
    if plan.get('talents_max'):
        if not characters: raise ParseError('本能のキャラ定義を取得できません。')
        for info in characters:
            for talent in info.get('talents', []):
                result['talents'].append({'id': int(info['id']), 'talent_id': int(talent['id']), 'level': int(talent['max_level'])})
        if not result['talents']: raise ParseError('本能定義を取得できません。引き継ぎ取得前に停止しました。')
        if not hasattr(module, '_set_cat_talent_level'): raise ParseError('本能処理がないエンジンです。')
    if plan.get('orb_s_amount') is not None:
        for group in module.get_legend_metadata().get('talent_orbs', []):
            for rank in group.get('ranks', []):
                if str(rank.get('rank', '')).strip().upper() == 'S':
                    result['orbs'][int(rank['id'])] = plan['orb_s_amount']
        if not result['orbs']: raise ParseError('Sランク本能玉の定義がありません。引き継ぎ取得前に停止しました。')
    if plan.get('helpers_legend'):
        raw = data('resLocal', 'GamatotoExpedition_Members_name_ja.csv')
        rows = module.core.CSV(raw, module.core.Delimeter.from_country_code_res(module.core.CountryCode.from_code('jp')), remove_empty=False)
        ids = []
        for row in list(rows)[1:]:
            if len(row) > 4 and ('レジェンド' in row[4].to_str() or 'legend' in row[4].to_str().lower()):
                member_id = row[0].to_int()
                if member_id >= 0 and member_id not in ids: ids.append(member_id)
        if len(ids) < 10: raise ParseError('レジェンド隊員10体の定義がありません。引き継ぎ取得前に停止しました。')
        result['helper_ids'] = ids[:10]
    return result


def apply_details(module, save, compiled):
    """Any missing target or failed write aborts final upload and uses original recovery."""
    logs = []
    ranges=compiled.get('quantity_ranges',{})
    for n,bounds in ranges.items():
        n=int(n)
        if n in (12,14):continue
        if n in (9,10):
            attr='catseyes' if n==9 else 'catamins'
            values=getattr(save,attr)
            if not values:raise RuntimeError('種類別数量の保存領域が空です。')
            setattr(save,attr,[roll(bounds) for _ in values])
        elif n in (8,11):
            values=save.battle_items.items if n==8 else save.ototo.base_materials.materials
            if not values:raise RuntimeError('種類別数量の保存領域が空です。')
            for item in values:item.amount=roll(bounds)
        elif n==13:
            ids=module._latest_talent_orb_ids()
            if not ids:raise RuntimeError('本能玉の種類定義がありません。')
            for orb_id in ids:save.talent_orbs.set_orb(orb_id,roll(bounds))
        elif n==82:
            if len(save.labyrinth_medals)<4:raise RuntimeError('地底迷宮勲章の保存領域が不足しています。')
            save.labyrinth_medals[:4]=[roll(bounds) for _ in range(4)]
        elif n==46:
            for position in (3,4,5):
                chapter=save.story.chapters[module.MAIN_STORY_REAL_INDEX[position]]
                stages=chapter.get_valid_treasure_stages()
                if not stages:raise RuntimeError('未来編のステージ定義がありません。')
                for stage in stages:stage.itf_timed_score=roll(bounds)
        logs.append(f'メニュー{n}：{bounds[0]}〜{bounds[1]}を種類・ステージごとに抽選')
    for display_id in compiled.get('unlock_ids', []):
        cat = save.cats.get_cat_by_id(display_id - 1)
        if cat is None: raise RuntimeError(f'解放対象No.{display_id}がセーブにありません。')
        cat.unlock(save)
        if not cat.unlocked: raise RuntimeError('キャラ解放を検証できません。')
    if compiled.get('named_unlocks'):
        logs.append('指定キャラ解放：'+'・'.join(f'{target["name"]}(No.{target["id"]})' for target in compiled['named_unlocks']))
        if len(compiled.get('unlock_ids',[]))>len(compiled['named_unlocks']):logs.append(f'キャラ解放：合計{len(compiled["unlock_ids"])}体（レア度指定を含む）')
    elif compiled.get('unlock_ids'): logs.append(f'レア度別キャラ解放({len(compiled["unlock_ids"])}体)')
    counts, collabs = {}, {}
    for target in compiled.get('levels', []):
        if target['base'] is None and target['plus'] is None:continue
        cat = save.cats.get_cat_by_id(target['id'] - 1)
        if cat is None: raise RuntimeError(f"レベル対象No.{target['id']}がセーブにありません。")
        if not cat.unlocked: continue
        previous_plus, previous_base = cat.upgrade.plus, cat.upgrade.base
        if target['base'] is not None: cat.upgrade.base = target['base'] - 1
        if target['plus'] is not None:cat.upgrade.plus = target['plus']
        expected_plus = previous_plus if target['plus'] is None else target['plus']
        expected_base = previous_base if target['base'] is None else target['base'] - 1
        if (cat.upgrade.base, cat.upgrade.plus) != (expected_base, expected_plus):
            raise RuntimeError('キャラのレベル設定を検証できません。')
        rarity = target['rarity']; counts[rarity] = counts.get(rarity, 0) + 1
        if target['collab']: collabs[rarity] = collabs.get(rarity, 0) + 1
    for rarity, count in counts.items():
        logs.append(f'{RARITY_LABELS[rarity]}レベル指定({count}体・コラボ判定{collabs.get(rarity, 0)}体)')
    if compiled.get('level_exclusions'):logs.append('基本Lv・＋値を保持：'+'・'.join(match['name'] for match in compiled['level_exclusions']))
    applied = 0
    for target in compiled.get('talents', []):
        cat = save.cats.get_cat_by_id(target['id'] - 1)
        if cat is None: raise RuntimeError(f"本能対象No.{target['id']}がセーブにありません。")
        if not cat.unlocked: continue
        module._set_cat_talent_level(cat, target['talent_id'], target['level'])
        applied += 1
    if compiled.get('talents'): logs.append(f'実装本能全解放MAX({applied}個)')
    for orb_id, amount in compiled.get('orbs', {}).items(): save.talent_orbs.set_orb(orb_id, amount)
    if compiled.get('orbs'): logs.append(f"本能玉Sのみ({len(compiled['orbs'])}種類・各{next(iter(compiled['orbs'].values()))})")
    if compiled.get('helper_ids'):
        cls = module.core.game.gamoto.gamatoto.Helper
        save.gamatoto.helpers.helpers = [cls(member_id) for member_id in compiled['helper_ids']]
        logs.append('ガマトト隊員レジェンド10体')
    if compiled.get('labyrinth_amount') is not None and '82' not in ranges:
        if not hasattr(save, 'labyrinth_medals'): raise RuntimeError('地底迷宮勲章の保存領域がありません。')
        while len(save.labyrinth_medals) < 4: save.labyrinth_medals.append(0)
        save.labyrinth_medals[:4] = [compiled['labyrinth_amount']] * 4
        logs.append(f'地底迷宮勲章4種(各{compiled["labyrinth_amount"]})')
    for index, amount in compiled.get('fruits', {}).items():
        if not hasattr(save, 'catfruit') or index >= len(save.catfruit): raise RuntimeError('素材の保存領域が定義と一致しません。')
        save.catfruit[index] = amount
    if compiled.get('fruits'): logs.append(f'指定素材のみ({len(compiled["fruits"])}種類)')
    if 'gamatoto_xp' in compiled:
        save.gamatoto.xp = compiled['gamatoto_xp']
        logs.append('ガマトト指定レベル')
    return logs
