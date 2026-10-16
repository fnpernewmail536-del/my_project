"""Explicit bounded quantities; never infer bounds from unrelated numbers."""
import copy
import re
import secrets
import unicodedata

ALIASES = {'猫缶':4,'XP':5,'NP':6,'リーダーシップ':7,'バトルアイテム':8,'キャッツアイ':9,'ネコビタン':10,'城素材':11,'マタタビ':12,'またたび':12,'本能玉':13,'獣石':14,'通常チケット':15,'銀チケット':15,'レアチケット':16,'プラチナチケット':17,'レジェンドチケット':18,'未来編採点スコア':46,'道場スコア':47,'ガマトトレベル':66,'地底迷宮勲章':82}
GROUPS={8,9,10,11,12,13,14,46,82}
RANGES={4:58999,5:99999999,6:9999,7:999,8:999,9:999,10:999,11:999,12:998,13:998,14:999,15:999,16:999,17:99,18:99,46:10000,47:2147483647,66:32767,82:32767}
RARITIES={'基本':0,'EX':1,'レア':2,'激レア':3,'超激レア':4,'伝説レア':5,'伝説':5}
NOTES={
'それぞれの種類で個別にランダムな数量を選んでください','全種類を同じ数量にそろえないでください',
'各チケットの数量は別々に選んでください','基本Lvと+値はキャラ1体ごとに個別に選んでください',
'範囲指定は全員共通の1つの数値にしないでください','ステージごとに個別に数値を選んでください',
'種類ごとに個別に数量を選んでください'}

def roll(bounds):
    lo,hi=bounds
    return lo+secrets.randbelow(hi-lo+1)

def parse_range_line(text,previous,fixed):
    from .parser import ParseError
    line=unicodedata.normalize('NFKC',text).strip().rstrip('。')
    compact=re.sub(r'\s+','',line)
    if '\n' in line:return None
    if compact in NOTES:return fixed('お願いします',previous)
    if compact=='+値を付けられないキャラには付けないでください':
        p=fixed('お願いします',previous);p['require_plus_support']=True
        for rule in p['level_rules'].values():rule['plus_only_supported']=True
        return p
    if re.fullmatch(r'コラボ(?:を)?含め(?:て)?全キャラ(?:を)?(?:解放|開放)(?:してください)?',compact):
        return fixed('キャラ全解放',previous)
    if re.fullmatch(r'上記\d+体は基本Lv・\+値ともに現在のまま',compact):
        return fixed(compact.replace('基本Lv','基本レベル').replace('+値','＋値'),previous)
    m=re.fullmatch(r'(基本|EX|レア|激レア|超激レア|伝説レア|伝説)(?:キャラ)?[:：]基本Lv(\d+)(?:[〜~～-](\d+))?[,、]\+値(\d+)[〜~～-](\d+)',compact)
    if m:
        rarity=RARITIES[m[1]];lo=int(m[2]);hi=int(m[3] or m[2]);pl,ph=int(m[4]),int(m[5])
        if not 1<=lo<=hi<=60 or not 0<=pl<=ph<=90:raise ParseError('キャラの範囲指定を確認してください。')
        p=fixed(f'{m[1]}全てレベル{lo}+{pl}〜{ph}',previous)
        p['level_rules'][str(rarity)]['base']=[lo,hi] if hi!=lo else lo
        if p.get('require_plus_support'):p['level_rules'][str(rarity)]['plus_only_supported']=True
        for label in p['labels']:
            if label['key']==f'level_{rarity}':label['label']=f'{m[1]}：基本Lv{lo}〜{hi}・＋{pl}〜{ph}（1体ごとに抽選）'
        return p
    m=re.fullmatch(r'(?:メニュー(\d+)=|('+ '|'.join(sorted(ALIASES,key=len,reverse=True))+r')(?:は|を|:|：)?(?:全章の各ステージで|4種類それぞれ|種類・ランクごとに|種類ごとに)?)(\d+)[〜~～-](\d+)(?:からおまかせ|からランダム|でおまかせ|おまかせ)?',compact,re.I)
    if not m:return None
    n=int(m[1]) if m[1] else ALIASES.get(m[2].upper() if m[2].upper() in ('XP','NP') else m[2]);lo,hi=int(m[3]),int(m[4])
    if n not in RANGES:raise ParseError('この項目の数量範囲は未対応です。')
    if not (1 if n==66 else 0)<=lo<=hi<=RANGES[n]:raise ParseError('数量範囲を確認してください。')
    chosen=lo if n in GROUPS else roll([lo,hi])
    p=fixed(f'メニュー{n}={chosen}',previous)
    p.setdefault('quantity_ranges',{})[str(n)]=[lo,hi]
    from .menus import MENU_BY_NUMBER
    entry=MENU_BY_NUMBER[n]
    keys={f'menu_{n}',entry.get('resource_key',''), 'custom_'+entry.get('resource_key','')}
    keys.update({12:'fruit_matatabi',14:'fruit_stones',46:'future_scores',47:'dojo_score',66:'gamatoto_level_detail',82:'labyrinth_medals_detail'}.get(n,'') for _ in [0])
    p['labels'][:]=[x for x in p['labels'] if x['key'] not in keys and x['key']!=f'range_{n}']
    scope='種類ごとに抽選' if n in GROUPS else f'選択値{chosen}'
    p['labels'].append({'key':f'range_{n}','label':f'{entry["name"]}：{lo}〜{hi}（{scope}）'})
    return p
