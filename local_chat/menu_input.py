"""Ground sales-menu text while keeping quoted yen prices separate from edit values."""
import re
import unicodedata
from .menus import MENU_BY_NUMBER


EXTRA_NAMES = {
    4: ['猫缶', 'ネコ缶', 'ネコ缶指定'], 5: ['XP', '経験値', '経験値指定'],
    6: ['NP'], 7: ['リーダーシップ'], 8: ['バトルアイテム', '戦闘アイテム'],
    9: ['キャッツアイ'], 10: ['ネコビタン'], 11: ['城素材', '城素材指定'],
    12: ['マタタビ', 'マタタビ全種'], 13: ['本能玉'], 14: ['獣石指定'],
    15: ['通常チケット', '銀チケット', '銀チケット指定'],
    16: ['レアチケット', 'レアチケ', 'レアチケ指定'],
    17: ['プラチナチケット指定', 'プラチナチケット', 'プラチケ'],
    18: ['レジェンドチケット指定', 'レジェンドチケット', 'レジェチケ'],
    20: ['キャラ全解放'], 27: ['指定キャラ1体','指定キャラ解放','指定キャラ開放','キャラ解放指定'],
    29: ['キャラレベル変更', '全キャラレベル', 'キャラレベル'],
    30: ['EXレベル', 'EXレベル変更'], 31: ['レアレベル', 'レアレベル変更'],
    32: ['激レアレベル', '激レアレベル変更'], 33: ['超激レアレベル', '超激レアレベル変更'],
    34: ['伝説レアレベル', '伝説レベル', '伝説レベル変更'],
    35: ['コラボ別レベル変更'], 36: ['指定キャラ1体レベル変更'],
    44: ['未来編採点スコア指定'], 45: ['未来編採点スコア指定'],
    46: ['未来編採点スコア指定'],
    55: ['旧レジェンドクリア', 'レジェンドクリア'],
    56: ['真レジェンドクリア'], 57: ['零レジェンドクリア', 'ゼロレジェンドクリア'],
    66: ['ガマトトレベル'], 69: ['オトート全城MAX'], 71: ['にゃんこ神社MAX'],
    84: ['プレイ時間'],
}
NUMBER = r'(?:\d{1,3}(?:,\d{3})+|\d+)'
PRICE = re.compile(r'(.+?)(?:\s*[:：]\s*|\s*)(' + NUMBER + r')\s*円\s*[。.!⚡]*$')
PRICE_BRACKET = re.compile(r'(.+?)\s*\(\s*(' + NUMBER + r')\s*円\s*\)\s*[。.!⚡]*$')
QUANTITY_KINDS = {'resource', 'fruit_filter', 'levels', 'facilities', 'labyrinth_medals',
                  'gamatoto_level', 'playtime', 'future_scores', 'dojo_score'}


def normalize_menu_name(text):
    value = unicodedata.normalize('NFKC', text).casefold().strip()
    value = value.replace('開放', '解放').replace('全種類', '全種')
    value = re.sub(r'(?:また|マタ)(?:たび|タビ)', 'マタタビ', value)
    return re.sub(r'\s+', '', value)


NAME_INDEX = {}
for number, entry in MENU_BY_NUMBER.items():
    for name in [entry['name']] + entry.get('aliases', []) + EXTRA_NAMES.get(number, []):
        for variant in {name, name.replace('の', '')}:
            NAME_INDEX.setdefault(normalize_menu_name(variant), set()).add(number)
for name in ['隊員追加・クラスアップ', '隊員追加とクラスアップ']:
    NAME_INDEX[normalize_menu_name(name)] = {67, 68}


def _argument(entry, text):
    if entry['kind'] not in QUANTITY_KINDS:
        return text.strip()
    value = unicodedata.normalize('NFKC', text).casefold().strip()
    value = re.sub(r'^(?:数量|個数|枚数|レベル|lv\.?|時間|スコア)\s*[:=]?\s*', '', value)
    value = re.sub(r'(?:個|枚|時間|h)$', '', value).strip()
    if entry['kind'] == 'playtime' and re.fullmatch(r'\d+[〜~-]\d+', value):
        return value.replace('~', '〜').replace('-', '〜')
    return value.replace(',', '') if re.fullmatch(NUMBER, value) else value


