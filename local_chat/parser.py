"""Strict, local Japanese command parsing. No cloud model or API key required."""
import re
import unicodedata
import copy
import secrets
from .menus import MENU_BY_NUMBER, MAX_ITEM_AMOUNTS, MAX_CHARACTER_BASE, MAX_CHARACTER_PLUS
from .unlock_input import target_text

class ParseError(ValueError):
    pass

# These values map to apply_daiko_segment in the supplied main13.py.
RESOURCES = [
    (r"猫缶|ネコ缶", "catfood", "猫缶", MAX_ITEM_AMOUNTS['catfood']),
    (r"xp|経験値", "xp", "XP", 99999999),
    (r"np", "np", "NP", 9999),
    (r"銀チケ(?:ット)?|にゃんこチケット|通常チケ(?:ット)?", "normal_tickets", "銀チケット", 999),
    (r"金チケ(?:ット)?|レアチケ(?:ット)?", "rare_tickets", "レアチケット", 999),
    (r"プラチナ(?:チケ(?:ット)?)?|プラチケ(?:ット)?", "platinum_tickets", "プラチナチケット", MAX_ITEM_AMOUNTS['platinum_tickets']),
    (r"レジェンドチケ(?:ット)?|レジェチケ(?:ット)?", "legend_tickets", "レジェンドチケット", MAX_ITEM_AMOUNTS['legend_tickets']),
    (r"リーダーシップ", "leadership", "リーダーシップ", 999),
    (r"キャッツアイ", "catseyes", "キャッツアイ各種", 999),
    (r"ネコビタン", "catamins", "ネコビタン各種", 999),
    (r"城素材|城の素材", "base_materials", "城素材各種", 999),
    (r"(?:マタ|また)(?:タビ|たび)", "matatabi", "マタタビ各種", 998),
    (r"本能玉", "talent_orbs", "本能玉各種", 998),
    (r"戦闘アイテム|バトルアイテム", "battle_items", "バトルアイテム各種", MAX_ITEM_AMOUNTS['battle_items']),
]
RESOURCES = [(pattern, key, label, MAX_ITEM_AMOUNTS[key]) for pattern, key, label, _ in RESOURCES]
ITEM_KEYS = [x[1] for x in RESOURCES if x[1] not in {
    "normal_tickets", "rare_tickets", "platinum_tickets", "legend_tickets"}]
TICKET_KEYS = ["normal_tickets", "rare_tickets", "platinum_tickets", "legend_tickets"]
LABELS = {x[1]: (x[2], x[3]) for x in RESOURCES}

def normalize(text):
    return unicodedata.normalize("NFKC", text).lower().strip()

def has_edit_actions(p):
    return bool(p.get('conditional_rules') or any(p.get('selected',{}).values()) or p.get('level_rules') or
                any(amount!='keep' for amount in p.get('plus_only_rules',{}).values()) or
                p.get('orb_s_amount') is not None or
                any(p.get(key) for key in ['helpers_legend','talents_max','backup_requested','fruit_filters',
                                          'unlock_rarities','unlock_queries','future_score_settings','dojo_score_settings']) or
                p.get('labyrinth_amount') is not None or p.get('gamatoto_level') is not None)

