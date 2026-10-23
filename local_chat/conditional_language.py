"""Explicit grammar for complex requests. Unknown restrictions remain blockers."""
import copy
import re
import unicodedata
from .parser import parse_request,ParseError
from .conditional_engine import select_ids
from .named_characters import NameIndex
from .characters import collaboration_ids

RARITIES={'基本':0,'EX':1,'レア':2,'激レア':3,'超激レア':4,'伝説レア':5,'伝説':5}
RES={'ネコカン':'catfood','猫缶':'catfood','缶':'catfood','XP':'xp','経験値':'xp','NP':'np','np':'np','リーダーシップ':'leadership','リダシ':'leadership','レアチケット':'rare_tickets','レアチケ':'rare_tickets','プラチナチケット':'platinum_tickets','プラチナ':'platinum_tickets','プラチケ':'platinum_tickets','レジェンドチケット':'legend_tickets','レジェチケ':'legend_tickets','にゃんこチケット':'normal_tickets','にゃんチケ':'normal_tickets','にゃんこチケ':'normal_tickets','にゃんチケット':'normal_tickets','にゃんチケ':'normal_tickets'}
ITEMS={'ネコボン':'ネコボン','スニャイパー':'スニャイパー','おかめはちもく':'おかめはちもく','スピードアップ':'スピードアップ'}
AMBIGUOUS={'現在開催中':'開催中のコラボ名・対象キャラを指定してください。','黒い敵に強い':'対黒のどの特性を対象にするかとキャラ一覧が必要です。','浮いている敵に超ダメージ':'属性・特性の照合定義が未接続です。対象キャラを指定してください。','第4形態':'第4形態の解放条件の照合は未対応です。','多め':'具体的な数量または範囲が必要です。','そこそこ':'具体的な数量または範囲が必要です。','ほぼカンスト':'具体的な数量またはMAXを指定してください。','ほぼ全解放':'解放しないキャラを指定してください。','いらないキャラ':'解放しないキャラを指定してください。','限定キャラ':'どの限定キャラを保持するか指定してください。','強すぎる':'対象キャラと変更値を指定してください。','半分くらい':'終了する章・ステージを指定してください。','だいたい':'終了する章・ステージを指定してください。','少し進め':'終了する章・ステージを指定してください。','使えるやつ':'対象キャラ・本能名を指定してください。','使わない本能':'除外する本能名を指定してください。','必要なもの':'対象施設を指定してください。','強い隊員':'隊員のクラス・人数を指定してください。','5万くらい':'許容する範囲を指定してください。','300くらい':'許容する範囲または固定値を指定してください。','500くらい':'許容する範囲または固定値を指定してください。','現在使用中':'編成は自動で保持します。','ネコパーフェクト:本能玉':'キャラに装着している本能玉の個別編集は未対応です。'}

def wants_conditional(text,previous=None):
    return bool((previous or {}).get('conditional_mode') or re.search(r'(?:レベル|[+＋]値)が\d+(?:未満|以上|以下)',text) or re.search(r'現在値|現在レベル|現在[+＋]値|現在の所持数|現在の2倍|現在のデータ|移動速度|未解放の伝説|既に持って|すでに持って|今持って|未所持|値を減らす|今より|下げない|奇数|偶数|1\.5倍|星[1234]|星[１-４]|第[34３４]形態|強いデータ|使わない本能|るろ(?:うに剣心|剣)(?=[^\n]*(?:キャラ|全解放))|隊員.*残|クリア済み.*お宝|普通のお宝|解放状態.*維持|種類について|本能玉.*減らさない|追加[234567]|【追加】',text))

def clean(text):
    text=unicodedata.normalize('NFKC',text).replace('＋','+')
    text=re.sub(r'(?<=\d),(?=\d{3}(?:\D|$))','',text)
    # Expand decimal Japanese ten-thousands without changing arithmetic dots.
    text=re.sub(r'(\d+(?:\.\d+)?)[〜~](\d+(?:\.\d+)?)万',lambda m:str(int(float(m[1])*10000))+'〜'+str(int(float(m[2])*10000)),text)
    text=re.sub(r'(\d+(?:\.\d+)?)万',lambda m:str(int(float(m[1])*10000)),text)
    text=text.replace('開放','解放').replace('超激は','超激レアは').replace('レジェンド0','零レジェンド').replace('レジェンドストーリー0','零レジェンド')
    return text

def expression(s):
    s=s.strip()
    s=re.sub(r'(?:MAX|最大|カンスト)じゃなくて', '', s, flags=re.I)
    if re.search(r'増やさなくていい|増やさない',s):return {'op':'keep'}
    tolerance=re.search(r'(\d+).*?±(\d+)',s)
    if tolerance:return {'op':'range','bounds':[max(0,int(tolerance[1])-int(tolerance[2])),int(tolerance[1])+int(tolerance[2])]}
    if re.search(r'くらい|程度|前後|ちょい',s) and not re.search(r'\d+[〜~]\d+',s):raise ParseError('おおよその数値の許容範囲を指定してください。')
    if re.search(r'現状維持|現在(?:値を維持|値のまま|のまま|の.*維持)|変更(?:なし|不要|禁止|しない|せず)|触らない|触らん|いじら|そのまま|キープ',s):return {'op':'keep'}
    if re.search(r'現在(?:値|の所持数|の枚数)?(?:の)?(\d+(?:\.\d+)?)倍',s):return {'op':'mul','value':re.search(r'(\d+(?:\.\d+)?)倍',s)[1]}
    if re.search(r'現在(?:値|の所持数|の枚数)?(?:に|\+)|今(?:の枚数)?\+|今より|追加|増や',s):
        m=re.search(r'(\d+)(?:個|枚)?(?:追加|増や)|\+(\d+)|今より(\d+)',s)
        if m:return {'op':'add','value':int(next(x for x in m.groups() if x))}
    if re.search(r'MAX|最大|上限|カンスト|カンスト',s,re.I):return {'op':'max'}
    m=re.search(r'(\d+)\s*(?:〜|~|～|-|から)\s*(\d+)',s)
    if m:return {'op':'range','bounds':[int(m[1]),int(m[2])]}
    m=re.search(r'(\d+)以上',s)
    if m:return {'op':'atleast','value':int(m[1])}
    nums=re.findall(r'\d+',s)
    if len(nums)==1:return {'op':'set','value':int(nums[0])}
    return None