def parse_menu_line(text):
    """Return a grounded transformation, or None without dropping any unknown text."""
    original = text.strip()
    value = unicodedata.normalize('NFKC', original).strip()
    value = re.sub(r'^(?:[-・•]\s+)', '', value)
    price = None
    priced = PRICE_BRACKET.fullmatch(value) or PRICE.fullmatch(value)
    if priced:
        value, amount = priced[1].strip(), priced[2]
        amount = amount.replace(',', '')
        if len(amount) > 15: return None
        price = int(amount)
    # An equals sign is an explicit value, not a price separator.
    if value.endswith('='):
        return None
    numbered = re.fullmatch(r'(?:メニュー|番号|#)\s*(\d+)(?:\s*=\s*(.*))?', value)
    prefix = re.fullmatch(r'(\d+)\s*[.．、)]\s*(.+)', value)
    explicit_number = int(numbered[1]) if numbered else int(prefix[1]) if prefix else None
    if prefix:
        value = prefix[2].strip()
    negative = value.endswith('不要')
    if negative: value = value[:-2].strip()
    menu_name = value
    numbers = NAME_INDEX.get(normalize_menu_name(value), set())
    argument = ''
    parenthesized = False
    if not numbers:
        bracket = re.fullmatch(r'(.+?)\s*\(([^()]*)\)', value)
        if bracket:
            name, argument = bracket[1].strip(), bracket[2].strip()
            menu_name = name
            numbers = NAME_INDEX.get(normalize_menu_name(name), set())
            parenthesized = bool(numbers)
        if not numbers:
            typed = re.fullmatch(r'(.+?)\s*[:=]?\s*(max|\d+(?:,\d{3})*(?:\+(?:max|\d+|なし))?)(?:個|枚|時間|h)?', value, re.I)
            if typed:
                candidates = NAME_INDEX.get(normalize_menu_name(typed[1]), set())
                if candidates and all(MENU_BY_NUMBER[n]['kind'] in QUANTITY_KINDS for n in candidates):
                    menu_name, argument, numbers = typed[1].strip(), typed[2], candidates
                    parenthesized = True
    if numbered and explicit_number in MENU_BY_NUMBER:
        numbers = {explicit_number}
        if numbered[2] is not None:
            argument = numbered[2]
            parenthesized = True
    elif explicit_number is not None:
        if explicit_number not in numbers:
            return None
        numbers = {explicit_number}
    combined = normalize_menu_name(menu_name) in {
        normalize_menu_name('隊員追加・クラスアップ'), normalize_menu_name('隊員追加とクラスアップ')}
    if combined:
        numbers = {67, 68}
    if not numbers:
        return None
    numbers = sorted(numbers)
    # Bare resource names retain the strict parser's shared-quantity semantics.
    if price is None and not parenthesized and explicit_number is None and not combined and not negative:
        if all(MENU_BY_NUMBER[n]['kind'] in {'resource', 'fruit_filter', 'playtime'} for n in numbers):
            return None
    ambiguity = len(numbers) > 1 and not combined and not negative
    if ambiguity:
        key = 'name:' + normalize_menu_name(value)
        choices = '・'.join(f'{n}（{MENU_BY_NUMBER[n]["name"]}）' for n in numbers)
        reason = value + '：対象の指定が必要です。' + choices + ' から選んでください。'
        return {'command': 'お願いします', 'numbers': [], 'candidates': numbers,
                'name': value, 'price_jpy': price, 'pending_key': key,
                'pending_reason': reason, 'input': original}
    commands = []
    for number in numbers:
        entry = MENU_BY_NUMBER[number]
        parameter = _argument(entry, argument) if parenthesized else ''
        if negative or parameter in {'不要', 'なし', '変更しない'}:
            commands.append('メニュー' + str(number) + '不要')
        elif entry['kind'] == 'playtime' and re.fullmatch(r'\d+[〜~-]\d+', parameter):
            commands.extend(['メニュー' + str(number), 'プレイ時間' + parameter])
        else:
            commands.append('メニュー' + str(number) + ('=' + parameter if parameter else ''))
    return {'command': '\n'.join(commands), 'numbers': numbers, 'candidates': [],
            'name': '・'.join(MENU_BY_NUMBER[n]['name'] for n in numbers),
            'price_jpy': price, 'input': original}


def inline_collab_annotation(text):
    """Keep the category and its collaboration exception together when written inline."""
    value = unicodedata.normalize('NFKC', text).strip()
    category = re.fullmatch(r'((?:基本(?:キャラ)?|EX|レア|激レア|超激レア|伝説(?:レア)?)(?:全(?:て|部))?(?:レベル|lv\.?)?)\s*\((\d+|max)(\+(?:max|なし|\d+(?:[〜~-]\d+)?))?\)', value, re.I)
    if category and re.search(r'レベル|lv', category[1], re.I):
        return category[1] + category[2] + (category[3] or '')
    match = re.fullmatch(r'(.+?)\s*\(コラボ(?:系)?(?:は|のみ)?\s*\+(\d+(?:[〜~-]\d+)?)\)', value)
    if not match or not re.match(r'^(?:レア|激レア|超激レア|伝説|EX|基本)', match[1], re.I):
        return None
    return match[1] + '\n(コラボ系は+' + match[2] + ')'
