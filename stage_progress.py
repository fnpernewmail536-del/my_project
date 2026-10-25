"""Exact progress for verified story, outbreak, Aku and event stage definitions."""
import copy

KINDS = {'event', 'tower', 'catamin_stages', 'legend_quest', 'gauntlets',
         'enigma_clears', 'collab_gauntlets', 'behemoth_culling', 'labyrinth', 'dojo_chapters'}


def valid_shape(raw):
    if not isinstance(raw, list) or len(raw) > 10000:
        return False
    seen = set()
    for row in raw:
        if (not isinstance(row, dict) or set(row) != {'family', 'map', 'star', 'progress'}
                or not isinstance(row['family'], str) or len(row['family']) > 40
                or any(type(row[key]) is not int for key in ('map', 'star', 'progress'))
                or not 0 <= row['map'] < 1000 or not 0 <= row['star'] < 4
                or not 0 <= row['progress'] <= 256):
            return False
        key = row['family'], row['map'], row['star']
        if key in seen:
            return False
        seen.add(key)
    return True


def manifest(module, metadata, family, map_id):
    if family in ('main_story', 'zombie', 'aku', 'aku_ex'):
        spec = next((m for m in metadata.get(family, {}).get('chapters', []) if m['id'] == map_id), None)
        if spec is None:
            raise ValueError('対象章のステージ定義を読み込めません：' + family)
        result = {**copy.deepcopy(spec), 'family': family, 'crowns': 1, 'kind': family}
        if family in ('main_story', 'zombie'):
            if not 0 <= map_id < len(module.MAIN_STORY_REAL_INDEX):
                raise ValueError('本編の章番号が不正です。')
            result['real_id'] = module.MAIN_STORY_REAL_INDEX[map_id]
        if family == 'aku_ex':
            if map_id != 0 or len(spec['stages']) != 1:
                raise ValueError('富士山EXの保存先を照合できません。')
            result.update(kind='event', group=1, id=42, base_index=4000)
    elif family.startswith('event:'):
        key = family[6:]
        connection = module.EVENT_STAGE_FAMILIES.get(key, {})
        spec = next((m for m in metadata.get('event_stage_families', {}).get(key, {}).get('maps', [])
                     if m['id'] == map_id), None)
        if connection.get('kind') not in KINDS or spec is None:
            raise ValueError('対象イベントの定義・保存先を照合できません：' + key)
        result = {**copy.deepcopy(spec), **copy.deepcopy(connection), 'family': family}
    else:
        raise ValueError('未対応のステージ系統です：' + family)
    if not result.get('stages') or not 1 <= len(result['stages']) <= 256 or not 1 <= result['crowns'] <= 4:
        raise ValueError('ステージ数・星の定義が不正です。')
    if result['kind'] == 'labyrinth' and (map_id != 0 or result['crowns'] != 1):
        raise ValueError('地底迷宮の対象定義を照合できません。')
    return result


def compile_targets(module, raw):
    if not valid_shape(raw):
        raise ValueError('ステージ進行指定の型・範囲・重複を確認してください。')
    if not raw:
        return []
    metadata = module.get_legend_metadata()
    result, locations, count = [], {}, 0
    for row in raw:
        spec = manifest(module, metadata, row['family'], row['map'])
        if row['star'] >= spec['crowns'] or row['progress'] > len(spec['stages']):
            raise ValueError(spec['name'] + 'の星・進行が実装範囲を超えています。')
        location = spec['kind'], spec.get('group'), spec['id'], row['star']
        if location in locations:
            old = locations[location]
            if old['progress'] != row['progress'] or len(old['spec']['stages']) != len(spec['stages']):
                raise ValueError('同じ保存先への進行指定が競合しています：' + spec['name'])
            continue
        target = {**copy.deepcopy(row), 'spec': spec}
        locations[location] = target
        result.append(target)
        count += len(spec['stages'])
    if count > 50000:
        raise ValueError('ステージ進行の対象が多すぎます。系統を分けて指定してください。')
    return result


def chapter_for(save, spec, star):
    kind = spec['kind']
    if kind == 'main_story':
        return save.story.chapters[spec['real_id']]
    if kind == 'zombie':
        return save.outbreaks.chapters[spec['real_id']]
    if kind == 'aku':
        return save.aku.chapters[0].chapters[0]
    if kind == 'event':
        container = save.event_stages.chapters[spec['group']]
    elif kind == 'tower':
        container = save.tower.chapters
    elif kind == 'catamin_stages':
        container = save.catamin_stages.chapters
    else:
        container = getattr(save, kind)
    return container.chapters[spec['id']].chapters[star]


def labyrinth_state(module, save):
    return tuple(module._labyrinth_value(save, names, 0) for names in (
        ('current_floor', 'cleared_floor', 'ush4'), ('highest_floor', 'max_cleared_floor', 'ush5'),
        ('display_floor', 'progress_floor', 'ush7'), ('remaining_floor_index', 'uby10'))) + (tuple(save.stage_ids_10s),)


def set_labyrinth(module, save, n, total):
    # Only canonical IDs for verified floors belong to this change.
    known = {33000000 + i * 10 for i in range(total)}
    save.stage_ids_10s = [key for key in save.stage_ids_10s if key not in known]
    save.stage_ids_10s.extend(33000000 + i * 10 for i in range(n))
    for names in (('current_floor', 'cleared_floor', 'ush4'), ('highest_floor', 'max_cleared_floor', 'ush5'),
                  ('display_floor', 'progress_floor', 'ush7')):
        module._set_labyrinth_value(save, names, n)
    module._set_labyrinth_value(save, ('remaining_floor_index', 'uby10'), max(-128, min(127, total - 1 - n)))