def _parse_legacy(text, previous=None):
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise ParseError("依頼を1〜4000文字で入力してください。")
    prev = previous or {}
    result = {"transfer_code": prev.get("transfer_code", ""),
              "auth_code": prev.get("auth_code", ""),
              "selected": {k: list(v) for k, v in prev.get("selected", {}).items()},
              "custom_amounts": dict(prev.get("custom_amounts", {})),
              "labels": list(prev.get("labels", []))}
    s = normalize(text)
    # Explicit labels keep stray numbers from becoming account credentials.
    for pattern, key, valid, description in [
        (r"(?:引き継ぎ(?:コード)?|引継ぎ(?:コード)?|transfer(?:_code)?)[\s:：=]*([a-z0-9]+)",
         "transfer_code", r"[0-9a-f]{9}", "引き継ぎコードは9桁の0〜9・a〜fです。"),
        (r"(?:認証(?:番号|コード)?|auth(?:_code)?)[\s:：=]*([a-z0-9]+)",
         "auth_code", r"[0-9]{4}", "認証番号は4桁の数字です。"),
    ]:
        match = re.search(pattern, s)
        if match:
            if not re.fullmatch(valid, match.group(1)):
                raise ParseError(description)
            result[key] = match.group(1)
            s = s[:match.start()] + " " + s[match.end():]
    # A standalone number is accepted only when the Bot is waiting for that code.
    bare = s.strip()
    if re.fullmatch(r"[0-9a-f]{9}", bare) and not result["transfer_code"]:
        result["transfer_code"] = bare; s = ""
    elif re.fullmatch(r"[0-9]{4}", bare) and result["transfer_code"] and not result["auth_code"]:
        result["auth_code"] = bare; s = ""

    def add(group, key, label):
        bucket = result["selected"].setdefault(group, [])
        # Last explicit setting wins, including MAX versus numeric quantity.
        base = key.removeprefix("custom_")
        bucket[:] = [x for x in bucket if x.removeprefix("custom_") != base]
        bucket.append(key)
        result["labels"][:] = [x for x in result["labels"] if x["key"] != base]
        result["labels"].append({"key": base, "label": label})

    chunks = re.split(r"[\n、,;。]|(?:それから|そして|と(?=(?:猫缶|xp|np|キャラ|全キャラ|アイテム|チケット|施設|全ステージ)))", s)
    actions = [
        (r"(?:全(?:ての)?キャラ|キャラ(?:クター)?(?:全(?:て|部)?)?)(?:を)?全?解放", "s3", "unlock_all_cats", "キャラ全解放"),
        (r"(?:全(?:ての)?キャラ|キャラ(?:クター)?(?:全(?:て|部)?)?)(?:を)?(?:レベル)?(?:全(?:て|部)?)?max\+", "s3", "max_all_cats_plus", f"全キャラ Lv{MAX_CHARACTER_BASE}+{MAX_CHARACTER_PLUS}"),
        (r"(?:全(?:ての)?キャラ|キャラ(?:クター)?(?:全(?:て|部)?)?)(?:を)?(?:レベル)?(?:全(?:て|部)?)?max", "s3", "max_all_cats_plus", f"全キャラ Lv{MAX_CHARACTER_BASE}+{MAX_CHARACTER_PLUS}"),
        (r"全(?:ての)?ステージ(?:とお宝|\+お宝)?(?:を)?(?:全(?:て|部)?)?(?:クリア|max)", "s2", "all_stage_clear", "全ステージクリア・メインお宝MAX（既存定義）"),
        (r"真レジェンド(?:ステージ)?(?:を)?(?:全(?:て|部)?)?(?:クリア|max)", "s2", "true_legend_clear", "真レジェンドクリア"),
        (r"ゼロレジェンド(?:ステージ)?(?:を)?(?:全(?:て|部)?)?(?:クリア|max)", "s2", "zero_legend_clear", "ゼロレジェンドクリア"),
        (r"レジェンド(?:ステージ)?(?:を)?(?:全(?:て|部)?)?(?:クリア|max)", "s2", "legend_clear", "レジェンドクリア"),
        (r"(?:メイン(?:ストーリー)?|日本編・未来編・宇宙編)(?:を)?(?:全(?:て|部)?)?(?:クリア|max)", "s2", "main_story_clear", "メインクリア・お宝MAX"),
        (r"施設(?:を)?(?:全(?:て|部)?)?max", "s2", "base_upgrades_max", "施設MAX"),
        (r"オトート(?:全城)?(?:を)?(?:全(?:て|部)?)?max", "s4", "ototo_max", "オトート全城MAX"),
        (r"(?:にゃんこ)?神社(?:を)?max", "s4", "cat_shrine_max", "にゃんこ神社MAX"),
        (r"ガマトト(?:レベル|lv)?(?:を)?max", "s4", "gamatoto_max", "ガマトトLv.MAX"),
        (r"エラー(?:キャラ)?(?:を)?削除", "s3", "remove_error_cats", "エラーキャラ削除（既存定義）"),
    ]
    unknown = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk: continue
        # A phrase is consumed fully; modifiers left over prevent all execution.
        original = chunk
        broad = re.search(r"アイテム(?:を)?(?:全(?:て|部)?)?max", chunk)
        if broad and "戦闘アイテム" not in chunk:
            for key in ITEM_KEYS:
                label, amount = LABELS[key]; add("s1", key, f"{label}：{amount:,}")
            chunk = chunk[:broad.start()] + chunk[broad.end():]
        broad = re.search(r"チケット(?:類)?(?:を)?(?:全(?:て|部)?)?max(?:\(イベント以外\))?", chunk)
        if broad and not re.search(r"レア|プラチナ|レジェンド|銀|金", chunk):
            for key in TICKET_KEYS:
                label, amount = LABELS[key]; add("s1", key, f"{label}：{amount:,}")
            chunk = chunk[:broad.start()] + chunk[broad.end():]
        for pattern, group, key, label in actions:
            match = re.search(pattern, chunk)
            if match:
                if key in {"max_all_cats", "max_all_cats_plus"}:
                    result["selected"].setdefault("s3", [])[:] = [x for x in result["selected"].get("s3", []) if x not in {"max_all_cats", "max_all_cats_plus"}]
                    result["labels"][:] = [x for x in result["labels"] if x["key"] not in {"max_all_cats", "max_all_cats_plus"}]
                add(group, key, label)
                chunk = chunk[:match.start()] + chunk[match.end():]
        for aliases, key, label, default in RESOURCES:
            match = re.search(r"(?:"+aliases+r")(?:を)?\s*(?:全(?:て|部)?)?\s*(max|\d+(?:万\d*)?)(?:個|枚|に)?", chunk)
            if not match: continue
            quantity = match.group(1)
            if quantity == "max":
                add("s1", key, f"{label}：{default:,}")
                result["custom_amounts"].pop("custom_"+key, None)
            else:
                if "万" in quantity:
                    a, b = quantity.split("万")
                    if len(b)>4:raise ParseError('「万」の後の数値は0〜9999で指定してください。')
                    quantity = int(a)*10000+int(b or 0)
                quantity = int(quantity)
                if not 0 <= quantity <= 2_000_000_000:
                    raise ParseError(f"{label}の数が範囲外です。")
                add("s1", "custom_"+key, f"{label}：{quantity:,}")
                result["custom_amounts"]["custom_"+key] = quantity
            chunk = chunk[:match.start()] + chunk[match.end():]
        residue = re.sub(r"(?:にして|にする|して|してください|お願いします|お願い|を|と|全部|全て|!|！|\s)", "", chunk)
        if residue: unknown.append(original)
    if unknown:
        raise ParseError("読み取れない指定があります：" + " ／ ".join(unknown) + "。依頼例の表現で送り直してください。まだ実行していません。")
    result["missing"] = [label for key, label in [("transfer_code", "引き継ぎコード"), ("auth_code", "認証番号")] if not result[key]]
    if not result["labels"]: result["missing"].append("代行内容")
    result["ready"] = not result["missing"]
    return result

RARITIES = {'基本': 0, 'ex': 1, 'レア': 2, '激レア': 3, '超激レア': 4, '伝説': 5}
RARITY_LABELS = ['基本', 'EX', 'レア', '激レア', '超激レア', '伝説']
COLLAB_RARITY_SCOPE = r'(?:コラボ(?:系|キャラ)?(?:の)?(?:超激レア|激レア|伝説(?:レア)?|レア|基本|ex)(?:キャラ)?|(?:超激レア|激レア|伝説(?:レア)?|レア|基本|ex)(?:の)?コラボ(?:系|キャラ)?)'
HEADINGS = {'アイテム': 'items', 'チケット類': 'tickets', 'チケット': 'tickets',
            'キャラ類': 'cats', 'キャラ': 'cats', 'レベル変更': 'levels',
            'ステージ': 'stages', 'その他': 'other', 'ガマトト': 'gamatoto', '仕様': 'specs',
            '指定キャラ解放':'named_unlock','指定キャラ開放':'named_unlock',
            'レベル変更から除外':'level_exclusions','変更対象から除外':'level_exclusions'}


def split_clauses(text):
    """Punctuation inside an annotation stays with its instruction."""
    depth, start = 0, 0
    parts = []
    numeric_commas = {index for match in re.finditer(r'(?<!\d)\d{1,3}(?:,\d{3})+(?!\d)', text)
                      for index in range(match.start(), match.end()) if text[index] == ','}
    for i, char in enumerate(text):
        if char in '(（':
            depth += 1
        elif char in ')）':
            depth -= 1
            if depth < 0:
                raise ParseError('括弧の対応を確認してください。')
        elif depth == 0 and char in '、,;。' and i not in numeric_commas:
            parts.append(text[start:i]); start = i + 1
    if depth:
        raise ParseError('括弧の対応を確認してください。')
    parts.append(text[start:])
    return [x.strip() for x in parts if x.strip()]


def parse_request(text, previous=None):
    text = re.sub(r"(?m)^\s*コラボ(?:を)?含め(?:て)?全キャラ(?:を)?(?:解放|開放)(?:してください)?\s*$", "キャラ全解放", text) if isinstance(text,str) else text
    from .range_input import parse_range_line
    ranged = parse_range_line(text, previous, _parse_request_fixed)
    return ranged if ranged is not None else _parse_request_fixed(text, previous)

