"""Bounded Japanese wording suggestions. Unknown limits and values are never discarded."""
from decimal import Decimal, InvalidOperation
import re
from .parser import normalize, RESOURCES, ITEM_KEYS, TICKET_KEYS, COLLAB_RARITY_SCOPE

NUMBER = r'(?:\d+億(?:\d{1,4}万)?\d{1,4}|\d+万\d{1,4}|\d+(?:\.\d+)?(?:億|万|千|k|m)?)'
SCOPE = r'(?:全キャラ|全てのキャラ|すべてのキャラ|キャラ全員|キャラ全部|キャラ全て|超激レア(?:キャラ)?|激レア(?:キャラ)?|伝説(?:レア)?(?:キャラ)?|レア(?:キャラ)?|基本(?:キャラ)?|ex(?:キャラ)?)'
PLUS_SCOPE = r'(?:'+COLLAB_RARITY_SCOPE+r'|コラボ(?:系|キャラ)?|' + SCOPE + r')'
FIELD = '|'.join('(?:' + aliases + ')' for aliases, *_ in RESOURCES)
FIELD += r'|普通のチケット|プラチナ券|レジェンド券'
JOIN = r'\s*(?:と|や|、|,|・|/|&)\s*'
FIELD_LIST = r'(?:' + FIELD + r')(?:' + JOIN + r'(?:' + FIELD + r'))*'
UNCHANGED = r'(?:そのまま(?:で)?|変更しない(?:で(?:ください)?)?|変えない(?:で(?:ください)?)?|触らない(?:で(?:ください)?)?|維持(?:してください)?)'


def integer(text):
    if text == 'max': return 'max'
    if len(text) > 40: return None
    compound = re.fullmatch(r'(\d+)億(?:(\d{1,4})万)?(\d{1,4})|(\d+)万(\d{1,4})', text)
    if compound:
        if compound[1]: return str(int(compound[1])*100000000 + int(compound[2] or 0)*10000 + int(compound[3]))
        return str(int(compound[4])*10000 + int(compound[5]))
    match = re.fullmatch(r'(\d+(?:\.\d+)?)(億|万|千|k|m)?', text)
    if not match: return None
    try:
        value = Decimal(match[1]) * {'億':100000000,'万':10000,'千':1000,'k':1000,'m':1000000,None:1}[match[2]]
        if value != value.to_integral_value(): return None
        return str(int(value))
    except (InvalidOperation, ValueError): return None


def quantity(text):
    match = re.fullmatch(r'(' + NUMBER + r')(?:[〜~]|から)(' + NUMBER + r')(?:の間)?', text)
    if match:
        lo, hi = integer(match[1]), integer(match[2])
        return lo + '〜' + hi if lo is not None and hi is not None else None
    return integer(text)


def field(text):
    special={'普通のチケット':'銀チケ','プラチナ券':'プラチケ','レジェンド券':'レジェチケ'}
    if text in special: return special[text]
    for aliases, key, label, *_ in RESOURCES:
        if re.fullmatch(aliases,text):
            return {'normal_tickets':'銀チケ','rare_tickets':'レアチケ',
                    'platinum_tickets':'プラチケ','legend_tickets':'レジェチケ',
                    'battle_items':'バトルアイテム'}.get(key,label.replace('各種',''))
    return None


def fields(text):
    return list(dict.fromkeys(field(m[0]) for m in re.finditer(FIELD, text)))


def resource_key(name):
    canonical=field(normalize(name))
    for _, key, label, _ in RESOURCES:
        if canonical == field(normalize(label.replace('各種',''))):return key
    return None


def domain_commands(kind, keep=()):
    """Cancel earlier selections explicitly; a broad 'other' clause preserves them."""
    keys=TICKET_KEYS if kind=='tickets' else ITEM_KEYS
    names={key:field(label.replace('各種','').lower()) for _,key,label,_ in RESOURCES}
    commands=[names[key]+'不要' for key in keys if names[key] not in keep]
    if kind=='tickets': commands.append('メニュー19不要')
    else: commands.append('その他素材不要')
    return commands


def polite(text):
    for _ in range(3):
        updated=re.sub(r'(?:に設定(?:してください)?|に変更(?:してください)?|にして(?:ください|ほしい|ね)?|にしたい|でお願い(?:します)?|お願い(?:します)?|して(?:ください|ほしい|ね)?|してください)$','',text).strip()
        if updated==text: break
        text=updated
    return text


def level_scope(text):
    if text.startswith(('全','すべて')) or text in {'キャラ全員','キャラ全部','キャラ全て'}: return '全キャラ'
    return text.replace('キャラ','').replace('伝説レア','伝説')