def conditions(s,field):
    result=[]
    m=re.search(r'(\d+)以上(\d+)未満',s)
    if m:result += [{'field':field,'op':'ge','value':int(m[1])},{'field':field,'op':'lt','value':int(m[2])}];return result
    m=re.search(r'(\d+)(?:〜|~)(\d+)(?:なら|の場合|:|:)',s)
    if m:return [{'field':field,'op':'ge','value':int(m[1])},{'field':field,'op':'le','value':int(m[2])}]
    m=re.search(r'(\d+)(未満|以上|以下|を超えて|より高)',s)
    if m:result.append({'field':field,'op':{'未満':'lt','以上':'ge','以下':'le','を超えて':'gt','より高':'gt'}[m[2]],'value':int(m[1])})
    if '奇数' in s:result.append({'field':field,'op':'odd'})
    if '偶数' in s:result.append({'field':field,'op':'even'})
    if re.search(r'すでに解放|既に解放|持ってた|持っている|持ってる|所持済|解放済み',s):result.append({'field':'unlocked','op':'eq','value':1})
    if re.search(r'未解放|未所持|未クリア',s):result.append({'field':'unlocked' if field in ('base','plus','unlocked','talent') else 'clear','op':'eq','value':0})
    return result

def selector(s):
    result={}
    if re.search(r'通常',s) or ('コラボ' in s and re.search(r'対象外|除く|以外',s) and not s.startswith('コラボ')):result['collab']=False
    elif 'コラボ' in s:result['collab']=True
    m=re.search(r'超激レア|激レア|伝説レア|伝説|レア|EX|基本',s,re.I)
    if m:result['rarity']=RARITIES[m[0].upper() if m[0].lower()=='ex' else m[0]]
    if '大狂乱' in s:result['series']='大狂乱'
    elif re.search(r'ちび(?:系|は全員|全員|シリーズ)',s):result['series']='ちび'
    elif re.search(r'特急系|特急シリーズ',s):result['series']='特急'
    if re.search(r'るろうに剣心|るろ剣|るろ剣',s):result={'group':'るろうに剣心'}
    return result

def split_sentences(text):
    # Numbers in quantities and decimal multipliers remain intact.
    result=[]
    for raw in clean(text).splitlines():
        raw=raw.strip()
        if not raw:continue
        if re.fullmatch(r'【.*】|《.*》',raw):result.append(raw);continue
        for part in re.split(r'[。!！]+|(?=ただし)|(?=でも)|(?=だけど)|(?=元々)',raw):
            part=re.sub(r'^[①-⑳\-・\s]+','',part).strip()
            part=re.sub(r'^(?:ただし[、,]?|でも|だけど|あと|また[、,]?|それと|そして|まず[、,]?|とりあえず|すみません[、,]?|やっぱり)','',part).strip()
            if part:result.append(part)
    return result

