"""Explicit additional requests. Unknown names and unsupported fields stay blocked."""
import re
import unicodedata
from .parser import ParseError
from .named_characters import NameIndex, name_key

ALIASES = {
    '黒キャスリィ':'漆黒の魔女ダークキャスリィ',
    '黒フォノウ':'断命の王ダークフォノウ',
    '白フォノウ':'天命の王フォノウ',
    '黒ダルターニャ':'黒傑ダークダルターニャ',
    'イザナギ':'暁のイザナギ', 'チビガウ':'ちびガウ',
}


def expand_name(query, metadata):
    canonical=next((v for k,v in ALIASES.items() if name_key(k)==name_key(query)),query)
    if metadata:
        matches=NameIndex(metadata).exact(canonical)
        if len(matches)==1:return next(iter(matches.values()))
    return canonical


def is_extended(text):
    if '円' in text:return False
    if '【キャッツアイ】' in text and re.search(r'(?m)^(?:EX|レア|激レア|超激レア|伝説レア|伝説|闇)(?:用)?(?:は|:|：)\s*\d+個',text):return True
    pattern=(r'(?m)^(?!(?:基本|EX|レア|激レア|超激レア|伝説|キャラ|全キャラ)(?:は|:|：))[^\n]{1,80}(?:は|:|：)(?:レベル|Lv)?\s*\d+\s*[+＋]\d+'
             r'|(?m:^(?!(?:基本|EX|レア|激レア|超激レア|伝説|コラボ|全キャラ|キャラ|上記))[^\n]{1,80}(?:は|:|：)[^\n]*[+＋]値(?:だけ|は|を)?\s*\d+)'
             r'|プラチナのかけら|プラチナの欠片|トレジャーレーダー|ニャンピュータ'
             r'|キャッツアイ(?:【|\[)?(?:EX|レア|激レア|超激レア|伝説レア|伝説|闇)(?:】|\])?(?:は|を|:)?\s*\d'
             r'|ネコビタン[ABCＡＢＣ](?:は|を|:)?\s*\d'
             r'|プレイ時間(?:は|を|:)?\s*\d+(?:時間|:)\d+|プレイ日数|ログイン日数|通常ガチャ'
             r'|にゃんこ塔|地底迷宮.*(?:階|層)|隊員.*レジェンド\s*\d+(?:体|人)'
             r'|(?:ユーザーランク|所持キャラ数|超激レアの数|伝説レアの数)(?:は|を|:|：)?\s*\d')
    return bool(re.search(pattern,text))