def apply_targets(module, save, plans):
    if not plans:
        return []
    raw = [{key: row[key] for key in ('family', 'map', 'star', 'progress')} for row in plans]
    if compile_targets(module, raw) != plans:
        raise ValueError('ステージ定義が変更されました。画面を再読込し、進行指定を確認してください。')
    from local_chat.targeted_engine import ensure_slots
    events = [row['spec'] for row in plans if row['spec']['kind'] in KINDS]
    ensure_slots(module, save, events)
    if any(row['spec']['kind'] == 'aku' for row in plans):
        total = max(len(row['spec']['stages']) for row in plans if row['spec']['kind'] == 'aku')
        lib = module.core.game.map.aku
        if not save.aku.chapters:
            save.aku.chapters.append(lib.ChaptersStars.init(total, 1))
        total = max([total] + [len(c.stages) for m in save.aku.chapters for c in m.chapters])
        for item in save.aku.chapters:
            if not item.chapters:
                item.chapters.append(lib.Chapter.init(total))
            for chapter in item.chapters:
                while len(chapter.stages) < total:
                    chapter.stages.append(lib.Stage.init())
    protected = {name: copy.deepcopy(getattr(save, name).serialize())
                 for name in ('cats', 'lineups', 'medals', 'item_reward_stages')}
    expected, logs, changed_maps = [], [], {}
    progress_by_location = {(r['spec']['kind'], r['spec'].get('group'), r['spec']['id'], r['star']): r['progress'] for r in plans}
    for row in plans:
        spec, star, n = row['spec'], row['star'], row['progress']
        kind, total = spec['kind'], len(spec['stages'])
        if kind == 'labyrinth':
            set_labyrinth(module, save, n, total)
            expected.append((row, labyrinth_state(module, save)))
        elif kind == 'zombie':
            lib = module.core.game.map.outbreaks
            chapter = save.outbreaks.chapters.setdefault(spec['real_id'], lib.Chapter.init(spec['real_id']))
            current = save.outbreaks.current_outbreaks.get(spec['real_id'])
            for i in range(total):
                chapter.outbreaks[i] = lib.Outbreak(i < n)
                if current is not None:
                    current.outbreaks.pop(i, None)
            if current is not None and not current.outbreaks:
                save.outbreaks.current_outbreaks.pop(spec['real_id'], None)
            expected.append((row, copy.deepcopy(chapter.serialize())))
        else:
            chapter = chapter_for(save, spec, star)
            if len(chapter.stages) < total:
                raise ValueError('対象の保存枠が不足しています：' + spec['name'])
            for i, stage in enumerate(chapter.stages[:total]):
                field = 'clear_amount' if hasattr(stage, 'clear_amount') else 'clear_times'
                setattr(stage, field, max(1, getattr(stage, field)) if i < n else 0)
                if kind == 'legend_quest' and i >= n:
                    stage.tries = 0
            if kind == 'main_story':
                chapter.progress = n
                chapter.selected_stage = min(max(0, chapter.selected_stage), min(n, total - 1))
            elif kind == 'aku':
                chapter.current_stage = max(0, n - 1)
            else:
                chapter.clear_progress = n
                chapter.selected_stage = min(max(0, chapter.selected_stage), min(n, total - 1))
                if n or star == 0:
                    chapter.chapter_unlock_state = 3 if n else 1
                else:
                    previous = progress_by_location.get((kind, spec.get('group'), spec['id'], star - 1))
                    if previous is None:
                        previous = sum(getattr(s, 'clear_amount', getattr(s, 'clear_times', 0)) > 0
                                       for s in chapter_for(save, spec, star - 1).stages[:total])
                    chapter.chapter_unlock_state = int(previous == total)
                if kind == 'dojo_chapters':
                    chapter.unlock_state = chapter.chapter_unlock_state
                changed_maps[(kind, spec.get('group'), spec['id'])] = spec
            expected.append((row, copy.deepcopy(chapter.serialize())))
        logs.append(f'{spec["name"]}' + (f' ★{star + 1}' if spec['crowns'] > 1 else '') + f'：{n}/{total}ステージまで（以降は未クリア）')
    counts = save.event_stages.chapter_completion_count
    expected_counts = {}
    for spec in changed_maps.values():
        key = spec.get('base_index', 0) + spec['id']
        if key not in counts and spec['kind'] != 'catamin_stages':
            continue
        complete = any(all(getattr(s, 'clear_amount', getattr(s, 'clear_times', 0)) > 0
                           for s in chapter_for(save, spec, star).stages[:len(spec['stages'])])
                       for star in range(spec['crowns']))
        counts[key] = max(1, counts.get(key, 0)) if complete else 0
        expected_counts[key] = counts[key]
    loaded = module.core.SaveFile(module.core.Data(save.to_data().data), cc=save.cc)
    if loaded.game_version.game_version != save.game_version.game_version:
        raise RuntimeError('進行変更後の保存版数が一致しません。')
    for row, values in expected:
        actual = (labyrinth_state(module, loaded) if row['spec']['kind'] == 'labyrinth'
                  else chapter_for(loaded, row['spec'], row['star']).serialize())
        if actual != values:
            raise RuntimeError('ステージ進行の保存・再読込が一致しません。送信を停止しました。')
    for name, values in protected.items():
        if getattr(loaded, name).serialize() != values:
            raise RuntimeError('進行変更でキャラ・編成・取得履歴が変化したため送信を停止しました。')
    if any(loaded.event_stages.chapter_completion_count.get(k) != v for k, v in expected_counts.items()):
        raise RuntimeError('ステージの表示回数を保存できませんでした。')
    return logs
