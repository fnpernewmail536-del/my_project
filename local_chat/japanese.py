"""Bounded Japanese rewrites. Never invent missing targets or quantities."""
import re
import unicodedata
from difflib import SequenceMatcher

FIELDS = ('猫缶','XP','NP','リーダーシップ','バトルアイテム','キャッツアイ',
          'ネコビタンA','ネコビタンB','ネコビタンC','トレジャーレーダー','ニャンピュータ',
          'プラチナのかけら','通常チケット','レアチケット','プラチケ','レジェチケ',
          'マタタビ','獣石','本能玉','プレイ時間','ガマトト')
SUBJECT = '(?:' + '|'.join(map(re.escape, sorted(FIELDS, key=len, reverse=True))) + ')'
ALIASES = {'ねこ缶':'猫缶','猫カン':'猫缶','猫かん':'猫缶','ネコ缶':'猫缶',
           'にゃんこ缶':'猫缶','経験値':'XP','エックスピー':'XP','エヌピー':'NP',
           'にゃんぴゅーた':'ニャンピュータ','にゃんぴゅーたー':'ニャンピュータ',
           'トレジャーレイダー':'トレジャーレーダー','銀チケット':'通常チケット',
           '金チケット':'レアチケット','レジェンドチケ':'レジェチケ',
           'プラチナチケ':'プラチケ','にゃんこチケット':'通常チケット'}
DIGITS = dict(zip('〇零一二三四五六七八九', (0,0,1,2,3,4,5,6,7,8,9)))


def kanji_number(s):
    total = section = value = 0
    after_big = False
    for i,c in enumerate(s):
        if c in DIGITS or c.isdigit():
            value = value * 10 + (DIGITS[c] if c in DIGITS else int(c))
        elif c in '十百千':
            section += (value or 1) * {'十':10,'百':100,'千':1000}[c]
            value = 0
        else:
            total += (section + value if i else 1) * {'万':10000,'億':100000000}[c]
            section = value = 0
            after_big = True
    if after_big and section+value >= 10000:
        raise ValueError('万・億より下の桁が曖昧です。')
    return str(total + section + value)


def number_match(m):
    try:return m[1]+kanji_number(m[2])
    except ValueError:return m[0]


def rewrite(text):
    if re.match(r'未解釈項目\d+', text.strip()):
        return text, []
    changes = []
    result = []
    for original in text.splitlines():
        # Credentials are validated separately, never fuzzy-matched or echoed.
        if re.search(r'引き?継ぎ|引継ぎ|認証|transfer|auth', original, re.I):
            result.append(original)
            continue
        if '円' in original or ('プレイ時間' in original and re.search('から|ランダム', original)):
            result.append(original)
            continue
        s = original
        for alias in sorted(ALIASES, key=len, reverse=True):
            s = re.sub(re.escape(alias)+(r'(?!ット)' if alias.endswith('チケ') else ''), ALIASES[alias], s)
        s = re.sub(r'^(?:えっと|えーと|すみませんが|じゃあ|それでは|とりあえず)[、,\s]*', '', s)
        s = re.sub(r'^(?:あと|それと|ついでに)[、,\s]+', '', s)
        s = re.sub(r'^(基本|EX|レア|激レア|超激レア|伝説レア)(?:キャラ)?は基本レベル(\d+)で[、,]\s*(?:プラス(?:値)?|[+＋]値)は?(\d+)(?:にして(?:ください)?|でお願いします)?[。？！?!]*$',
                   r'\1はレベル\2+\3', s)
        if not re.search(r'なら|場合|とき|時', s):
            s = re.sub(r'((?:\d|MAX|max)[^、,\n]*?)[、,]\s*(?='+SUBJECT+r'(?:は|を|:))', r'\1\n', s)
        s = re.sub(r'(?<=[。！？!?])\s*(?='+SUBJECT+r')', '\n', s)
        parts = []
        for part in s.split('\n'):
            # Kanji numbers only immediately after a recognized resource name.
            part = re.sub('('+SUBJECT+r'(?:は|を|:)?\s*)([〇零一二三四五六七八九十百千万億]+)',
                          number_match, part)
            part = re.sub('('+SUBJECT+r'(?:は|を|:)?\s*)([0-9]+[万億千百十][0-9〇零一二三四五六七八九十百千万億]*)',
                          number_match, part)
            part = re.sub(r'(\d+)(?:個|枚)?\s*(?:から|以上)\s*(\d+)(?:個|枚)?\s*(?:の間|以下)', r'\1〜\2の範囲', part)
            part = re.sub(r'(\d+)\s*から\s*(\d+)(?=\s*(?:個|枚|に|で|まで|の範囲|ランダム))', r'\1〜\2', part)
            part = re.sub(r'(?<=\d)まで(?=.*(?:ランダム|おまかせ))', 'の範囲で', part)
            part = re.sub(r'(?<=\d)(?:個|枚)ずつ(?=.*(?:ランダム|おまかせ))', '', part)
            if re.match('^'+SUBJECT, part) and re.search(r'\d+[〜~]\d+', part):
                part = re.sub(r'(?:ランダム(?:にして|で)?|適当に選んで)(?!な)', 'おまかせ', part)
            part = re.sub('^('+SUBJECT+r')(?:は|を|:)?\s*(\d+[〜~]\d+)(?:個|枚)?(?:の範囲)?(?:で)?おまかせ[。？！?!]*$', r'\1\2おまかせ', part)
            if re.match('^'+SUBJECT, part):
                part = re.sub(r'(?:にしてもらえますか|にしていただけますか|にしてくれますか|にしてほしいです|にして欲しいです|にしてほしい|にして欲しい|で頼みます|で頼む|でお願いできますか|に設定してほしい)[。？！?!]*$', '', part)
                part = re.sub(r'(?:は|を|:)\s*(?:いじらないで|変えずに|触らず)[。？！?!]*$', 'は現在値を維持', part)
                part = re.sub(r'(?<=\d)で[。？！?!]*$', '', part)
            parts.append(part.strip())
        changed = '\n'.join(parts)
        if changed != original.strip() and original.strip():
            changes.append({'input':original.strip(), 'suggested':changed,
                            'method':'項目名・語尾・漢数字・範囲の言い換え'})
        result.append(changed)
    return '\n'.join(result), changes


def suggestions(text):
    """Suggestions require a user click; no confidence percentage is fabricated."""
    s = unicodedata.normalize('NFKC', text)
    m = re.match(r'([^\d:：はを、,]+?)(?:は|を|:|：)?\s*(\d.*|MAX.*|max.*)$', s)
    if not m or re.search(r'ない|以外|除|だけ|のみ|以上|未満', s):
        return []
    scores = sorted(((SequenceMatcher(None, m[1].strip(), name).ratio(), name) for name in FIELDS), reverse=True)
    return [name+'：'+m[2] for score,name in scores[:3] if score >= .6]