def suggest_phrase(text):
    s=normalize(text).rstrip('。!！.').strip()
    if re.search(r'円|¥|￥|引き継ぎ|引継ぎ|認証|transfer|auth',s): return None
    s=re.sub(r'(?<!\d)\d{1,3}(?:,\d{3})+(?!\d)',lambda m:m[0].replace(',',''),s)
    s=re.sub(r'(?:最大|上限|カンスト|マックス)(?:まで)?','max',s)
    s=s.replace('開放','解放')
    if re.fullmatch(r'(?:基本|本体)(?:の)?レベル(?:は|を)?'+UNCHANGED,s):
        return '基本レベルは変更せず'
    prefix=re.fullmatch(r'(?:やっぱり|やっぱ|訂正|修正)(?:[:：]\s*|\s+)?(.+)',s)
    if prefix and not re.match(r'やっぱ|訂正|修正',prefix[1]):
        return suggest_phrase(prefix[1])
    if re.fullmatch(r'(?:プラス(?:値)?|\+(?:値)?)(?:だけ|のみ)(?:変えて|変更したい|変更して|変更してください)',s):
        return '＋値だけ変更してください'
    exclude=re.fullmatch(r'((?:'+FIELD+r')(?:と(?:'+FIELD+r'))*)(?:は|を)?(?:いらない|要らない|不要|変更しない(?:で(?:ください)?)?|変えない(?:で(?:ください)?)?|触らない(?:で(?:ください)?)?|そのまま(?:で)?)',s)
    if exclude:
        names=[field(name) for name in exclude[1].split('と')]
        if all(names): return '\n'.join(name+'不要' for name in names)
    t=polite(s)
    replacement=re.fullmatch(r'('+FIELD+r')(?:は|を)?('+NUMBER+r'|max)(?:個|枚)?(?:じゃなくて|ではなくて|ではなく|じゃなく)('+NUMBER+r'|max)(?:個|枚)?(?:に)?',t)
    if replacement:
        amount=integer(replacement[3])
        if amount is not None:return field(replacement[1])+amount
    grouped=re.fullmatch(r'(アイテム(?:類)?|チケット(?:類)?)(?:は|を)?(?:全部|全て|すべて)?'+UNCHANGED,t)
    if grouped:return '\n'.join(domain_commands('tickets' if grouped[1].startswith('チケット') else 'items'))
    no_levels=re.fullmatch(r'(?:全キャラ|キャラ)(?:の|は|を)?(?:レベル|レベル変更)(?:は|を)?'+UNCHANGED,t)
    if no_levels:return 'メニュー29不要'
    base_keep=re.fullmatch(r'('+SCOPE+r')(?:の|は|を)?(?:レベル|lv\.?)?(?:は|を)?('+NUMBER+r'|max)(?:[、,]|\()\s*(?:プラス(?:値)?|\+(?:値)?)(?:は|を)?'+UNCHANGED+r'\)?',t)
    if base_keep:
        base=integer(base_keep[2]);scope=level_scope(base_keep[1])
        if base is not None:return ('メニュー29='+base if scope=='全キャラ' else scope+'レベル'+base)+'\n'+scope+'＋維持'
    keep_plus=re.fullmatch(r'(?:('+PLUS_SCOPE+r')(?:の|は|も)?)?(?:プラス(?:値)?|\+(?:値)?)(?:は|を)?'+UNCHANGED,t)
    if keep_plus:
        scope=keep_plus[1] or '全キャラ'
        scope='コラボキャラ' if scope.startswith('コラボ') else level_scope(scope)
        return scope+'＋維持'
    only=re.fullmatch(r'(?:(アイテム(?:類)?|チケット(?:類)?)(?:は|を)?)?('+FIELD_LIST+r')(?:だけ|のみ)(?:は|を)?(?:各(?:種)?)?('+NUMBER+r'|max)?(?:個|枚)?(?:に)?(?:[、,](?:その他|それ以外|他)(?:の(?:アイテム|チケット))?(?:は)?(?:不要|いらない|そのまま|変更しない))?',t)
    if only:
        names=fields(only[2]);keys=[resource_key(n) for n in names]
        kind='tickets' if all(k in TICKET_KEYS for k in keys) else 'items' if all(k in ITEM_KEYS for k in keys) else None
        if kind and (not only[1] or ('tickets' if only[1].startswith('チケット') else 'items')==kind):
            amount=integer(only[3] or 'max')
            if amount is not None:return '\n'.join([name+amount for name in names]+domain_commands(kind,names))
    exceptions=[
        r'('+FIELD_LIST+r')以外(?:の)?(アイテム(?:類)?|チケット(?:類)?)(?:は|を)?(?:全部|全て|すべて)?max',
        r'(アイテム(?:類)?|チケット(?:類)?)(?:は|を)?('+FIELD_LIST+r')以外(?:は|を)?(?:全部|全て|すべて)?max',
        r'(アイテム(?:類)?|チケット(?:類)?)(?:は|を)?(?:全部|全て|すべて)?max\(('+FIELD_LIST+r')以外\)',
    ]
    for i,pattern in enumerate(exceptions):
        match=re.fullmatch(pattern,t)
        if not match:continue
        names=fields(match[1] if i==0 else match[2]);group=match[2] if i==0 else match[1]
        keys=TICKET_KEYS if group.startswith('チケット') else ITEM_KEYS
        if all(resource_key(n) in keys for n in names):
            return ('チケットMAX' if keys==TICKET_KEYS else 'アイテムMAX')+'\n'+'\n'.join(n+'不要' for n in names)
    plus=re.fullmatch(r'(?:ただし[、,]\s*)?('+PLUS_SCOPE+r')(?:の|は|も)?\s*[:：]?\s*(?:だけ|のみ)?(?:プラス(?:値)?|\+(?:値)?)(?:だけ|のみ)?(?:を|は)?\s*('+NUMBER+r'(?:(?:[〜~]|から)'+NUMBER+r')?|max|なし)(?:程度|くらい)?(?:の範囲で(?:ランダム|おまかせ)|の範囲|の間|ランダム|\(おまかせ\))?',t)
    if plus:
        amount='0' if plus[2]=='なし' else quantity(plus[2])
        if amount is not None:
            if re.fullmatch(COLLAB_RARITY_SCOPE,plus[1]):scope='コラボ'+re.sub(r'コラボ|キャラ|系|の','',plus[1])
            else:scope='コラボキャラ' if plus[1].startswith('コラボ') else level_scope(plus[1])
            return scope+'＋'+amount
    rare_only=re.fullmatch(r'(?:チケット(?:類)?(?:は|を)?)?(?:レアチケ(?:ット)?|レア)(?:だけ|のみ)(max|'+NUMBER+r')?(?:枚)?(?:[、,](?:その他|それ以外|他)(?:は)?(?:不要|いらない|そのまま|変更しない))?',t)
    if rare_only and ('チケ' in t):
        amount=quantity(rare_only[1] or 'max')
        if amount is not None:
            return 'レアチケ'+amount+'\nメニュー15不要\nメニュー17不要\nメニュー18不要\nメニュー19不要'
    orb=re.fullmatch(r'本能(?:玉)?s(?:ランク)?(?:だけ|のみ)?(?:は|を)?(?:各(?:種)?)?('+NUMBER+r'|max)(?:個)?(?:[、,](?:その他|他)(?:は)?(?:不要|そのまま|変更しない))?',t)
    if orb:
        amount=integer(orb[1])
        if amount is not None:return '本能玉Sのみ'+amount
    # Unknown negations and limits cannot become a positive operation.
    if re.search(r'以外|除く|だけ|のみ|しない|変えない|触らない|いらない|要らない|不要',s):return None
    shared=re.fullmatch(r'('+FIELD_LIST+r')(?:の)?(?:数|数量|個数|枚数)?(?:は|を|が|:|=)?\s*(?:各(?:種)?|全種(?:各)?|全部|全て|すべて)?\s*('+NUMBER+r'|max)(?:個|枚)?(?:に)?',t)
    if shared:
        amount=integer(shared[2])
        if amount is not None:return '\n'.join(n+amount for n in fields(shared[1]))
    maximize=re.fullmatch(r'('+FIELD+r')(?:は|を)?(?:増や(?:して)?|増やしたい|いっぱい(?:に)?|全部max|すべてmax|フルmax)',t)
    if maximize:return field(maximize[1])+'MAX'
    levels=re.fullmatch(r'('+SCOPE+r')(?:の|は|を)?(?:全員|全部|全て|すべて)?(?:の)?(?:レベル|lv\.?)(?:を|は)?\s*('+NUMBER+r'|max)(?:[、,]|で)?\s*(?:(?:プラス(?:値)?|\+(?:値)?)(?:は|を)?('+NUMBER+r'(?:(?:[〜~]|から)'+NUMBER+r')?|max|なし))?',t)
    if levels:
        base=integer(levels[2]);extra=quantity(levels[3]) if levels[3] not in (None,'なし') else levels[3]
        if base is not None and (levels[3] is None or extra is not None):
            value=base+('+'+extra if extra is not None else '')
            scope=level_scope(levels[1])
            return 'メニュー29='+value if scope=='全キャラ' else scope+'全てレベル'+value
    unlock=re.fullmatch(r'(?:全キャラ|キャラ(?:を)?(?:全部|全て|全員)|すべてのキャラ|全てのキャラ)(?:を)?解放',t)
    if unlock:return 'キャラ全解放'
    rewards=re.fullmatch(r'(ユーザーランク|ur|猫図鑑|にゃんこ図鑑)(?:の)?報酬(?:を|は)?(?:全部|全て|すべて|全)?(未受け取り|未受取|受け取り済み|受取済み|受け取り|受取)(?:状態)?(?:に)?',t)
    if rewards:
        unclaimed=rewards[2].startswith('未')
        number=(78 if unclaimed else 77) if rewards[1] in {'ユーザーランク','ur'} else (80 if unclaimed else 79)
        return 'メニュー'+str(number)
    play=re.fullmatch(r'プレイ時間(?:は|を|:)?('+NUMBER+r'(?:(?:[〜~]|から)'+NUMBER+r')?)(?:時間|h)?(?:の間)?(?:で(?:ランダム|適当))?',t)
    if play:
        amount=quantity(play[1])
        if amount is not None:return 'プレイ時間'+amount
    score=re.fullmatch(r'道場(?:の)?スコア(?:は|を|:)?('+NUMBER+r'|max)(?:点)?(?:に)?',t)
    if score:
        amount=integer(score[1])
        if amount is not None:return 'メニュー47='+amount
    future=re.fullmatch(r'未来編(?:の)?採点(?:スコア)?(?:を|は)?(?:全部|全て|全ステージ)('+NUMBER+r'|max)(?:点)?(?:に)?',t)
    if future:
        amount=integer(future[1])
        if amount is not None:return 'メニュー46='+amount
    gamatoto=re.fullmatch(r'ガマトト(?:の)?(?:レベル|lv\.?)?(?:は|を|:)?('+NUMBER+r'|max)(?:レベル)?(?:に)?',t)
    if gamatoto:
        amount=integer(gamatoto[1])
        if amount is not None:return 'メニュー66='+amount
    presets=re.fullmatch(r'(初心者|中級者|上級者)(?:の)?(?:垢|アカウント)(?:を)?(?:作って|作りたい|作成|欲しい|ほしい)',t)
    if presets:return 'メニュー'+str({'初心者':1,'中級者':2,'上級者':3}[presets[1]])
    chapter=re.fullmatch(r'(日本編|未来編|宇宙編)(?:を)?(?:全部|全て|全章)(?:クリア|全クリ)',t)
    if chapter:return 'メニュー'+str({'日本編':39,'未来編':40,'宇宙編':41}[chapter[1]])+'=全章'
    numbered=re.fullmatch(r'(?:メニュー|番号|#)?(\d+)(?:番)?(?:を|は|:|=)?('+NUMBER+r'|max)',t)
    if numbered:
        amount=integer(numbered[2])
        if amount is not None:return 'メニュー'+numbered[1]+'='+amount
    action_patterns=[
        (r'全(?:て|部)?(?:の)?ステージ(?:を)?(?:全部|全て)?(?:クリア|制覇)(?:して)?(?:[、,]?(?:お宝|宝)(?:も|は|を)?(?:全部|全て)?max)?','全ステージMAX'),
        (r'ステージ(?:を)?(?:全部|全て)(?:クリア|制覇)(?:して)?(?:[、,]?(?:お宝|宝)(?:も|は|を)?(?:全部|全て)?max)?','全ステージMAX'),
        (r'真レジェ(?:ンド)?(?:を)?(?:全部|全て|全ステージ)?(?:クリア|全クリ)','メニュー56'),
        (r'(?:零|ゼロ)レジェ(?:ンド)?(?:を)?(?:全部|全て|全ステージ)?(?:クリア|全クリ)','メニュー57'),
        (r'(?:旧)?レジェ(?:ンド)?(?:を)?(?:全部|全て|全ステージ)?(?:クリア|全クリ)','メニュー55'),
        (r'ゾンビ(?:ステージ)?(?:を)?(?:全部|全て|全ステージ)?(?:クリア|全クリ)','メニュー48'),
        (r'魔界編(?:を)?(?:全部|全て)?(?:クリア|全クリ)','メニュー53'),
        (r'ゴールド会員(?:に|化)?','メニュー72'),
        (r'編成スロット(?:を|は)?(?:全部|全て)?max(?:に)?','メニュー73'),
        (r'施設(?:を|は)?(?:全部|全て)?max(?:に)?','施設MAX'),
        (r'オトート(?:の)?(?:城|全城)(?:を|は)?(?:全部|全て)?max(?:に)?','オトートMAX'),
        (r'(?:にゃんこ)?神社(?:を|は)?max(?:に)?','神社MAX'),
        (r'エラー(?:キャラ)?(?:を)?(?:消(?:して)?|消したい|削除)','エラーキャラ削除'),
        (r'スクラッチ(?:を|は)?(?:未実施|未実施状態|未実施の状態)(?:に)?','メニュー81'),
        (r'敵(?:キャラ)?図鑑(?:を|は)?(?:全部|全て)?解放','メニュー83'),
    ]
    for pattern,command in action_patterns:
        if re.fullmatch(pattern,t):return command
    return None