def _parse_request_fixed(text, previous=None):
    """Structured requests, precise quantities, exclusions and legacy commands.

    Parsing is transactional. A failed message never alters an earlier draft.
    Unknown specifications remain visible blockers, never silently skipped.
    """
    if not isinstance(text, str) or not text.strip() or len(text) > 16000:
        raise ParseError('依頼を1〜16000文字で入力してください。')
    p = copy.deepcopy(previous or {})
    for key, default in {'selected': {}, 'custom_amounts': {}, 'labels': [],
                         'level_rules': {}, 'spec_numbers': [], 'ban_requested': False,
                         'plus_only_rules': {}, 'preserve_base_levels': False,
                         'collab_plus_excluded_rarities': [],
                         'all_plus_excluded_rarities': [],
                         'orb_s_amount': None, 'custom_playtime': '',
                         'section': '', 'last_rarity': None,
                         'menu_numbers': [], 'menu_pending': {}, 'backup_requested': False,
                         'quoted_prices': [], 'menu_target_values': {},
                         'fruit_filters': {}, 'unlock_rarities': [],
                         'unlock_queries': [], 'unlock_resolved': [], 'unlock_issues': [],
                         'level_exclusion_queries': [], 'level_exclusion_resolved': [], 'level_exclusion_issues': [],
                         'level_exclusion_expected_count': None,
                         'labyrinth_amount': None, 'gamatoto_level': None,
                         'future_score_settings': None, 'dojo_score_settings': None,
                         'explicit_items': [], 'explicit_tickets': []}.items():
        p.setdefault(key, default)
    section = p['section']

    def label(key, value):
        p['labels'][:] = [x for x in p['labels'] if x['key'].removeprefix('custom_') != key]
        p['labels'].append({'key': key, 'label': value})

    def remove(key):
        for n, entry in MENU_BY_NUMBER.items():
            if entry.get("resource_key")==key or entry.get("filter")==key: clear_range(n)
        for bucket in p['selected'].values():
            bucket[:] = [x for x in bucket if x.removeprefix('custom_') != key]
        p['custom_amounts'].pop('custom_' + key, None)
        p['labels'][:] = [x for x in p['labels'] if x['key'].removeprefix('custom_') != key]
        if key == 'talent_orbs':
            p['orb_s_amount'] = None
            p['labels'][:] = [x for x in p['labels'] if x['key'] != 'orbs_s']
        if key == 'matatabi':
            p['fruit_filters'].pop('matatabi',None)
            p['labels'][:] = [x for x in p['labels'] if x['key'] != 'fruit_matatabi']

    def legacy(line):
        nonlocal p
        old = _parse_legacy(line, p)
        p.update(old)
        if re.search(r'アイテム(?:を)?(?:全(?:て|部)?)?max',line) and not re.search(r'戦闘|バトル',line):
            p['fruit_filters']['other_fruit'] = MAX_ITEM_AMOUNTS['stones']
            p['fruit_filters'].pop('stones',None)
            label('other_fruit',f'その他素材（獣石など）：各{MAX_ITEM_AMOUNTS["stones"]}')

    def resource(key, quantity='max', explicit=True):
        for n, entry in MENU_BY_NUMBER.items():
            if entry.get("resource_key")==key: clear_range(n)
        p['labels'][:] = [x for x in p['labels'] if x['key'] != 'exclude_' + key]
        if key == 'talent_orbs':
            p['orb_s_amount'] = None
            p['labels'][:] = [x for x in p['labels'] if x['key'] != 'orbs_s']
        # Use the literal, canonical name expected by the legacy worker.
        canonical = {'normal_tickets': '銀チケ', 'rare_tickets': 'レアチケ',
                     'platinum_tickets': 'プラチナ', 'legend_tickets': 'レジェンドチケ',
                     'battle_items': '戦闘アイテム'}.get(key, LABELS[key][0].replace('各種', ''))
        legacy(canonical + str(quantity))
        if explicit:
            field = 'explicit_tickets' if key in TICKET_KEYS else 'explicit_items'
            if key not in p[field]: p[field].append(key)
        elif key == 'matatabi':
            p['fruit_filters']['other_fruit'] = MAX_ITEM_AMOUNTS['stones']
            p['fruit_filters'].pop('stones',None)
            label('other_fruit',f'その他素材（獣石など）：各{MAX_ITEM_AMOUNTS["stones"]}')

    def exclude(keys):
        for key in keys:
            remove(key)
            label('exclude_' + key, LABELS[key][0] + '：変更しない（不要）')
            for field in ['explicit_items', 'explicit_tickets']:
                p[field][:] = [x for x in p[field] if x != key]
            numbers={number for number,entry in MENU_BY_NUMBER.items() if entry.get('resource_key')==key or (entry['kind']=='fruit_filter' and entry.get('filter')==key)}
            p['menu_numbers'][:]=[number for number in p['menu_numbers'] if number not in numbers]
            p['quoted_prices'][:]=[quote for quote in p['quoted_prices'] if not numbers.intersection(quote.get('numbers',[]))]

    def parse_resources(line):
        # Queue bare names so “猫缶、XPをMAX” shares the trailing quantity.
        pending = []
        for part in split_clauses(line):
            if part == 'その他不要':
                keys = TICKET_KEYS if section == 'tickets' else ITEM_KEYS
                field = 'explicit_tickets' if section == 'tickets' else 'explicit_items'
                exclude([key for key in keys if key not in p[field]])
                if section != 'tickets':
                    p['fruit_filters'].pop('other_fruit',None)
                    p['labels'][:] = [x for x in p['labels'] if x['key'] != 'other_fruit']
                continue
            if re.fullmatch(r'城素材(?:や|と)マタタビ(?:など)?(?:その他アイテム)?不要', part):
                keys = ['base_materials', 'matatabi']
                if 'その他アイテム' in part:
                    keys += [key for key in ITEM_KEYS if key not in p['explicit_items']]
                    p['fruit_filters'].pop('other_fruit',None)
                    p['labels'][:] = [x for x in p['labels'] if x['key'] != 'other_fruit']
                exclude(list(dict.fromkeys(keys))); continue
            if re.fullmatch(r'イベント(?:のみ)?(?:なし|不要)', part):
                # No event-ticket write is selected by this parser.
                label('exclude_event_tickets', 'イベントチケット：変更しない（不要）'); continue
            # Split only between known resource names. “ガマトト” isn't split.
            alternatives = '|'.join('(?:' + x[0] + ')' for x in RESOURCES)
            parts = re.split(r'(?:と|や)(?=(?:' + alternatives + r'))', part)
            for item in parts:
                found = None
                for aliases, key, _, _ in RESOURCES:
                    alias = aliases
                    if section == 'tickets' and key == 'rare_tickets': alias += '|レア'
                    m = re.fullmatch(r'(?:' + alias + r')(?:を)?\s*(?:全(?:て|部)?)?\s*(max|\d+(?:万\d*)?)?(?:個|枚|に)?\s*(不要|なし)?', item)
                    if m:
                        found = (key, m.group(1), m.group(2)); break
                if not found:
                    return False
                key, quantity, negative = found
                if negative:
                    if quantity: raise ParseError('数量と不要を同時に指定できません：' + item)
                    for bare_key in pending: resource(bare_key)
                    pending = []; exclude([key])
                elif quantity is None:
                    pending.append(key)
                else:
                    for target in pending + [key]: resource(target, quantity)
                    pending = []
        for key in pending: resource(key)
        return True

    def level_label(rarity):
        rule = p['level_rules'][str(rarity)]
        base = f"{rule['base'][0]}〜{rule['base'][1]}" if isinstance(rule['base'],list) else str(MAX_CHARACTER_BASE if rule['base'] == 'max' else rule['base'])
        specific = p['plus_only_rules'].get('rarity:' + str(rarity))
        plus = specific if specific is not None else rule['plus']
        all_override=p['plus_only_rules'].get('all') if rarity not in p['all_plus_excluded_rarities'] else None
        if specific is None and all_override is not None:plus=all_override
        if rule['base'] == 'keep':
            plus_text = str(MAX_CHARACTER_PLUS) if plus == 'max' else '維持' if plus == 'keep' else f'{plus[0]}〜{plus[1]}' if plus[0] != plus[1] else str(plus[0])
            value = f'{RARITY_LABELS[rarity]}：基本Lvは維持・＋{plus_text}'
        elif plus == 'keep':
            value = f'{RARITY_LABELS[rarity]}：Lv.{base}（＋値は維持）'
        else:
            plus_text = str(MAX_CHARACTER_PLUS) if plus == 'max' else f'{plus[0]}〜{plus[1]}' if plus[0] != plus[1] else str(plus[0])
            value = f'{RARITY_LABELS[rarity]}：Lv.{base}+{plus_text}'
        collab_value = rule.get('collab_plus') if all_override is None else None
        if p['plus_only_rules'].get('collab') and rarity not in p['collab_plus_excluded_rarities']:
            collab_value = p['plus_only_rules']['collab']
        scoped=p['plus_only_rules'].get('collab:'+str(rarity))
        if scoped is not None:collab_value=scoped
        if collab_value and (specific is None or scoped is not None):
            if collab_value == 'max': value += f'（コラボ＋{MAX_CHARACTER_PLUS}）'
            elif collab_value == 'keep': value += '（コラボの＋値は維持）'
            else:
                lo, hi = collab_value; value += f'（コラボ＋{lo}〜{hi}）'
        if isinstance(rule['base'],list): value += '（基本Lvは1体ごとに抽選）'
        if rule.get('plus_only_supported'): value += '（＋対応キャラのみ）'
        label('level_' + str(rarity), value)

    def replace_explicit_plus(rarities):
        for rarity in rarities:
            key = 'rarity:' + str(rarity)
            p['plus_only_rules'].pop(key,None)
            p['labels'][:]=[item for item in p['labels'] if item['key']!='plus_only_'+key]
            scoped='collab:'+str(rarity)
            p['plus_only_rules'].pop(scoped,None)
            p['labels'][:]=[item for item in p['labels'] if item['key']!='plus_only_'+scoped]
            if 'collab' in p['plus_only_rules'] and rarity not in p['collab_plus_excluded_rarities']:
                p['collab_plus_excluded_rarities'].append(rarity)
            if 'all' in p['plus_only_rules'] and rarity not in p['all_plus_excluded_rarities']:
                p['all_plus_excluded_rarities'].append(rarity)
        for scope,field in [('collab','collab_plus_excluded_rarities'),('all','all_plus_excluded_rarities')]:
            if set(p[field])==set(range(6)):
                p['plus_only_rules'].pop(scope,None)
                p['labels'][:]=[item for item in p['labels'] if item['key']!='plus_only_'+scope]
                p[field]=[]

    def parse_plus(value):
        if value == 'max' or not value: return 'max'
        if value == '維持': return 'keep'
        if value in {'なし', '不要'}: return [0, 0]
        m = re.fullmatch(r'(\d+)(?:[~〜-](\d+))?', value)
        if not m: raise ParseError('プラス値を読み取れません：' + value)
        lo, hi = int(m[1]), int(m[2] or m[1])
        if not 0 <= lo <= hi <= 32767: raise ParseError('プラス値の範囲を確認してください。')
        return [lo, hi]

    def action(group, key, value):
        bucket = p['selected'].setdefault(group, [])
        if key not in bucket: bucket.append(key)
        label(key, value)

    def number_amount(value, maximum, default=None):
        if value in ('', 'max'): return maximum if default is None else default
        if not re.fullmatch(r'\d+', value): raise ParseError('数量は数字またはMAXで指定してください。')
        amount = int(value)
        if not 0 <= amount <= maximum: raise ParseError(f'数量は0〜{maximum}で指定してください。')
        return amount

    def add_unlock(value):
        value=target_text(value)
        if not value or len(value)>1000:raise ParseError('指定キャラ名またはNo.番号を1〜1000文字で入力してください。')
        if value not in p['unlock_queries']:p['unlock_queries'].append(value)
        if len(p['unlock_queries'])>1000:raise ParseError('指定キャラは1000件以内で入力してください。')
        if 27 not in p['menu_numbers']:p['menu_numbers'].append(27)
        p['unlock_resolved']=[];p['unlock_issues']=[]
        p['menu_target_values']['27']='\n'.join(p['unlock_queries'])
        p['labels'][:]=[x for x in p['labels'] if not x['key'].startswith('unlock_named_')]
        p['menu_pending']['27']='指定キャラ解放：'+value+' — キャラ名・番号の照合が必要です。'
        label('menu_27',p['menu_pending']['27'])

    def add_level_exclusion(value):
        value=target_text(value)
        if not value or len(value)>1000:raise ParseError('除外するキャラ名・No.番号を1〜1000文字で指定してください。')
        if value not in p['level_exclusion_queries']:p['level_exclusion_queries'].append(value)
        p['level_exclusion_resolved']=[];p['level_exclusion_issues']=[]
        p['menu_pending']['level_exclusions']='レベル変更から除外するキャラ名の照合が必要です。'
        label('level_exclusions_pending',p['menu_pending']['level_exclusions'])

    def clear_range(number):
        p.setdefault("quantity_ranges",{}).pop(str(number),None)
        p["labels"][:]=[x for x in p["labels"] if x["key"]!=f"range_{number}"]

    def apply_menu(number, value=''):
        nonlocal section
        clear_range(number)
        entry = MENU_BY_NUMBER.get(number)
        if number not in p['menu_numbers']: p['menu_numbers'].append(number)
        if number >= 72 and number not in p['spec_numbers']: p['spec_numbers'].append(number)
        if entry is None:
            p['menu_pending'][str(number)] = f'メニュー{number}は定義表にありません。'
            label('menu_' + str(number), p['menu_pending'][str(number)]); return
        title = f'{number}. {entry["name"]}'
        kind = entry['kind']; p['menu_pending'].pop(str(number), None)
        if kind=='unlock_named':
            if value:add_unlock(value)
            elif not p['unlock_queries']:
                p['menu_pending']['27']='指定キャラ解放：キャラ名またはNo.番号を送ってください。'
                label('menu_27',p['menu_pending']['27'])
            section='named_unlock'
            return
        if section=='named_unlock':section=''
        if kind == 'needs_target':
            if value:
                p['menu_target_values'][str(number)] = value
                title += '（指定：' + value + '）'
            p['menu_pending'][str(number)] = title + ('：この対象を使う個別の実行接続は未対応です。' if value else '：個別の実行接続は未対応。' + entry['requires'])
            label('menu_' + str(number), p['menu_pending'][str(number)]); return
        if kind == 'resource':
            resource(entry['resource_key'], value or 'max')
        elif kind == 'action':
            if value not in ('', 'max'): raise ParseError(title + 'は数量指定を受け付けません。')
            if entry.get('exclusive_group'):
                for other in MENU_BY_NUMBER.values():
                    if other.get('exclusive_group') == entry['exclusive_group'] and other['number'] != number:
                        remove(other['action'])
                        p['menu_numbers'][:] = [n for n in p['menu_numbers'] if n != other['number']]
                        p['spec_numbers'][:] = [n for n in p['spec_numbers'] if n != other['number']]
            action(entry['segment'], entry['action'], title)
        elif kind == 'levels':
            match = re.fullmatch(r'(?:lv\.?|レベル)?(\d+|max)?(?:\+(max|\d+(?:[~〜-]\d+)?|なし))?', value)
            if not match: raise ParseError('レベルは例「メニュー29=40+70」で指定してください。')
            base = int(match[1]) if match[1] and match[1].isdigit() else 'max'
            p['preserve_base_levels']=False
            if match[2] is not None or base=='max': replace_explicit_plus(entry['rarities'])
            if isinstance(base, int) and not 1 <= base <= 32767: raise ParseError('レベルの範囲を確認してください。')
            for rarity in entry['rarities']:
                plus = parse_plus(match[2]) if match[2] is not None or base == 'max' else 'keep'
                p['level_rules'][str(rarity)] = {'base': base, 'plus': plus}
                level_label(rarity)
            for key in ['max_all_cats', 'max_all_cats_plus']: remove(key)
        elif kind == 'unlock_rarity':
            if value not in ('', 'max'): raise ParseError(title + 'は数量指定を受け付けません。')
            if entry['rarity'] not in p['unlock_rarities']: p['unlock_rarities'].append(entry['rarity'])
            label('unlock_rarity_' + str(entry['rarity']), title)
        elif kind == 'fruit_filter':
            amount = number_amount(value, 2_000_000_000, entry['default_quantity'])
            p['fruit_filters'][entry['filter']] = amount
            label('fruit_' + entry['filter'], title + f'：各{amount}')
        elif kind == 'facilities':
            if entry.get('fixed_level') is not None:
                if value not in ('', 'max', '30'): raise ParseError('61番はLv30です。別の値は62番で指定してください。')
                amount = entry['fixed_level']
            elif not value or value == 'max':
                remove('base_upgrades'); action('s2','base_upgrades_max',title + '：MAX'); return
            else: amount = number_amount(value, 32767)
            remove('base_upgrades_max')
            action('s2','custom_base_upgrades',title + f'：Lv{amount}')
            p['custom_amounts']['custom_base_upgrades'] = amount
        elif kind == 'backup':
            if value not in ('', 'max'): raise ParseError('64番は代行前バックアップの指定です。')
            p['backup_requested'] = True; p['ban_requested'] = False
            p['labels'][:] = [x for x in p['labels'] if x['key'] != 'ban_guarantee']
            label('backup_before', title + '（BANを防ぐ保証ではありません）')
        elif kind == 'labyrinth_medals':
            p['labyrinth_amount'] = number_amount(value, entry['max_quantity'], entry['default_quantity'])
            label('labyrinth_medals_detail', title + f'：全4種各{p["labyrinth_amount"]}')
        elif kind == 'gamatoto_level':
            if value in ('', 'max'):
                p['gamatoto_level'] = None; action('s4','gamatoto_max',title + '：MAX')
            else:
                level = number_amount(value, 32767)
                if level < 1: raise ParseError('ガマトトのレベルは1以上です。')
                remove('gamatoto_max'); p['gamatoto_level'] = level
                label('gamatoto_level_detail',title + f'：Lv{level}')
        elif kind == 'playtime':
            hours = number_amount(value, entry['max_hours'])
            p['custom_playtime'] = f'{hours}:00'
            action('s4','custom_playtime',title + f'：{hours}時間')
        elif kind == 'future_scores':
            score = number_amount(value, entry['max_score'])
            p['future_score_settings'] = {str(position): score for position in [3,4,5]}
            label('future_scores',title + f'：全章各ステージ{score}')
        elif kind == 'dojo_score':
            score = number_amount(value, entry['max_score'])
            p['dojo_score_settings'] = {'hall_of_initiates':score}
            label('dojo_score',title + f'：入門の間{score}')

    def remove_menu(number):
        clear_range(number)
        entry = MENU_BY_NUMBER.get(number)
        p['menu_pending'].pop(str(number),None)
        p['menu_target_values'].pop(str(number),None)
        p['quoted_prices'][:] = [row for row in p['quoted_prices'] if number not in row.get('numbers', [])]
        p['menu_numbers'][:] = [n for n in p['menu_numbers'] if n != number]
        p['spec_numbers'][:] = [n for n in p['spec_numbers'] if n != number]
        p['labels'][:] = [x for x in p['labels'] if x['key'] not in ('menu_'+str(number),'spec_'+str(number))]
        if not entry: return
        kind=entry['kind']
        if kind=='action': remove(entry['action'])
        elif kind=='resource': remove(entry['resource_key'])
        elif kind=='backup':
            p['backup_requested']=False
            p['labels'][:] = [x for x in p['labels'] if x['key']!='backup_before']
        elif kind=='labyrinth_medals':
            p['labyrinth_amount']=None
            p['labels'][:] = [x for x in p['labels'] if x['key']!='labyrinth_medals_detail']
        elif kind=='levels':
            replace_explicit_plus(entry['rarities'])
            numbers={n for n,e in MENU_BY_NUMBER.items() if e['kind']=='levels' and set(e['rarities'])<=set(entry['rarities'])}
            if set(entry['rarities'])==set(range(6)):numbers.update([35,36,37])
            p['menu_numbers'][:]=[n for n in p['menu_numbers'] if n not in numbers]
            p['quoted_prices'][:]=[q for q in p['quoted_prices'] if not numbers.intersection(q.get('numbers',[]))]
            for n in numbers:p['menu_pending'].pop(str(n),None);p['menu_target_values'].pop(str(n),None)
            p['labels'][:]=[item for item in p['labels'] if item['key'] not in {'menu_'+str(n) for n in numbers}]
            if set(entry['rarities'])==set(range(6)):
                for key in ['max_all_cats','max_all_cats_plus']:remove(key)
                p['preserve_base_levels']=False
            for rarity in entry['rarities']:
                p['level_rules'].pop(str(rarity),None)
                p['labels'][:] = [x for x in p['labels'] if x['key']!='level_'+str(rarity)]
        elif kind=='unlock_rarity':
            p['unlock_rarities'][:]=[r for r in p['unlock_rarities'] if r!=entry['rarity']]
            p['labels'][:] = [x for x in p['labels'] if x['key']!='unlock_rarity_'+str(entry['rarity'])]
        elif kind=='unlock_named':
            p['unlock_queries']=[];p['unlock_resolved']=[];p['unlock_issues']=[]
            p['labels'][:]=[x for x in p['labels'] if not x['key'].startswith('unlock_named_')]
        elif kind=='fruit_filter':
            p['fruit_filters'].pop(entry['filter'],None)
            p['labels'][:] = [x for x in p['labels'] if x['key']!='fruit_'+entry['filter']]
        elif kind=='playtime': p['custom_playtime']=''; remove('playtime'); remove('custom_playtime')
        elif kind=='future_scores': p['future_score_settings']=None; p['labels'][:]=[x for x in p['labels'] if x['key']!='future_scores']
        elif kind=='dojo_score': p['dojo_score_settings']=None; p['labels'][:]=[x for x in p['labels'] if x['key']!='dojo_score']
        elif kind=='gamatoto_level': p['gamatoto_level']=None; remove('gamatoto_max'); p['labels'][:]=[x for x in p['labels'] if x['key']!='gamatoto_level_detail']
        elif kind=='facilities': remove('base_upgrades'); remove('base_upgrades_max')

    # Headings work on their own line or inline. Unknown headings are errors.
    pieces = re.split(r'([【《][^】》]+[】》])', normalize(text))
    for piece in pieces:
        if not piece.strip(): continue
        if piece[0] in '【《':
            heading = piece[1:-1].strip()
            if heading not in HEADINGS: raise ParseError('未対応の見出し：' + heading)
            section = HEADINGS[heading]
            p['section'] = section
            if section=='named_unlock':apply_menu(27)
            if section=='level_exclusions' and not p['level_exclusion_queries']:
                p['menu_pending']['level_exclusions']='除外するキャラ名またはNo.番号を送ってください。'
            if section != 'levels': p['last_rarity'] = None
            continue
        lines = [part for raw in piece.splitlines() for part in (split_clauses(raw) if section == 'gamatoto' else [raw])]
        for line in lines:
            line = line.strip()
            if not line: continue
            if line=='レベル除外なし':
                p['level_exclusion_queries']=[];p['level_exclusion_resolved']=[];p['level_exclusion_issues']=[];p['level_exclusion_expected_count']=None
                p['menu_pending'].pop('level_exclusions',None)
                p['labels'][:]=[x for x in p['labels'] if not x['key'].startswith('level_exclusion')]
                section='';continue
            excluded=re.fullmatch(r'レベル除外=(.+)',line)
            if excluded:add_level_exclusion(excluded[1]);continue
            footer=re.fullmatch(r'上記(\d+)体は基本レベル・\+値ともに現在のまま',line)
            if footer:
                if not p['level_exclusion_queries']:raise ParseError('保持するキャラを先に指定してください。')
                p['level_exclusion_expected_count']=int(footer[1]);continue
            if section=='named_unlock' and line=='その他不要':
                raise ParseError('読み取れない指定：指定キャラを限定する場合は「指定キャラ解放を正式名に変更して」で対象を指定してください。')
            named_change=re.fullmatch(r'指定キャラ変更=(.+)',line)
            if named_change:
                value=target_text(named_change[1])
                remove_menu(27);apply_menu(27,value)
                continue
            # Credentials and submission messages always bypass section context.
            if re.match(r'(?:引き継ぎ|引継ぎ|transfer|認証|auth)', line) or re.fullmatch(r'[0-9a-f]{9}|\d{4}|お願いします|お願い|してください', line):
                legacy(line); continue
            if line in {'仕様不要', '仕様なし'}:
                for number in list(p['spec_numbers']): remove_menu(number)
                continue
            if re.fullmatch(r'ban保(?:証|障)(?:は)?(?:不要|なし)', line):
                p['ban_requested'] = False
                p['labels'][:] = [x for x in p['labels'] if x['key'] != 'ban_guarantee']
                remove_menu(64)
                continue
            if re.fullmatch(r'ban保(?:証|障)|代行前バックアップ', line):
                apply_menu(64)
                continue
            menu = re.fullmatch(r'(?:メニュー|番号|#)\s*(\d+)(?:\s*[:=]\s*(.*)|\s*(不要|なし))?',line)
            if menu:
                if menu[3]: remove_menu(int(menu[1]))
                else: apply_menu(int(menu[1]), (menu[2] or '').strip())
                continue
            named_menu = False
            for number, entry in MENU_BY_NUMBER.items():
                for name in [entry['name']] + entry.get('aliases', []):
                    if line == normalize(name):
                        apply_menu(number); named_menu = True; break
                if named_menu: break
            if named_menu: continue
            spec = re.fullmatch(r'(?:仕様\s*[:：]?\s*)?(\d+(?:[.、,\s]+\d+)*)', line)
            if spec and (section == 'specs' or line.startswith('仕様')):
                for number in map(int, re.findall(r'\d+', spec[1])):
                    apply_menu(number)
                continue
            play = re.fullmatch(r'プレイ時間(?:は|を|:|：)?\s*(\d+|max)?(?:[~〜-](\d+)(?:の間で(?:適当|ランダム))?)?\s*(?:時間|h)?', line)
            if play:
                lo = 9999 if not play[1] or play[1] == 'max' else int(play[1])
                hi = int(play[2]) if play[2] else lo
                if not 0 <= lo <= hi <= 9999: raise ParseError('プレイ時間は0〜9999時間で指定してください。')
                # Choose once during parsing; retries and incremental messages keep it.
                hours = lo + secrets.randbelow(hi - lo + 1)
                p['custom_playtime'] = f'{hours}:00'
                legacy('お願いします')
                p['selected'].setdefault('s4', [])[:] = [x for x in p['selected'].get('s4', []) if x != 'custom_playtime'] + ['custom_playtime']
                label('custom_playtime', f'プレイ時間：{hours}時間（指定{lo}〜{hi}）')
                continue
            orb = re.fullmatch(r'本能(?:玉)?\s*s(?:ランク)?(?:のみ|だけ)?\s*(max|\d+)?(?:個)?(?:\(その他不要\))?', line)
            if orb:
                amount = MAX_ITEM_AMOUNTS['talent_orbs'] if not orb[1] or orb[1] == 'max' else int(orb[1])
                if not 0 <= amount <= 2_000_000_000: raise ParseError('本能玉の数が範囲外です。')
                remove('talent_orbs'); p['orb_s_amount'] = amount
                p['labels'][:] = [x for x in p['labels'] if x['key'] != 'exclude_talent_orbs']
                if 'talent_orbs' not in p['explicit_items']: p['explicit_items'].append('talent_orbs')
                label('orbs_s', f'本能玉Sランクのみ：各{amount}（他ランクは変更しない）')
                continue
            # Category-specific levels and their parenthesized continuation lines.
            plus_only = re.fullmatch(r'('+COLLAB_RARITY_SCOPE+r'|全キャラ|コラボ(?:系|キャラ)?|超激レア|激レア|伝説(?:レア)?|レア|基本(?:キャラ)?|ex)(?:の|も|は)?\s*[:：]?\s*\(?(?:プラス(?:値)?|\+(?:値)?)(?:を|は)?\s*(max|維持|なし|\d+(?:[~〜-]\d+)?)\)?(?:の範囲で)?(?:ランダムに|に|おまかせ)?(?:設定|変更)?(?:してください)?', line)
            if plus_only:
                scope = plus_only[1]
                amount = parse_plus(plus_only[2])
                if scope=='全キャラ':
                    replace_explicit_plus(range(6))
                    keys=['all']
                    p['all_plus_excluded_rarities']=[]
                elif re.fullmatch(COLLAB_RARITY_SCOPE,scope):
                    name=re.sub(r'コラボ|キャラ|系|の','',scope).replace('伝説レア','伝説')
                    keys=['collab:'+str(RARITIES[name])]
                else:keys=['collab' if scope.startswith('コラボ') else 'rarity:' + str(RARITIES[scope.replace('キャラ','').replace('伝説レア','伝説')])]
                for key in keys:p['plus_only_rules'][key] = amount
                if 'collab' in keys:p['collab_plus_excluded_rarities']=[]
                continue
            if line=='その他素材不要':
                for kind in ['other_fruit','stones']:p['fruit_filters'].pop(kind,None)
                p['labels'][:]=[x for x in p['labels'] if x['key'] not in {'other_fruit','fruit_stones'}]
                remove_menu(14)
                label('exclude_other_fruit','その他素材（獣石など）：変更しない（不要）')
                continue
            if re.fullmatch(r'基本レベル(?:は|を)?(?:変更せず|変更しないでください|維持)(?:[、,]\s*\+(?:値)?だけ変更(?:してください)?)?|\+(?:値)?だけ変更(?:してください)?', line):
                p['preserve_base_levels'] = True
                continue
            lev = re.fullmatch(r'(超激レア|激レア|伝説(?:レア)?|レア|基本(?:キャラ)?|ex)(?:全(?:て|部)?)?(?:レベル|lv\.?|max)?\s*(\d+|max)?\s*(?:\+\s*(max|\d+(?:[~〜-]\d+)?|なし|不要))?\s*(\(.*\))?', line)
            if lev and (section in {'levels', 'cats'} or 'レベル' in line or 'lv' in line or '+' in line):
                name = lev[1].replace('キャラ', '').replace('伝説レア', '伝説')
                rarity = RARITIES[name]; annotation = lev[4] or ''
                base = int(lev[2]) if lev[2] and lev[2].isdigit() else 'max'
                p['preserve_base_levels']=False
                if lev[3] is not None or base=='max' or annotation:replace_explicit_plus([rarity])
                if isinstance(base, int) and not 1 <= base <= 32767: raise ParseError('レベルの範囲を確認してください。')
                if annotation and annotation != '(+付けられるものは全て+maxまで)':
                    raise ParseError('レベルの補足を読み取れません：' + annotation)
                plus = parse_plus(lev[3]) if lev[3] is not None or base == 'max' or annotation else 'keep'
                p['level_rules'][str(rarity)] = {'base': base, 'plus': plus}
                if annotation: p['level_rules'][str(rarity)]['plus_only_supported'] = True
                p['last_rarity'] = rarity
                for key in ['max_all_cats', 'max_all_cats_plus']: remove(key)
                level_label(rarity); continue
            collab = re.fullmatch(r'\(コラボ(?:系)?(?:は)?\s*\+(\d+(?:[~〜-]\d+)?)\)', line)
            if collab:
                rarity = p['last_rarity']
                if rarity is None or str(rarity) not in p['level_rules']:
                    raise ParseError('コラボの指定は、対象レア度のレベル指定の直後に書いてください。')
                p['level_rules'][str(rarity)]['collab_plus'] = parse_plus(collab[1])
                level_label(rarity); continue
            if re.fullmatch(r'\(\+\d+[~〜-]\d+のばらつき(?:が|か)不可であれば全て\+\d+\)', line):
                # Range mode is supported. Verify the comment describes that range.
                rarity = p['last_rarity']; numbers = list(map(int, re.findall(r'\d+', line)))
                if rarity is None or p['level_rules'][str(rarity)]['plus'] != numbers[:2] or numbers[2] != numbers[0]:
                    raise ParseError('範囲指定と補足の数値が一致しません。')
                continue
            broad = re.fullmatch(r'(アイテム|チケット(?:類)?)?(?:を)?(?:全(?:て|部)?)?(?:max)?(?:\(イベント以外\))?', line)
            if broad and line and (broad[1] or section in {'items', 'tickets'}):
                kind = 'tickets' if (broad[1] and 'チケット' in broad[1]) or (not broad[1] and section == 'tickets') else 'items'
                for key in TICKET_KEYS if kind == 'tickets' else ITEM_KEYS: resource(key, explicit=False)
                continue
            if section == 'cats' and re.fullmatch(r'(?:コラボ含め)?全(?:て|部)(?:解放|max)?', line):
                legacy('キャラ全解放'); continue
            if section == 'stages' and re.fullmatch(r'全(?:て|部)(?:クリア)?(?:お宝も(?:max)?)?', line):
                legacy('全ステージMAX'); continue
            if section == 'gamatoto' and re.fullmatch(r'(?:全(?:て|部))?レジェンド', line):
                p['helpers_legend'] = True
                label('helpers_legend', 'ガマトト隊員：レジェンド10体'); continue
            if re.fullmatch(r'(?:ガマトト)?(?:隊員)?(?:全(?:て|部)|10体)レジェンド', line):
                p['helpers_legend'] = True
                label('helpers_legend', 'ガマトト隊員：レジェンド10体'); continue
            if re.fullmatch(r'(?:キャラの)?本能(?:は)?(?:全(?:て|部))?(?:解放)?(?:max)?', line) and line:
                p['talents_max'] = True
                label('talents_all_detail', '全キャラ本能：実装された本能を全解放MAX'); continue
            aliases = {'施設': '施設MAX', 'オトート': 'オトートMAX', '城': 'オトートMAX',
                       '神社': '神社MAX', 'にゃんこ神社': '神社MAX', 'ガマトト': 'ガマトトMAX',
                       'キャラ': 'キャラMAX', '全ステージ': '全ステージMAX'}
            if re.fullmatch(r'城と神社(?:max)?', line):
                legacy('オトートMAX、神社MAX'); continue
            # Try item translation transactionally, so mixed actions fall back intact.
            saved = copy.deepcopy(p)
            if parse_resources(line):
                if section=='named_unlock':section=''
                continue
            p = saved
            if section=='level_exclusions' and not re.search(r'ステージ|クリア|お宝|本能|レベル|lv|max|チケット|不要|受取',line):
                add_level_exclusion(line);continue
            translated = '、'.join(aliases.get(part, part) for part in split_clauses(line))
            translated = re.sub(r'コラボ含め(?=キャラ)', '', translated)
            translated_parts = []
            for part in split_clauses(translated):
                if any(re.fullmatch(r'(?:' + aliases + r')(?:を)?', part) for aliases, *_ in RESOURCES): part += 'MAX'
                translated_parts.append(part)
            translated = '、'.join(translated_parts)
            try:legacy(translated)
            except ParseError as error:
                if section=='named_unlock' and str(error).startswith('読み取れない指定') and not re.search(r'ステージ|クリア|お宝|本能|レベル|lv|max|チケット|不要|受取',line):
                    add_unlock(line);continue
                raise
            if section=='named_unlock':section=''
            if re.search(r'キャラ(?:max|max\+)', translated):
                replace_explicit_plus(range(6))
                p['preserve_base_levels']=False
                p['level_rules'] = {}
                p['labels'][:] = [x for x in p['labels'] if not x['key'].startswith('level_')]
    if p['plus_only_rules'] or p['preserve_base_levels'] or p['level_exclusion_queries']:
        legacy_max = any(key in keys for keys in p['selected'].values() for key in ['max_all_cats','max_all_cats_plus'])
        if legacy_max:
            for key in ['max_all_cats','max_all_cats_plus']: remove(key)
            for rarity in range(6):
                p['level_rules'].setdefault(str(rarity),{'base':'max','plus':'max'})
                level_label(rarity)
        if p['preserve_base_levels']:
            for rarity, rule in p['level_rules'].items():
                rule['base'] = 'keep'; level_label(int(rarity))
        for rarity in p['level_rules']:level_label(int(rarity))
        for key,amount in p['plus_only_rules'].items():
            excluded=p['collab_plus_excluded_rarities'] if key=='collab' else p['all_plus_excluded_rarities'] if key=='all' else []
            rarities = [r for r in range(6) if r not in excluded] if key in {'collab','all'} else [int(key.split(':')[1])]
            base_change = any(p['level_rules'].get(str(r),{}).get('base','keep')!='keep' for r in rarities)
            note = '基本Lvはレベル指定に従う' if base_change else '基本Lvは維持'
            if excluded:
                note += '・' + '、'.join(RARITY_LABELS[r] for r in sorted(excluded)) + 'は除く'
            specific=[r for r in rarities if 'rarity:'+str(r) in p['plus_only_rules']]
            if key in {'all','collab'} and specific:
                note+='・'+'、'.join(RARITY_LABELS[r] for r in specific)+'は個別指定を優先'
            if key=='all' and 'collab' in p['plus_only_rules']:note+='・コラボは個別指定を優先'
            if key=='collab':
                scoped=[r for r in rarities if 'collab:'+str(r) in p['plus_only_rules']]
                if scoped:note+='・コラボの'+'、'.join(RARITY_LABELS[r] for r in scoped)+'は個別指定を優先'
            display='コラボキャラ' if key=='collab' else '全キャラ' if key=='all' else ('コラボの' if key.startswith('collab:') else '')+RARITY_LABELS[rarities[0]]
            value=str(MAX_CHARACTER_PLUS) if amount=='max' else '維持' if amount=='keep' else str(amount[0]) if amount[0]==amount[1] else f'{amount[0]}〜{amount[1]}'
            label('plus_only_'+key,display+('：＋値は維持' if amount=='keep' else '：＋'+value)+'（'+note+'）')
    p['section'] = section
    selected_resources = {key for keys in p['selected'].values() for key in keys}
    if p['fruit_filters'].get('other_fruit') is not None or p['fruit_filters'].get('stones') is not None:
        p['labels'][:]=[item for item in p['labels'] if item['key']!='exclude_other_fruit']
    if 'matatabi' in selected_resources:
        p['fruit_filters']['matatabi'] = MAX_ITEM_AMOUNTS['matatabi']
    elif 'custom_matatabi' in selected_resources:
        p['fruit_filters']['matatabi'] = p['custom_amounts']['custom_matatabi']
    blockers = []
    blockers.extend(p['menu_pending'].values())
    p['blockers'] = blockers
    p['missing'] = [name for key, name in [('transfer_code', '引き継ぎコード'), ('auth_code', '認証番号')] if not p.get(key)]
    has_actions = has_edit_actions(p)
    if not has_actions and not p['menu_pending']: p['missing'].append('代行内容')
    p['ready'] = not p['missing'] and not blockers
    return p