def read(reader,s):
    from .conditional_language import expression, conditions
    add=reader.add
    if re.search(r'第[34]形態',s) and re.search(r'保持|変更しない|そのまま',s) and not re.search(r'解放して|進化させ',s):return True
    if s in ('以下の内容でお願いします','対象を特定できない項目や未対応の操作は、勝手に置き換えず確認待ちにしてください'):
        return True
    if re.search(r'プレイ日数|ログイン日数',s) and re.search(r'保持|変更しない|そのまま',s):return True
    if re.search(r'プレイ日数|ログイン日数',s):
        raise ParseError('プレイ日数の保存先・意味が未確認のため変更できません。プレイ時間では代用しません。')
    m=re.fullmatch(r'(?:プラチナのかけら|プラチナの欠片|プラチナかけら)(?:は|を|:)?\s*(\d+)(?:個|にして.*|に設定.*|に変更.*)?',s)
    if m:
        amount=int(m[1])
        if amount>999:raise ParseError('プラチナのかけらは0〜999の範囲で指定してください。')
        add('resource','platinum_shards',{'op':'set','value':amount},label=s);return True
    for label in ('トレジャーレーダー','ニャンピュータ'):
        if s.startswith(label) and re.match(r'(?:は|を|:)',s[len(label):]):
            add('items','amount',expression(s[len(label):]),group='battle_items',names=[label],priority=3,label=s);return True
    item=re.fullmatch(r'(?:キャッツアイ(?:【|\[)?(EX|レア|激レア|超激レア|伝説レア|伝説|闇)(?:】|\])?|ネコビタン([ABC]))(?:は|を|:)?\s*(\d+)(?:個|にして.*|に設定.*|に変更.*)?',s,re.I)
    if not item and 'キャッツアイ' in reader.section:
        item=re.fullmatch(r'(EX|レア|激レア|超激レア|伝説レア|伝説|闇)(?:用)?(?:は|を|:)?\s*(\d+)(?:個)?',s,re.I)
        if item:
            category=item[1].replace('伝説レア','伝説').upper();amount=int(item[2]);group='catseyes';label='キャッツアイ【'+category+'】'
    elif item:
        amount=int(item[3]);group='catseyes' if item[1] else 'catamins'
        label='キャッツアイ【'+item[1].replace('伝説レア','伝説').upper()+'】' if item[1] else 'ネコビタン'+item[2].upper()
    if item:
        if amount>999:raise ParseError('種類別アイテムは0〜999で指定してください。')
        add('items','amount',{'op':'set','value':amount},group=group,names=[label],priority=3,label=s);return True
    time=re.fullmatch(r'プレイ時間(?:は|を|:)?\s*(\d+)(?:時間|:)(\d+)(?:分|(?=:)|$)(?:(?:\s*|:)(\d+)秒?)?(?:にして.*|に変更.*|に設定.*)?',s)
    if time:
        h,m,sec=int(time[1]),int(time[2]),int(time[3] or 0)
        if h>9999 or m>59 or sec>59:raise ParseError('プレイ時間は9999時間59分59秒以内、分・秒は0〜59で指定してください。')
        add('profile','playtime',{'op':'set','value':h*3600+m*60+sec},label=s);return True
    helper=re.search(r'隊員.*レジェンド\s*(\d+)(?:体|人).*?(一人前|熟練|カリスマ|その他)\s*(\d+)(?:体|人)',s)
    if helper:
        a,b=int(helper[1]),int(helper[3]);other=helper[2]
        if a+b>10:raise ParseError('隊員は合計10体以内で指定してください。')
        if other=='その他':
            raise ParseError('「その他」の隊員クラスを指定してください。一人前・熟練・カリスマ、または既存の非レジェンドを保持と指定できます。')
        add('helper_mix','composition',{'op':'set','value':a+b},composition={'レジェンド':a,other:b},label=s);return True
    helper_keep=re.search(r'隊員.*レジェンド\s*(\d+)(?:体|人).*非レジェンド\s*(\d+)(?:体|人).*保持',s)
    if helper_keep:
        a,b=int(helper_keep[1]),int(helper_keep[2])
        if a+b>10:raise ParseError('隊員は合計10体以内で指定してください。')
        add('helper_mix','composition',{'op':'set','value':a+b},composition={'レジェンド':a},keep_nonlegend=b,label=s);return True
    tower=re.fullmatch(r'(風雲にゃんこ塔|にゃんこ塔|異界にゃんこ塔)(?:は|を|:)?\s*(\d+)階(?:まで)?(?:クリア|攻略)(?:済み|にして.*|してください)?',s)
    if tower:
        add('tower','clear',{'op':'atleast','value':1},map_name='風雲にゃんこ塔' if tower[1]=='にゃんこ塔' else tower[1],floor=int(tower[2]),label=s);return True
    labyrinth=re.fullmatch(r'地底迷宮(?:の)?(最高到達(?:地点|階)?|進行度)?(?:は|を|:)?(?:地下|地底)?\s*(\d+)(?:階|層)(?:まで)?(?:クリア|にして.*|に変更.*|に設定.*)?',s)
    if labyrinth:
        add('labyrinth','highest' if labyrinth[1] and '最高' in labyrinth[1] else 'clear',{'op':'set','value':int(labyrinth[2])},label=s);return True
    gamatoto=re.fullmatch(r'ガマトト(?:は|を|の)?(?:レベル|Lv)\s*(\d+)(?:にして.*|に変更.*|に設定.*)?',s,re.I)
    if gamatoto:
        add('profile','gamatoto_level',{'op':'set','value':int(gamatoto[1])},label=s);return True
    ordinary=re.match(r'通常ガチャの激レアキャラ(?:だけ|は)(.*)',s)
    if ordinary:
        expr=expression(ordinary[1]);add('cat','plus',expr,selector={'rarity':3,'regular_gacha':True},label=s);return True
    target=re.fullmatch(r'(ユーザーランク|所持キャラ数|超激レアの数|伝説レアの数)(?:は|を|:)?\s*(\d+)(?:体|ランク)?(?:にして.*|に調整.*)?(?:[、,]調整対象[=:：](.+))?',s)
    if target:
        field='rank' if target[1]=='ユーザーランク' else 'count'
        selector={}
        if target[1]=='超激レアの数':selector['rarity']=4
        if target[1]=='伝説レアの数':selector['rarity']=5
        if target[3]:selector['names']=[expand_name(n.strip(),reader.metadata) for n in re.split('[、,]',target[3]) if n.strip()]
        add('account',field,{'op':'set','value':int(target[2])},selector=selector,label=s);return True
    # Every name is resolved against metadata. A guessed name never becomes a target.
    pair=re.match(r'(.+?)(?:は|:|を)(.*)',s)
    if pair:
        queries=[expand_name(n.strip(),reader.metadata) for n in re.split('[、,]|と|および',pair[1]) if n.strip()]
        index=NameIndex(reader.metadata) if reader.metadata else None
        if index and queries and all(len(index.exact(n))==1 for n in queries):
            tail=pair[2]
            if re.search(r'(?:解放|開放)',tail) and not re.search(r'解放状態|解放しない|未解放のまま',tail):
                reader.named('、'.join(queries),unlocked={'op':'set','value':1},when=[{'field':'unlocked','op':'eq','value':0}] if re.search('未所持|未解放',tail) else [])
                tail=re.sub(r'未所持の場合だけ(?:解放|開放).*?(?:[、,]|$)','',tail)
                if not re.search(r'\d|レベル|Lv|\+',tail):return True
            if re.search(r'\+値|プラス値',tail) and not re.search(r'(?:レベル|Lv)\s*\d',tail,re.I):
                plus_text=re.split(r'\+値|プラス値',tail)[-1]
                plus=expression(plus_text)
                if plus is None:raise ParseError('＋値の数値・計算方法を指定してください。')
                reader.named('、'.join(queries),plus=plus,when=conditions(tail,'plus'));return True
            level=re.search(r'(?:レベル|Lv)?\s*(\d+)(?:\s*\+\s*(\d+|MAX))?',tail,re.I)
            if level:
                reader.named('、'.join(queries),base={'op':'set','value':int(level[1])},plus=expression(level[2]) if level[2] else None);return True
            if re.search(r'レベル|基本|\+値|本能|解放状態',tail) and re.search(r'保持|変更しない|そのまま|現状維持',tail):
                if re.search(r'基本|レベル',tail):reader.named('、'.join(queries),base={'op':'keep'})
                if re.search(r'\+値',tail):reader.named('、'.join(queries),plus={'op':'keep'})
                if '本能' in tail:reader.named('、'.join(queries),talents=True)
                if '解放状態' in tail:reader.named('、'.join(queries),unlocked={'op':'keep'})
                return True
    return False
