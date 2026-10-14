"""Validate every public rule before metadata acquisition and account transfer."""
import copy
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from .engine import compile_conditional, SCALARS
from .support import ParseError

DOMAINS = {
    'resource': set(SCALARS), 'cat': {'base', 'plus', 'unlocked', 'forms'},
    'talent': {'talent'}, 'items': {'amount'}, 'orbs': {'amount'},
    'story': {'clear', 'treasure'}, 'legend': {'clear'}, 'event': {'clear'},
    'helpers': {'fill', 'legend'},
    'action': {'facilities', 'ototo', 'shrine', 'gamatoto_max', 'remove_errors'},
}


def integer(value, low=0, high=2147483647):
    if type(value) is not int or not low <= value <= high:
        raise ParseError('条件付き指定の数値が範囲外、または整数ではありません。')
    return value


def strings(value, maximum=1000):
    if (not isinstance(value, list) or not 1 <= len(value) <= maximum
            or any(not isinstance(s, str) or not s.strip() or len(s) > 100 for s in value)):
        raise ParseError('対象名・番号の一覧が不正です。')


def validate(raw):
    if not isinstance(raw, dict) or set(raw) - {'rules', 'no_decrease', 'cat_no_decrease'}:
        raise ParseError('条件付き設定の形式が不正です。')
    for key in ('no_decrease', 'cat_no_decrease'):
        if type(raw.get(key, False)) is not bool:
            raise ParseError('値を減らさない指定が不正です。')
    rules = raw.get('rules')
    if not isinstance(rules, list) or not 1 <= len(rules) <= 200:
        raise ParseError('条件付き指定は1〜200件にしてください。')
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) - {
            'domain','field','expr','when','selector','label','no_decrease',
            'group','names','rank','chapters','series','stars','progressive',
            'until_penultimate','family','map_name','exclude_speed',
        }:
            raise ParseError('条件付き指定に未知の項目があります。')
        domain, field = rule.get('domain'), rule.get('field')
        if not isinstance(domain, str) or domain not in DOMAINS or not isinstance(field, str) or field not in DOMAINS[domain]:
            raise ParseError('変更対象が不正です。')
        if not isinstance(rule.get('label',''), str) or len(rule.get('label','')) > 300:
            raise ParseError('指定の表示名が長すぎます。')
        for key in ('no_decrease','progressive','until_penultimate','exclude_speed'):
            if type(rule.get(key,False)) is not bool:
                raise ParseError('条件のチェック項目が不正です。')
        expr = rule.get('expr')
        if not isinstance(expr, dict) or set(expr) - {'op','value','bounds'}:
            raise ParseError('計算方法が不正です。')
        op = expr.get('op')
        if op not in ('keep','max','set','add','mul','range','atleast'):
            raise ParseError('未対応の計算方法です。')
        if op == 'range':
            bounds = expr.get('bounds')
            if not isinstance(bounds,list) or len(bounds)!=2:
                raise ParseError('最小値・最大値を指定してください。')
            if integer(bounds[0]) > integer(bounds[1]):
                raise ParseError('最小値は最大値以下にしてください。')
        elif op == 'mul':
            v = expr.get('value')
            if isinstance(v,bool) or not isinstance(v,(str,int,float)) or len(str(v))>16:
                raise ParseError('倍率が不正です。')
            try: number = Decimal(str(v))
            except InvalidOperation: raise ParseError('倍率が不正です。') from None
            if not number.is_finite() or not 0 <= number <= 1000:
                raise ParseError('倍率は0〜1000にしてください。')
        elif op in ('set','add','atleast'):
            integer(expr.get('value'), -2147483647 if op=='add' else 0)
        if field in ('unlocked','forms') and op not in ('keep','set'):
            raise ParseError('解放状態・形態は固定値か保持で指定してください。')
        if field == 'unlocked' and op == 'set':
            integer(expr['value'],1,1)  # relocking is deliberately not an unlock action
        if field == 'forms' and op == 'set':
            integer(expr['value'],1,3)
        if domain in ('helpers','action') and (op!='max' or rule.get('when')):
            raise ParseError('隊員・補助操作はMAXを選択してください。')
        if rule.get('exclude_speed') and domain!='talent':
            raise ParseError('移動速度本能の保持は本能だけに指定できます。')
        when = rule.get('when',[])
        allowed = {'base','plus','unlocked'} if domain=='cat' else {'talent','unlocked'} if domain=='talent' else {'clear','treasure'} if domain=='story' else {field}
        if not isinstance(when,list) or len(when)>8:
            raise ParseError('条件は1指定あたり8件までです。')
        for condition in when:
            if not isinstance(condition,dict) or set(condition)-{'field','op','value'}:
                raise ParseError('条件の形式が不正です。')
            if condition.get('field') not in allowed or condition.get('op') not in ('lt','le','eq','ne','ge','gt','odd','even'):
                raise ParseError('判定する現在値・条件が不正です。')
            if condition['op'] not in ('odd','even'):integer(condition.get('value'))
        selector = rule.get('selector',{})
        if not isinstance(selector,dict) or set(selector)-{'rarity','collab','names','group','series','forms_min'}:
            raise ParseError('対象キャラの指定が不正です。')
        if selector and domain not in ('cat','talent'):
            raise ParseError('この項目には対象キャラを指定できません。')
        if 'rarity' in selector:integer(selector['rarity'],0,5)
        if 'forms_min' in selector:integer(selector['forms_min'],1,3)
        if 'collab' in selector and type(selector['collab']) is not bool:
            raise ParseError('コラボ指定が不正です。')
        if 'names' in selector:strings(selector['names'])
        if 'group' in selector:strings([selector['group']],1)
        if 'series' in selector and selector['series'] not in ('ちび','大狂乱','特急'):
            raise ParseError('キャラ系列の指定が不正です。')
        if domain=='items':
            if rule.get('group') not in ('battle_items','catseyes','catamins','base_materials','catfruit'):
                raise ParseError('アイテム種類が不正です。')
            if 'names' in rule:strings(rule['names'])
        if domain=='orbs' and rule.get('rank') not in (None,'S'):
            raise ParseError('本能玉は全ランクかSランクで指定してください。')
        if domain=='story':
            chapters=rule.get('chapters')
            if not isinstance(chapters,list) or not 1<=len(chapters)<=9:
                raise ParseError('お宝・クリアを変更する章を選択してください。')
            for position in chapters:integer(position,0,8)
        if domain=='legend':
            if rule.get('series') not in ('legend','true_legend','zero_legend'):
                raise ParseError('レジェンド系列が不正です。')
            integer(rule.get('stars',4),1,4)
            if op not in ('keep','atleast'):
                raise ParseError('レジェンドは保持かクリア回数を増やす指定にしてください。')
        if domain=='event':
            if rule.get('family') not in ('event','aku','colosseum','collab','dojo_ranking'):
                raise ParseError('イベント系列が不正です。')
            if 'map_name' in rule:strings([rule['map_name']],1)
            if op not in ('keep','atleast'):
                raise ParseError('イベントは保持かクリア回数を増やす指定にしてください。')
        # Do not silently ignore cross-domain settings.
        ownership={'group':{'items'},'names':{'items'},'rank':{'orbs'},'chapters':{'story'},
                   'series':{'legend'},'stars':{'legend'},'progressive':{'legend'},
                   'until_penultimate':{'legend'},'family':{'event'},'map_name':{'event'}}
        if any(key in rule and domain not in domains for key,domains in ownership.items()):
            raise ParseError('変更対象と詳細条件が一致しません。')
    return copy.deepcopy(raw)


def compile_public(namespace, raw):
    raw = validate(raw)
    module = SimpleNamespace(**namespace)
    for rule in raw['rules']:
        if rule['domain']=='event' and rule['family']!='aku' and rule['family'] not in namespace['EVENT_STAGE_FAMILIES']:
            raise ParseError('このイベント系列の保存先がありません。')
    return compile_conditional(module, {'conditional_rules':raw['rules'],
                                      'conditional_no_decrease':raw.get('no_decrease',False),
                                      'conditional_cat_no_decrease':raw.get('cat_no_decrease',False)})