class Reader:
    def __init__(self,plan,metadata):self.p=plan;self.metadata=metadata;self.context=None;self.section='';self.exclusion=None;self.last_selector={};self.current_field='base'
    def add(self,domain,field,expr,**kwargs):
        if not expr:raise ParseError('数値・計算方法を特定できません。')
        if expr['op']=='range' and not 0<=expr['bounds'][0]<=expr['bounds'][1]:raise ParseError('範囲の大小関係を確認してください。')
        if domain=='legend':
            self.p['conditional_rules'][:]=[r for r in self.p['conditional_rules'] if not (r['domain']=='legend' and r.get('series')==kwargs.get('series'))]
        rule={'domain':domain,'field':field,'expr':expr,**kwargs}
        self.p['conditional_rules'].append(rule);self.p['labels'].append({'key':'conditional_'+str(len(self.p['conditional_rules'])),'label':kwargs.get('label',field)+'：'+str(expr)})
    def cat(self,s,base=None,plus=None,field=None,expr=None,when=None):
        sel=selector(s)
        if not sel and not re.search(r'全キャラ|全部解放|全部.*キャラ',s):sel=self.last_selector or {}
        if 'rarity' not in sel and sel.get('collab') is not None and self.last_selector.get('rarity') is not None:sel['rarity']=self.last_selector['rarity']
        if re.search(r'コラボ.*含(?:む|め)',s):sel={}
        self.last_selector=sel
        for f,e in [('base',base),('plus',plus), (field,expr)]:
            if f and e:self.add('cat',f,e,selector=sel,when=when or [],label=s)
        self.context=('cat',sel)
        self.current_field=field or ('plus' if plus and not base else 'base')
    def named(self,names,base=None,plus=None,keep=False,unlocked=None,when=None,talents=False):
        names=re.split(r'[、,・]|と|および',names);names=[n.strip() for n in names if n.strip()]
        if any(re.search(r'すでに|レベル|以上|ただし|場合|現在',n) for n in names):raise ParseError('個別キャラ名を明示してください。')
        sel={'names':names}
        for f,e in [('base',base),('plus',plus),('unlocked',unlocked)]:
            if e:self.add('cat',f,e,selector=sel,when=when or [],label='・'.join(names))
        if keep:
            for f in ('unlocked','base','plus'):self.add('cat',f,{'op':'keep'},selector=sel,label='・'.join(names))
            self.add('talent','talent',{'op':'keep'},selector=sel,label='・'.join(names))
        if talents:self.add('talent','talent',{'op':'keep'},selector=sel,label='・'.join(names))
        self.context=('cat',sel);self.last_selector=sel
    def resource(self,field,s):
        self.context=('resource',field)
        s=s.lstrip(' :')
        # A paired condition has an explicit keep branch followed by its complement.
        if '現在値をxとして' in s:return
        bounded=re.search(r'(\d+)以上欲しい.*?(\d+)を超える',s)
        if bounded:
            self.add('resource',field,{'op':'range','bounds':[int(bounded[1]),int(bounded[2])]},priority=3,label=s);return
        pair=re.search(r'(\d+)(以上|未満|を超えている|を超えてる).*?(?:現状維持|変更しない|そのまま)',s)
        if pair:
            self.add('resource',field,{'op':'keep'},priority=3,when=[{'field':field,'op':'gt' if '超えて' in pair[2] else 'ge' if pair[2]=='以上' else 'lt','value':int(pair[1])}],label=s);return
        cond=conditions(s,field)
        tail=re.split(r'なら|の場合は|の場合|:|:',s,maxsplit=1)[-1] if cond else s
        expr=expression(tail)
        if cond and tail.lstrip().startswith('+') and re.match(r'\+\d+',tail.strip()):expr={'op':'add','value':int(re.match(r'\+(\d+)',tail.strip())[1])}
        self.add('resource',field,expr,priority=3,when=cond,label=s);self.context=('resource',field)
    def read(self,line):
        s=line.strip();s=s.lstrip('、,');s=re.sub(r'^ただ[、,]','',s);s=re.sub(r'^基本キャラの(?=ネコ)', '', s);compact=re.sub(r'\s+','',s)
        if s.startswith('【') or s.startswith('《'):
            self.section=s[1:-1];self.exclusion=None
            if re.search(r'追加|最初|通貨|チケット|キャラ|ステージ|全体',self.section):self.context=None;self.last_selector={}
            return True
        from .advanced_requests import read as read_advanced
        # Explicit subject, threshold and action are all required. Scope is never
        # inferred from a nearby rarity or from the first number in a sentence.
        rarities=r'(基本|EX|超激レア|激レア|伝説レア|レア)'
        field_pat=r'(基本レベル|レベル|\+値)'
        before=re.fullmatch(r'(?:現在(?:の)?)?'+field_pat+r'(?:が)?(\d+)(未満|以上|以下)の?'+rarities+r'(?:キャラ)?(?:だけ|は|を)(.+)',s)
        after=re.fullmatch(rarities+r'(?:キャラ)?の(?:現在の)?'+field_pat+r'が(\d+)(未満|以上|以下)(?:なら|の場合は|のキャラだけ)(.+)',s)
        if before and re.search(r'[、,。]|\d+(?:未満|以上|以下)',before[5]):before=None
        if after and re.search(r'[、,。]|\d+(?:未満|以上|以下)',after[5]):after=None
        if before or after:
            if before:field,threshold,op,rarity,tail=before.groups()
            else:rarity,field,threshold,op,tail=after.groups()
            action_field='plus' if re.search(r'\+値|^\+',tail) else 'base' if re.search(r'レベル|Lv',tail,re.I) else ('plus' if field=='+値' else 'base')
            expr=expression(tail)
            if expr is None:raise ParseError('条件の後に変更値または保持を指定してください。')
            self.cat(rarity,field=action_field,expr=expr,when=[{'field':'plus' if field=='+値' else 'base','op':{'未満':'lt','以上':'ge','以下':'le'}[op],'value':int(threshold)}])
            return True
        if read_advanced(self,s):return True
        if re.search(r'通常の超激レアについては以下|通常超激レアについては以下',s):
            self.context=('cat',{'rarity':4,'collab':False});self.last_selector=self.context[1];self.current_field='base';return True
        if re.search(r'^上限.*(?:丸め|止め)|^個別の固定値指定|^すでにレジェンドの隊員|^現在\d+個以上.*そのまま',s):
            if re.search(r'^現在\d+個以上',s):
                self.add('orbs','amount',{'op':'keep'},rank='S',when=[{'field':'amount','op':'ge','value':int(re.search(r'\d+',s)[0])}],label=s)
            return True
        if re.fullmatch(r'キャッツアイは種類ごとにバラバラでお願いします',s):return True
        multi_items=re.match(r'(.+?)の3種類については[、,]?(.*)',s)
        if multi_items:
            names=re.split('[・、]',multi_items[1])
            if set(names)!=set(['ネコボン','スニャイパー','おかめはちもく']):raise ParseError('バトルアイテム名を確認してください。')
            self.context=('items',{'group':'battle_items','names':names});self.current_field='amount'
            return self.read(multi_items[2])
        s=re.sub(r'^(?:やっぱり|すみません[、,]?|あと|まず|そして|それと)', '', s)
        s=re.sub(r'^(ネコカン|猫缶|XP|NP|経験値|降臨|イベントステージ|通常は\d|コラボは\d|\+値もMAX|レアチケット|プラチナチケット|レジェンドチケット)\s+',r'\1',s)
        bare=re.fullmatch(r'(基本|EX|レア|激レア|超激レア|伝説レア|伝説)(\d+(?:\+(?:\d+(?:[〜~]\d+)?|MAX))?)',s,re.I)
        if bare:s=bare[1]+'は全て'+bare[2]
        if re.search(r'これに変更で|超激レアだけ通常とコラボで分けたい',s):
            if '超激レア' in s:self.last_selector={'rarity':4};self.context=('cat',self.last_selector)
            return True
        if re.search(r'変更していない項目は最初の指示',s):return True
        if re.match(r'本能MAXも少し変更で[、,]',s):return self.read(s.split('、',1)[-1])
        if re.search(r'ネコカンカンの本能だけ一切変更しない',s):self.named('ネコカンカン',talents=True);return True
        if re.search(r'現在\+値が20以上.*20未満',s):
            self.cat('レアキャラ',plus={'op':'keep'},when=[{'field':'plus','op':'ge','value':20}])
            self.cat('レアキャラ',plus={'op':'set','value':30},when=[{'field':'plus','op':'lt','value':20}]);return True
        if re.fullmatch(r'メニュー\d+(?:=.*|不要)?',s):
            from .conditional_migration import migrate
            one=parse_request(s);rules,issues=migrate(one)
            if issues:raise ParseError('・'.join(x['reason'] for x in issues))
            if not rules:raise ParseError('この項目の取り消しは対象を自然文で指定してください。')
            for r in rules:
                self.add(r.pop('domain'),r.pop('field'),r.pop('expr'),**r)
            return True
        if 'ネコ特急系' in s and '解放' in s:
            self.cat('特急系',field='unlocked',expr={'op':'set','value':1})
            self.cat('特急系',base={'op':'keep'},plus={'op':'keep'});return True
        parts=re.split(r'[、,](?=(?:\d+(?:未満|以上|[〜~])|奇数なら|偶数なら|XP|NP|ネコカン(?=は|MAX|\d|:)|猫缶|アイテム|チケット|キャラ全解放|全キャラレベル|本能MAX|全ステージ|お宝最高|キャッツアイ|本能玉|レアチケット|プラチナチケット|レジェンドチケット|オトート|にゃんこ神社))',s)
        if len(parts)>1:
            for part in parts:
                if not self.read(part):raise ParseError('複数指定の対象を読み取れません：'+part)
            return True
        if re.match(r'キャラ全解放も訂正で[、,]',s):return self.read(re.split('[、,]',s,maxsplit=1)[1])
        if re.search(r'^全キャラ50.*訂正|ステージ.*変更お願いします|本能MAX.*変更|全キャラ.*これに変更|レベル変更するキャラ.*以下|以下の通り|次のキャラ.*例外|については以下',s):return True
        if re.search(r'チケット(?:全|全部).*MAX',s,re.I):
            for f in ('normal_tickets','rare_tickets','platinum_tickets','legend_tickets'):self.add('resource',f,{'op':'max'},label=f)
            return True
        if s.startswith('全ステージクリア'):
            for chapters in [list(range(9))]:
                for f in ('clear','treasure'):self.add('story',f,{'op':'atleast','value':1} if f=='clear' else {'op':'set','value':3},chapters=chapters,label=s)
            for series in ('legend','true_legend','zero_legend'):self.add('legend','clear',{'op':'atleast','value':1},series=series,stars=4,label=s)
            self.add('event','clear',{'op':'atleast','value':1},family='aku',label=s)
            self.add('event','clear',{'op':'atleast','value':1},family='event',label=s);return True
        if re.fullmatch(r'お宝最高.*',s):
            self.add('story','treasure',{'op':'set','value':3},chapters=list(range(9)),label=s);return True
        if re.search(r'全キャラレベル(\d+)',s):
            self.cat('全キャラ',base={'op':'set','value':int(re.search(r'レベル(\d+)',s)[1])});return True
        if re.match(r'\+値は?\d+[〜~]\d+',s):self.cat('全キャラ' if not self.last_selector else s,plus=expression(s));return True
        if re.fullmatch(r'\+値(?:も)?MAX.*',s,re.I):self.cat('全キャラ',plus={'op':'max'});return True
        if 'コラボキャラは対象外'  in s:
            for r in reversed(self.p['conditional_rules']):
                if r['domain']=='cat' and r.get('selector')==self.last_selector:r['selector']['collab']=False
            self.last_selector['collab']=False;return True
        if re.search(r'現在\+値が20以上.*20未満',s):
            self.cat('レアキャラ',plus={'op':'keep'},when=[{'field':'plus','op':'ge','value':20}])
            self.cat('レアキャラ',plus={'op':'set','value':30},when=[{'field':'plus','op':'lt','value':20}]);return True
        if re.search(r'本能.*MAX.*移動速度',s):
            self.add('talent','talent',{'op':'max'},selector={k:v for k,v in selector(s).items() if k!='rarity'},exclude_speed=True,label=s);self.p['conditional_preserve_speed']=True;return True
        if re.search(r'ほかはレベル(\d+)',s):
            other=re.search(r'ほかはレベル(\d+)',s)
            group=next((r.get('selector') for r in reversed(self.p['conditional_rules']) if r.get('selector',{}).get('group')),None)
            if not group:raise ParseError('「ほか」の対象コラボ名を指定してください。')
            self.add('cat','base',{'op':'set','value':int(other[1])},selector=group,label='同じコラボの他キャラ')
            return self.read(s.split('、ほか')[0])
        if re.search(r'すでに持って.*伝説レア.*変更しない',s):
            self.cat('伝説レア',base={'op':'keep'},plus={'op':'keep'},when=[{'field':'unlocked','op':'eq','value':1}]);return True
        if re.search(r'未クリアステージ.*触らない',s) and self.context and self.context[0]=='story':
            for f in ('clear','treasure'):self.add('story',f,{'op':'keep'},chapters=self.context[1],when=[{'field':'clear','op':'eq','value':0}],label=s)
            return True
        if self.context and self.context[0]=='resource':
            math=re.fullmatch(r'(?:x<|x＜)(\d+)(?:の場合は|なら)(.+)',s)
            math2=re.fullmatch(r'(\d+)(?:≦|<=|≤)x(?:<|＜)(\d+)(?:の場合は|なら)(.+)',s)
            math3=re.fullmatch(r'x(?:≧|>=|≥)(\d+)(?:の場合は|なら)(.+)',s)
            field=self.context[1]
            if math or math2 or math3:
                when=([{'field':field,'op':'lt','value':int(math[1])}] if math else [{'field':field,'op':'ge','value':int(math2[1])},{'field':field,'op':'lt','value':int(math2[2])}] if math2 else [{'field':field,'op':'ge','value':int(math3[1])}])
                tail=(math or math2 or math3).groups()[-1]
                self.add('resource',field,expression(tail.replace('x+','現在値+')),when=when,priority=3,label=s);return True
        if re.search(r'合計が58999.*超える',s) or re.fullmatch(r'58999を上限とする',s):return True
        if re.search(r'すでにMAX.*アイテム.*MAX未満',s):
            return self.read('アイテム全部MAX')
        if re.search(r'既存隊員.*名前・レアリティ.*変更しない',s):return True
        if re.search(r'ガマトト隊員.*現在の隊員を残し|足りない人数だけ追加',s):
            self.add('helpers','fill',{'op':'set','value':10},label=s);return True
        if re.search(r'下げない|低くなる.*下げ|値を減らす.*禁止|指定値より高い.*下げない',s):
            self.p['conditional_cat_no_decrease']=True if re.search(r'キャラ|レベル',s) else self.p.get('conditional_cat_no_decrease',False)
            if not re.search(r'キャラ|レベル',s):self.p['conditional_no_decrease']=True
            return True
        if re.search(r'指定していない|書いてない|書いていない',s):
            if re.search(r'MAX|マックス',s,re.I):raise ParseError('指定していない項目を全MAXにする範囲が曖昧です。対象を列挙してください。')
            return True
        if re.search(r'重複|矛盾|個別指定を優先|計算.*変更前|同じ項目.*複数回|ランダム.*キャラごと|一意に特定できない|略称・正式名|対応できない|無理なやつ|対象外キャラ|^最終的な値.*上限|^もし指定した数値.*上限|^合計が.*上限|^小数.*切り捨て|編成|お気に入り|並び順|使用中.*形態|最終.*確認|反映.*確認|項目だけ報告',s):return True
        if re.fullmatch(r'お願いします|代行お願いします|キャラ中心の依頼です|かなり細かい.*お願いします|おまかせでいいので強いデータにしてほしいです|こんな感じでお願いしたい|今回は固定値ではなく.*|基本的に|ただし|本能未実装キャラ.*|本能未実装.*|本能がある場合のみMAX|ほかはレベル40にしてください',s):
            if s=='ほかはレベル40にしてください':raise ParseError('「ほか」の対象コラボを指定してください。')
            return True
        for key,reason in AMBIGUOUS.items():
            if key in s and key!='現在使用中':raise ParseError(reason)
        if re.search(r'次のキャラ.*未解放.*まま|次のキャラ.*例外|4体だけ.*解放状態を維持',s):
            names=re.match(r'(.+?)(?:の4体だけ)',s)
            if names:self.named(names[1],unlocked={'op':'keep'});return True
            self.exclusion='all' if '未解放' in s else 'list';return True
        if self.exclusion=='all' and re.fullmatch(r'[ぁ-んァ-ヶ一-龯a-zA-Z]+',s):self.named(s,keep=True);return True
        if re.search(r'すでに解放されている場合.*完全に現状維持',s):return True
        if re.search(r'統率力.*(?:維持|まま)',s):return True
        if re.search(r'イベント.*チケット.*(?:変更|触ら|禁止)',s):return True
        if re.search(r'コラボ.*全(?:キャラクター|キャラ|員|て|部).*解放|キャラ全解放|キャラ.*未(?:所持|所有).*全部解放|基本的に.*全キャラクター.*解放',s):self.cat('全キャラ' if re.search(r'含め|基本的|未所持|キャラ全解放',s) else s,field='unlocked',expr={'op':'set','value':1});return True
        if re.search(r'通常入手.*全部解放',s):self.cat('通常キャラ',field='unlocked',expr={'op':'set','value':1});return True
        if re.search(r'未解放の伝説レア.*解放|伝説レア.*全部解放',s):self.cat('伝説レア',field='unlocked',expr={'op':'set','value':1});return True
        if re.search(r'持って.*レベル.*(?:変えない|いじらない)',s):
            self.cat('全キャラ',base={'op':'keep'},plus={'op':'keep'},when=[{'field':'unlocked','op':'eq','value':1}]);return True
        if re.search(r'第3形態.*(?:解放|対象)',s):
            self.add('cat','forms',{'op':'atleast','value':3},selector={'forms_min':3},no_decrease=True,label=s);return True
        if re.search(r'移動速度.*(?:そのまま|維持|変更しない|未解放|現在値キープ)',s):
            self.p['conditional_preserve_speed']=True
            for r in self.p['conditional_rules']:
                if r['domain']=='talent':r['exclude_speed']=True
            return True
        if re.search(r'本能.*(?:全|MAX|max|原則)',s) and not '本能玉' in s:
            self.add('talent','talent',{'op':'max'},selector={k:v for k,v in selector(s).items() if k!='rarity'},exclude_speed=self.p.get('conditional_preserve_speed',False),label=s);return True
        # Individual name + level/plus/keep, including evolution aliases.
        m=re.match(r'(.+?)(?:は|:|だけ)(.*)',s)
        if m and m[1] not in RES and not any(s.startswith(n) for n in ITEMS) and (m[1].startswith('ネコ') or m[1].startswith('比古') or m[1].startswith('志々雄') or 'ノノ' in m[1] or 'ちびガウ' in m[1]):
            names=m[1];tail=m[2]
            if self.section=='本能' and re.search(r'現状維持|変更しない',tail):self.named(names,talents=True);return True
            if '本能' in tail and re.search(r'現状|変更しない',tail):self.named(names,talents=True);return True
            if re.search(r'解放状態.*維持|持って.*未解放|持ってなければ.*解放しない',tail):self.named(names,keep=True);return True
            if '現状維持' in tail or ('そのまま' in tail and not re.search(r'レベル|\+値',tail)):self.named(names,keep=True);return True
            bm=re.search(r'(?:レベル|Lv)?(\d+)(?:[+＋](MAX|最大|\d+(?:[〜~]\d+)?))?',tail,re.I)
            base={'op':'set','value':int(bm[1])} if bm else None;plus=expression(bm[2]) if bm and bm[2] else None
            if re.search(r'基本レベル.*(?:まま|維持)|レベル.*現在.*まま|レベル変更なし',tail):base={'op':'keep'}
            if re.search(r'\+.*(?:維持|触ら|いじら|現在.*まま)',tail):plus={'op':'keep'}
            if re.search(r'\+値だけ.*増や|\+値.*今より',tail):plus=expression(tail.split('+値')[-1])
            if re.search(r'\+値だけ90',tail):base={'op':'keep'};plus={'op':'set','value':90}
            if base or plus:self.named(names,base,plus,when=conditions(tail,'base'));return True
        # Resource and battle item clauses.
        if re.match(r'レジェンドは\d+枚',s):self.resource('legend_tickets',s[len('レジェンド'):]);return True
        for name,field in sorted(RES.items(),key=lambda x:len(x[0]),reverse=True):
            if re.match(re.escape(name)+r'(?:は|が|:|だけ|について|も|\d|MAX|を)',s,re.I):self.resource(field,s[len(name):]);return True
        if re.match(r'その他のバトルアイテム',s):self.add('items','amount',{'op':'max'},group='battle_items',label=s);return True
        for name,canonical in ITEMS.items():
            if re.match(re.escape(name)+r'(?:は|:|だけ)',s):
                tail=s[len(name):];self.add('items','amount',expression(tail),group='battle_items',names=[canonical],priority=3,label=s);self.context=('items',{'group':'battle_items','names':[canonical]});return True
        if re.search(r'アイテム.*(?:全部|全|ぜんぶ).*(?:MAX|最大|上限|マックス)',s,re.I):
            for group in ('battle_items','catseyes','catamins','base_materials','catfruit'):self.add('items','amount',{'op':'max'},group=group,label=group)
            for field in ('catfood','xp','np','leadership',):self.add('resource',field,{'op':'max'},label=field)
            self.add('orbs','amount',{'op':'max'},label=s);return True
        if re.search(r'バトルアイテム.*\d+',s):self.add('items','amount',expression(s),group='battle_items',label=s);return True
        if re.search(r'キャッツアイ.*(?:全種類|全種).*\d+',s):self.add('items','amount',expression(s),group='catseyes',label=s);return True
        m=re.match(r'(EX|レア|激レア|超激レア|伝説)用は(.+)',s)
        if m:self.add('items','amount',expression(m[2]),group='catseyes',names=['キャッツアイ【'+m[1]+'】'],priority=3,label=s);return True
        if re.search(r'本能玉.*S.*10',s):self.add('orbs','amount',{'op':'set','value':10},rank='S',label=s);self.context=('orbs','S');return True
        if re.search(r'(?:現在|元から|元々).*10(?:個)?(?:以上|を超え).*そのまま|Sランク.*10.*減らさない',s):
            self.add('orbs','amount',{'op':'keep'},rank='S',when=[{'field':'amount','op':'ge','value':10}],label=s);return True
        # Rarity/series character edits and predicates against original levels.
        if re.search(r'基本|EX|レア|超激|伝説|大狂乱|ちび系|ちびは全員|通常は|コラボは',s,re.I) and re.search(r'レベル|Lv|\+|全員\d|全て\d|:\d|\d+以上|全て\d+',s,re.I):
            sel=selector(s);s=re.sub(r'\s+','',s);field='plus' if re.search(r'現在\+値|\+値だけ|\+値は現在|基本レベル.*変更せず',s) else 'base'
            when=conditions(s,field) if re.search(r'現在|すでに|元々|今の|今より',s) else []
            if re.search(r'基本レベル.*一切変更せず',s):self.cat(s,base={'op':'keep'},plus=expression(s.split('+値')[-1]),when=when);return True
            level=re.search(r'(?:レベル|Lv|全員|全て|:|は)(\d+(?:[〜~]\d+)?)(?:[+＋](\d+(?:[〜~]\d+)?|MAX|最大|なし))?',s,re.I)
            if when:
                tail=re.split(r'なら|の場合は|だけ|:',s)[-1];e=expression(tail)
                self.cat(s,field=field,expr=e,when=when);return True
            if level:
                base=expression(level[1]+'以上') if re.search(re.escape(level[1])+r'以上',s) else expression(level[1]);plus=expression(level[2]) if level[2] else None
                if re.search(r'付けられる最大値|\+.*付けられる.*MAX|\+.*最大まで',s,re.I):plus={'op':'max'}
                if '+値' in s and not when:
                    plus=expression(s.split('+値')[-1]) or plus
                if '+値なし' in s or '+なし' in s:plus={'op':'set','value':0}
                if level[2]=='なし':plus={'op':'set','value':0}
                if re.search(r'\+値.*(?:触ら|維持|現在.*まま)',s):plus={'op':'keep'}
                self.cat(s,base,plus);return True
            plus_only=re.search(r'(?:\+値|\+)(\d+(?:[〜~]\d+)?)',s)
            if plus_only:self.cat(s,plus=expression(plus_only[1]));return True
            if re.search(r'\+値.*(?:MAX|最大)|\+振れる.*MAX',s):self.cat(s,plus={'op':'max'});return True
        if re.search(r'るろ.*キャラ.*全部解放|るろ.*全解放',s):self.cat(s,field='unlocked',expr={'op':'set','value':1});return True
        # Story treasure and clear targets are separate writes.
        if re.search(r'宇宙編.*第2章までクリア.*第3章.*未クリア',s):
            self.add('story','clear',{'op':'atleast','value':1},chapters=[6,7],label=s)
            for f in ('clear','treasure'):self.add('story',f,{'op':'keep'},chapters=[8],label='宇宙編第3章を保持')
            self.context=('story',[8]);return True
        if re.search(r'日本編|未来編|宇宙編|日本未来宇宙',s):
            chapters=[]
            for name,start in [('日本編',0),('未来編',3),('宇宙編',6)]:
                if name in s or '日本未来宇宙' in s:
                    m=re.search(re.escape(name)+r'(?:の)?第?(\d)章',s)
                    chapters += [start+int(m[1])-1] if m else list(range(start,start+3))
            if re.search(r'宇宙編.*第2章まで',s):chapters=[6,7]
            preserve=bool(re.search(r'維持|変更なし|触らない|未クリアのまま',s))
            if preserve:
                when=[{'field':'clear','op':'eq','value':0}] if '未クリア' in s and 'お宝' in s else []
                for f in ('clear','treasure'):self.add('story',f,{'op':'keep'},chapters=chapters,when=when,label=s)
            else:
                if re.search(r'クリア|終わらせ',s) and not re.search(r'クリア済み.*お宝|未クリア.*お宝',s):self.add('story','clear',{'op':'atleast','value':1},chapters=chapters,label=s)
                if re.search(r'お宝|最高',s):
                    when=[{'field':'clear','op':'gt','value':0}] if re.search(r'クリア済み|未クリア.*触ら|未クリア.*お宝',s) else []
                    self.add('story','treasure',{'op':'set','value':3},chapters=chapters,when=when,label=s)
            self.context=('story',chapters);return True
        if re.search(r'普通のお宝.*普通のまま',s) and self.context and self.context[0]=='story':self.add('story','treasure',{'op':'keep'},chapters=self.context[1],when=[{'field':'treasure','op':'eq','value':2}],label=s);return True
        if re.search(r'零レジェンド|真レジェンド|真レジェ|レジェンド|レジェ',s) and self.section not in ('チケット','チケット類'):
            series='zero_legend' if '零レジェンド' in s else 'true_legend' if '真レジェ' in s else 'legend'
            if re.search(r'現状|変更なし|維持|いじらない',s) and not re.search(r'星[1-3].*クリア',s):
                self.add('legend','clear',{'op':'keep'},series=series,stars=4,label=s);return True
            m=re.search(r'星(\d)(?:[〜~]([1-4]))?|★(\d)',s)
            stars=int(m[2] or m[1] or m[3]) if m else 4
            if re.search(r'星4.*維持',s):stars=3
            self.add('legend','clear',{'op':'atleast','value':1},series=series,stars=stars,until_penultimate='最終ステージの1つ前' in s,progressive='現在クリア済みの星数' in s,label=s);return True
        if re.search(r'降臨|イベントステージ|イベステ',s):
            if re.search(r'現状|維持|変更しない|変更なし|触らない',s):
                self.add('event','clear',{'op':'keep'},family='event',label=s);return True
            if '降臨' in s:
                raise ParseError('「降臨」のイベント系統一覧が未定義です。具体的なステージ名を指定してください。')
            self.add('event','clear',{'op':'atleast','value':1},family='event',label=s);return True
        if '大狂乱' in s and 'ステージ' in s:
            self.add('event','clear',{'op':'atleast','value':1},family='event',map_name='大狂乱',label=s);return True
        if '魔界' in s:
            self.add('event','clear',{'op':'atleast','value':1},family='aku',label=s);return True
        if 'ガマトト' in s and re.search(r'レベル.*(?:MAX|上限)|全MAX',s,re.I):self.add('action','gamatoto_max',{'op':'max'},label=s);return True
        if re.search(r'隊員|10人|10体',s):
            if re.search(r'レジェンド|レジェ',s):
                self.add('helpers','legend',{'op':'set','value':10},label=s);return True
            if re.search(r'既存|すでに|現在',s):return True
            raise ParseError('隊員のクラスと最終人数を指定してください。')
        if re.search(r'施設|にゃんこ砲|砲も',s) and re.search(r'MAX|マックス|カンスト',s,re.I):self.add('action','facilities',{'op':'max'},label=s);return True
        if re.search(r'オトート.*(?:MAX|全開発|全てMAX)',s,re.I):self.add('action','ototo',{'op':'max'},label=s);return True
        if re.search(r'神社.*MAX',s,re.I):self.add('action','shrine',{'op':'max'},label=s);return True
        if re.search(r'エラー削除|エラー.*削除',s):self.add('action','remove_errors',{'op':'set','value':1},label=s);return True
        # Continuations are grounded in the preceding resource/character context.
        if self.context and re.search(r'現在|元々|今すでに|すでに|NPも|XPは|\+値は|レベル[1-4]|^\d+.*(?:未満|以上|なら)|偶数|奇数|今の数',s):
            if self.context[0]=='resource':self.resource(self.context[1],s);return True
            if self.context[0]=='items':
                opts=self.context[1];cond=conditions(s,'amount');tail=re.split(r'なら|の場合|:',s)[-1]
                self.add('items','amount',expression(tail),when=cond,priority=3,**opts,label=s);return True
            if self.context[0]=='cat':
                field='plus' if '+値' in s else self.current_field;when=conditions(s,field)
                tail=re.split(r'なら|の場合|:',s)[-1];self.add('cat',field,expression(tail),selector=self.context[1],when=when,label=s);self.current_field=field;return True
        return False

def interpret_conditional(text,previous,metadata):
    from .conditional_migration import migrate,display
    carried,carry_issues=migrate(previous or {})
    p=parse_request('お願いします',previous)
    p['conditional_mode']=True;p.setdefault('conditional_rules',carried)
    p.setdefault('conditional_no_decrease',False)
    reader=Reader(p,metadata)
    unresolved=copy.deepcopy(p.get('unresolved',[]))+carry_issues
    for line in split_sentences(text):
        if re.match(r'引き継ぎ|認証|引継ぎ',line):p.update(parse_request(line,p));continue
        before=copy.deepcopy(p)
        state=copy.deepcopy((reader.context,reader.section,reader.exclusion,reader.last_selector,reader.current_field))
        try:
            if not reader.read(line):raise ParseError('対象・条件・変更値を特定できません。具体的な対象と数値を指定してください。')
        except (ParseError,ValueError) as exc:
            p.clear();p.update(before)
            reader.context,reader.section,reader.exclusion,reader.last_selector,reader.current_field=state
            unresolved.append({'input':line,'reason':str(exc)})
    # Resolve names for the confirmation, and re-check before transfer.
    if metadata and isinstance(metadata,dict) and any(r['domain'] in ('cat','talent','account') for r in p['conditional_rules']):
        try:
            index=NameIndex(metadata);need_collab=any(r.get('selector',{}).get('collab') is not None or r.get('selector',{}).get('group') for r in p['conditional_rules'])
            collabs=collaboration_ids(metadata,metadata['characters']) if need_collab else set()
            for r in p['conditional_rules']:
                if r['domain'] in ('cat','talent','account'):
                    try:r['expected_ids']=select_ids(r.get('selector',{}),metadata,index,collabs)
                    except ParseError as e:unresolved.append({'input':r.get('label','対象キャラ'),'reason':str(e)})
        except ParseError as e:unresolved.append({'input':'キャラ照合','reason':str(e)})
    for rule in p['conditional_rules']:
        if rule['domain']=='account' and not rule.get('selector',{}).get('names'):
            unresolved.append({'input':rule.get('label','ランク・所持数'),'reason':'調整対象のキャラ名・No.番号を、同じ文に「、調整対象:名前1、名前2」の形式で指定してください。対象を勝手に追加・削除しません。'})
    p['unresolved']=list({(x['input'],x['reason']):x for x in unresolved}.values())
    p['selected']={};p['custom_amounts']={};p['level_rules']={};p['talents_max']=False;p['unlock_queries']=[];p['level_exclusion_queries']=[]
    p['menu_pending']={};p['missing']=[x for x in p['missing'] if x!='代行内容']
    if not p['conditional_rules']:p['missing'].append('代行内容')
    p['blockers']=[f'未解釈項目{i+1}：{x["input"]} — {x["reason"]}' for i,x in enumerate(p['unresolved'])]
    p['labels']=[{'key':'conditional_'+str(i),'label':display(r)} for i,r in enumerate(p['conditional_rules'])]
    if p.get('conditional_no_decrease'):p['labels'].append({'key':'conditional_keep_high','label':'共通条件：現在値より低くしない'})
    elif p.get('conditional_cat_no_decrease'):p['labels'].append({'key':'conditional_keep_high','label':'共通条件：キャラのレベル・＋値を下げない'})
    p['labels'].append({'key':'conditional_protection','label':'編成・使用形態・お気に入り・並び順を保持。変更前の値で計算し、適用後に検証'})
    p['ready']=not p['missing'] and not p['blockers'];p['_review_required']=True;p.pop('_review_confirmed',None)
    p['inferences']=p.get('inferences',[])
    return p
