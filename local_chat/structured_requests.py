"""Section-aware orders. Parsed intent never supplies unverified character IDs."""
import copy
import re
import unicodedata

from .parser import ParseError, parse_request, split_clauses, has_edit_actions
from .menus import MENU_BY_NUMBER
from .menu_input import NAME_INDEX, normalize_menu_name, parse_menu_line
from .named_characters import NameIndex

RARITIES = {'基本': 0, 'EX': 1, 'レア': 2, '激レア': 3, '超激レア': 4, '伝説レア': 5}
RESOURCE_NAMES = {4:'catfood',5:'xp',6:'np',7:'leadership',15:'normal_tickets',
                  16:'rare_tickets',17:'platinum_tickets',18:'legend_tickets'}
GROUP_NAMES = {8:'battle_items',9:'catseyes',10:'catamins',11:'materials',
               12:'matatabi',13:'talent_orbs',14:'beast_stones',82:'grand_abyss'}
ALIASES = {'ネコビタン全種類':10,'城の素材全種類':11,'キャッツアイ全種類':9,
           '猫缶':4,'XP':5,'NP':6,'リーダーシップ':7,'本能玉':13,
           '施設レベル':61,'プレイ時間':84,'道場スコア':47,
           'オトート全城':69,'にゃんこ神社':71,'チュートリアルスキップ':75}
MAIN_HEADINGS = {'代行依頼内容','アイテム・所持数変更','アイテム','チケット類',
                 'キャラ解放','キャラ開放','キャラレベル変更','レベル変更','その他のレベル指定',
                 'ステージ・お宝・スコア','施設','ガマトト・オトート','ガマトト&オトート',
                 'その他の仕様','本能解放','全体の処理ルール'}
STAGE_HEADINGS = {'ステージクリア','採点スコア・道場','ゾンビ・その他ステージ'}


def plain_main_heading(text):
    """Recognize only whole known titles, never numbers in an instruction."""
    value=re.sub(r'^(?:\d{1,2}[.、)]?\s*)','',text.strip()).rstrip(':')
    return value if value in MAIN_HEADINGS else None


def document_lines(text):
    text = unicodedata.normalize('NFKC', text).replace('\\#', '#')
    text = re.sub(r'#{1,4}\s*(?=[【《])','',text)
    text = re.sub(r'([【《][^】》\n]+[】》])',r'\n\1\n',text)
    # Inline footnotes retain their own scope instead of disappearing inside a
    # heading's level rule or a quantity argument.
    text = re.sub(r'[ \t]*※', '\n※', text)
    # Markdown copies sometimes represent paragraph boundaries with two spaces.
    text = re.sub(r' {2,}', '\n', text)
    text = re.sub(r'(?<!#)\s+(?=#{1,4}\s)', '\n', text)
    text = re.sub(r'\s+(?=\d{1,2}[.](?!\d))', '\n', text)
    text = re.sub(r'\s+-\s+(?=[^\W\d_])','\n- ',text)
    text = re.sub(r'\s+-\s+(?=\d{4}年)','\n- ',text)
    # Only complete separator lines are ignored. An ellipsis embedded in an
    # instruction must still be reviewed, including after a Markdown bullet.
    return [s.strip() for s in text.splitlines() if s.strip() and not re.fullmatch(r'[━─—=\-⋮⋯…・•·. \t]+',s.strip())]


def is_document(text):
    lines=document_lines(text)
    if any(re.match(r'^#{1,4}\s',s) or re.match(r'^[ABC](?:\+)?(?:グループ|ランク)\s*:',s,re.I) or re.fullmatch(r'【(?:代行依頼内容|キャラ解放|本能解放|Aグループ|Bグループ|Cグループ)】',s) for s in lines):return True
    titles={plain_main_heading(s) for s in lines}-{None}
    descriptive={'代行依頼内容','アイテム・所持数変更','キャラレベル変更','その他のレベル指定','全体の処理ルール','ステージ・お宝・スコア','その他の仕様'}
    return len(lines)>1 and (len(titles)>=2 or bool(titles&descriptive) or any(re.match(r'^\d',s) and plain_main_heading(s) for s in lines))


def level_rule(text):
    text = re.sub(r'\s+', '', text)
    base = re.search(r'基本(?:Lv\.?|レベル)(\d+|MAX)',text,re.I)
    plus = re.search(r'(?:プラス値|\+値)\+?(\d+|MAX)(?:[〜~～-]\+?(\d+)|から\+?(\d+))?',text,re.I)
    if not base and not plus:
        old = re.fullmatch(r'(\d+)(?:レベル)?\+(\d+)(?:から|[〜~～-])\+?(\d+)(?:.*)?',text)
        if old:
            return {'base':{'op':'set','value':int(old[1])},'plus':{'op':'range','bounds':[int(old[2]),int(old[3])]}}
        return None
    result = {}
    if base: result['base'] = {'op':'max'} if base[1].upper()=='MAX' else {'op':'set','value':int(base[1])}
    if plus:
        if plus[1].upper()=='MAX': result['plus']={'op':'max'}
        elif plus[2] or plus[3]: result['plus']={'op':'range','bounds':[int(plus[1]),int(plus[2] or plus[3])]}
        else: result['plus']={'op':'set','value':int(plus[1])}
    return result


def parse_document(text):
    if not isinstance(text,str) or not text.strip() or len(text)>16000:
        raise ParseError('依頼を1〜16000文字で入力してください。')
    out = dict(resources={},collections={},unlock_rules=[],level_groups=[],talent_rules=[],
               stage_requests=[],other_requests=[],unparsed=[],warnings=[],issues=[],operations=[],notes=[],collab_cutoffs=[],excluded_collab_names=[])
    section='resource'; scope=''; group=None; talent=None

    def issue(source,reason,category='needs_input'):
        tag={'needs_input':'条件不足','unsupported':'機能未対応','metadata':'定義確認'}[category]
        item={'input':source,'reason':'【'+tag+'】'+reason,'category':category}
        if item not in out['issues']:out['issues'].append(item)

    def heading(title):
        nonlocal section,scope,group,talent
        title=re.sub(r'^\d+\s*','',title).strip()
        group=None;talent=None;scope=''
        if re.search('本能解放|その他の本能解放割合',title):section='talent'
        elif re.search('レベル変更|その他のレベル指定',title):section='level'
        elif re.search('キャラ解放|キャラ開放',title):section='unlock'
        elif re.search('アイテム|所持数|チケット',title):section='resource'
        elif re.search('ステージ|スコア|お宝',title):section='stage'
        elif re.search('全体の処理ルール',title):section='rules'
        elif re.search('施設|ガマトト|オトート|仕様|その他メニュー',title):section='other'
        elif title in ('代行依頼内容','キャラ類'):section='resource' if title=='代行依頼内容' else 'unlock'
        else:
            section='unknown';issue(title,'この見出しの範囲を判断できません。対象の操作を指定してください。')

    def command(line):
        # Prices and negations never become quantities, even if a number follows.
        if re.search(r'円|[¥￥]|変更しない|変更せず|例です|保持|除外|以外',line):
            issue(line,'料金・例示・保持条件を変更数量として扱いません。変更する対象と値を別の行で指定してください。');return
        numbered=re.match(r'^(\d+)[.]\s*(.+)',line)
        n=int(numbered[1]) if numbered else None
        body=numbered[2] if numbered else line
        name,sep,arg=body.partition(':')
        name=name.strip();arg=arg.strip()
        if arg=='未指定':out['notes'].append(line+'（変更しません）');return
        known=NAME_INDEX.get(normalize_menu_name(name),set()) | ({ALIASES[name]} if name in ALIASES else set())
        # A few explicit menu aliases, preserving the declared scope.
        extra={'全ステージクリア':38,'全レジェンドステージクリア':60,'イベントステージクリア':59,
               '各ステージのクリア回数':42,'未来編の採点スコア':44}
        if name in extra:known=known | {extra[name]}
        if n is not None and n not in known:
            issue(line,'メニュー番号と項目名を照合できません。番号・対象名を確認してください。');return
        if n is None:
            if len(known)!=1:
                parsed=parse_menu_line(line)
                if parsed and len(parsed['numbers'])==1:
                    out['operations'].append({'kind':'command','command':parsed['command'],'source':line});return
                issue(line,'数量・対象・操作を特定できません。対象名と変更値を指定してください。');return
            n=next(iter(known))
        entry=MENU_BY_NUMBER[n]
        from .targeted_requests import scoped_menu_operation
        scoped=scoped_menu_operation(n,arg,line)
        if scoped is not None:
            out['operations'].append(scoped);return
        if entry['status']!='ready':
            reason=entry.get('requires','対象の指定または個別の実行接続が必要です。')
            issue(line,reason,'unsupported' if re.search('未実装|未対応',reason) else 'needs_input');return
        arg=re.sub(r'^(?:各|Lv[.]?)\s*','',arg,flags=re.I)
        arg=re.sub(r'(?:個|枚|回)$','',arg).strip()
        if n==84 and arg:
            m=re.fullmatch(r'(\d+)時間(\d+)分(?:(\d+)秒)?',arg)
            if m:arg=f'{m[1]}:{m[2]}:{m[3] or 0}'
            clock=re.fullmatch(r'(\d+):(\d+)(?::(\d+))?',arg)
            if clock:
                h,minute,sec=int(clock[1]),int(clock[2]),int(clock[3] or 0)
                if h>9999 or minute>59 or sec>59:
                    issue(line,'プレイ時間は9999時間59分59秒以内、分・秒は0〜59で指定してください。');return
                out['operations'].append({'kind':'profile','selector':{},'rule':{'playtime':{'op':'set','value':h*3600+minute*60+sec}},'source':line})
                out['resources']['play_time']={'hours':h,'minutes':minute,'seconds':sec};return
        if sep and not re.fullmatch(r'(?i:MAX)|\d+(?:[〜~～-]\d+|から\d+|:\d+:\d+)?',arg):
            issue(line,'指定値を読み取れません。固定値・範囲・MAXで指定してください。');return
        if entry['kind']=='action' and arg and not re.fullmatch(r'(?i:MAX|Lv[.]?MAX)',arg):
            issue(line,'この一括操作に付けた条件は未対応です。対象と変更値を確認してください。');return
        arg=arg.replace('から','〜')
        cmd=f'メニュー{n}'+('='+arg if arg else '')
        out['operations'].append({'kind':'command','command':cmd,'source':line})
        if n in RESOURCE_NAMES or n in GROUP_NAMES:
            val={'range':list(map(int,re.split('[〜~～-]',arg)))} if re.fullmatch(r'\d+[〜~～-]\d+',arg) else int(arg) if arg.isdigit() else arg or 'MAX'
            out['resources' if n in RESOURCE_NAMES else 'collections'][(RESOURCE_NAMES|GROUP_NAMES)[n]]=val
        elif section=='stage':out['stage_requests'].append({'request':line})
        else:out['other_requests'].append({'request':line})

    for raw in document_lines(text):
        s=re.sub(r'^[-•・]\s*','',raw).strip()
        from .character_forms import is_third_form_request
        if is_third_form_request(s):
            out['operations'].append({'kind':'level','selector':{'forms_min':3},
                'rule':{'form':{'op':'set','value':3}},'source':s})
            continue
        cutoff=re.fullmatch(r'(\d{4})年以前を最後に開催されたコラボシリーズは、全レアリティで(?:取得|解放)対象から除外[。]?',s)
        priority_note=re.fullmatch(r'(?:\d+[.]\s*)?(\d{4})年以前のコラボ除外条件は、他の解放割合より優先する[。]?',s)
        if cutoff or priority_note:
            out['collab_cutoffs'].append({'year':int((cutoff or priority_note)[1]),'source':s});continue
        exclusion=re.fullmatch(r'(?:取得対象から除外するコラボ|除外コラボ)\s*:\s*(.+)',s)
        if exclusion:
            out['excluded_collab_names'].extend(split_clauses(exclusion[1]));continue
        keep_note=s.removeprefix('※').strip().rstrip('。')
        if re.fullmatch(r'本能(?:の)?(?:実装)?定義が(?:取得できない|ない)キャラは[、,]?(?:本能だけ|本能のみ|本能を)(?:変更せず)?(?:現在のまま|現状維持|保持)(?:にしてください|してください|する)?',keep_note):
            out['keep_unknown_talents']=True;out['notes'].append(s);continue
        title=re.sub(r'^#{1,4}\s*','',s)
        g=re.fullmatch(r'(?:###\s*)?([ABC](?:\+)?)(?:グループ|ランク)\s*:(.+)',s,re.I)
        if not g:g=re.fullmatch(r'([ABC](?:\+)?)(?:グループ|ランク)\s*:(.+)',title,re.I)
        if g:
            section='level';group={'name':g[1].upper(),'characters':[],'rule':level_rule(g[2]),'raw_rule':g[2]}
            out['level_groups'].append(group)
            if not group['rule']:issue(raw,'グループの基本Lv・＋値を指定してください。')
            continue
        bare_group=re.fullmatch(r'([ABC](?:\+)?)(?:グループ|ランク)',s[1:-1] if s.startswith('【') and s.endswith('】') else s,re.I)
        if bare_group:
            section='level';group={'name':bare_group[1].upper(),'characters':[],'rule':None,'raw_rule':None}
            out['level_groups'].append(group);issue(raw,'このグループの基本Lv・＋値が未指定です。前のグループの値は引き継ぎません。');continue
        if re.match(r'^#{1,4}\s',s):heading(title);continue
        main_title=plain_main_heading(s)
        if main_title:
            heading(main_title);continue
        if section=='unlock' and s in ('恒常ガチャ','コラボキャラ','その他'):
            scope=s;continue
        if section=='talent' and s in ('超激レア・指定キャラ','その他の本能解放割合'):
            talent=None;continue
        if s in STAGE_HEADINGS:
            heading(s);continue
        h=re.fullmatch(r'[【《](.+)[】》]',s)
        if h:
            if h[1]=='解放':heading('キャラ解放')
            elif h[1]=='レベル変更':heading('レベル変更')
            elif section=='unlock' and h[1] in ('恒常ガチャ','コラボキャラ','その他'):scope=h[1]
            elif section=='talent' and h[1] in ('超激レア・指定キャラ','その他の本能解放割合'):talent=None
            else:heading(h[1])
            continue
        if re.fullmatch(r'対象キャラ[:：]?',s):continue
        if s.startswith('※'):
            note=s[1:].strip().rstrip('。')
            if section=='level' and group and re.fullmatch(r'コラボキャラのプラス値は\+?\d+(?:[〜~～-]|から)\+?\d+',note):
                nums=list(map(int,re.findall(r'\d+',note)));group['collaboration_plus_range']=nums;continue
            if re.fullmatch(r'範囲指定は各種類に個別適用してください|個別指定があるキャラはA〜Cグループの設定を優先してください。実際の上限を超える指定は確認対象にしてください|本能が存在しないキャラや未解放の本能は、無理に適用せず確認対象としてください',note):
                out['notes'].append(s);continue
            issue(s,'この補足条件の適用範囲を確認してください。');continue
        if section=='rules':
            note=re.sub(r'^\d+[.]\s*','',s).rstrip('。')
            supported=[r'「AからB」はA以上B以下の範囲指定として扱う',r'「全種類」「全種」は該当カテゴリの全アイテムに適用する',
                       r'キャラの略称と括弧内の正式名称は同一キャラとして扱う',r'同名キャラは括弧内の名称や所属シリーズを優先して区別する',
                       r'個別指定のない項目は勝手に追加・変更しない',r'数値が未指定の項目は未指定のままにする',
                       r'処理前にキャラ数・レベル・アイテム数・除外対象を一覧表示する',r'解釈が不確かな項目は確認対象として表示する']
            if note in supported or note=='「80%」「90%」などは対象キャラ集合を確定してから計算する':out['notes'].append(s)
            else:issue(s,'この共通条件は自動適用できません。必要な対象・除外条件を確認してください。','unsupported')
            continue
        if section=='unlock':
            out['unlock_rules'].append({'request':s,'scope':scope})
            if re.fullmatch(r'基本キャラ\s*:\s*全員解放',s):
                out['operations'].append({'kind':'level','selector':{'rarity':0},
                    'rule':{'unlocked':{'op':'set','value':1}},'source':s})
                continue
            pct=re.fullmatch(r'(コラボ系|(?:コラボの)?(?:超激レア|伝説レア|激レア|レア)(?:・(?:超激レア|伝説レア|激レア|レア))*)\s*:(?:全対象の)?(\d+)%を?解放',s)
            if pct:
                percent=int(pct[2])
                if not 0<=percent<=100:issue(s,'所持率は0〜100%で指定してください。');continue
                target=pct[1];selector={}
                if target.startswith('コラボ') or scope=='コラボキャラ':selector['collab']=True
                elif scope=='恒常ガチャ':selector['regular_gacha']=True
                for rarity in ([None] if target=='コラボ系' else [RARITIES[n] for n in target.removeprefix('コラボの').split('・')]):
                    sel={**selector,**({'rarity':rarity} if rarity is not None else {})}
                    out['operations'].append({'kind':'ownership','selector':sel,'rule':{'unlocked':{'op':'percent','value':percent}},'source':s})
                continue
            if '%' in s or re.search(r'\d+割|開催|除外',s):
                issue(s,'開催年の対象定義または解放対象の範囲を照合できません。対象コラボ・キャラを指定してください。','unsupported');continue
            if re.match(r'^\d+[.]',s):command(s)
            elif s=='エラーキャラを削除':out['operations'].append({'kind':'command','command':'メニュー63','source':s})
            elif re.fullmatch(r'.+?(?:も|を)削除(?:してください)?[。]?',s):
                name=re.sub(r'(?:も|を)削除(?:してください)?[。]?$','',s)
                out['operations'].append({'kind':'level','names':[name],'rule':{'unlocked':{'op':'set','value':0}},'source':s,'removal':True})
            else:issue(s,'この指定キャラ削除の実行接続は未対応です。' if '削除' in s else '解放する対象を正式名またはNo.番号と操作で指定してください。','unsupported' if '削除' in s else 'needs_input')
            continue
        if section=='level':
            category=re.fullmatch(r'(基本キャラ|EX|レア|激レア|超激レア|伝説レア)\s*:(.+)',s,re.I)
            if category:
                name=category[1].replace('基本キャラ','基本').upper() if category[1].lower()=='ex' else category[1].replace('基本キャラ','基本')
                rule={'base':{'op':'max'},'plus':{'op':'max'}} if re.fullmatch(r'各キャラのMAXレベル',category[2],re.I) else level_rule(category[2])
                if rule:out['operations'].append({'kind':'level','selector':{'rarity':RARITIES[name]},'rule':rule,'source':s})
                else:issue(s,'基本Lv・＋値の指定を確認してください。')
            elif s.startswith('↑') and group:
                group['rule']=level_rule(s[1:]);group['raw_rule']=s[1:]
            elif group:
                try:group['characters'].extend({'name':x,'original':x} for x in split_clauses(s))
                except ParseError as e:issue(s,str(e))
            else:issue(s,'キャラ一覧に対応するグループ名・レベル条件を指定してください。')
            continue
        if section=='talent':
            out['talent_rules'].append({'request':s})
            if re.search(r'本能.*(?:%|割)',s):
                p=re.search(r'(\d+)%',s)
                talent={'request':s,'names':[],'percent':int(p[1]) if p else None};out['talent_rules'][-1]=talent
                if not p or not 0<=talent['percent']<=100:issue(s,'本能Lvの割合は0〜100%で指定してください。')
            elif talent and not re.search(r'%|割',s):
                talent['names'].extend(split_clauses(s))
            else:
                p=re.fullmatch(r'(上記以外の超激レア|超激レア|激レア|レア|EX)\s*:(\d+)%',s,re.I)
                if p and 0<=int(p[2])<=100:
                    sel={'rarity':RARITIES[p[1].replace('上記以外の','').upper() if p[1].lower()=='ex' else p[1].replace('上記以外の','')]}
                    op={'kind':'talent','selector':sel,'rule':{'talent':{'op':'percent','value':int(p[2])}},'source':s}
                    if p[1].startswith('上記以外'):op['exclude_names']=[n for t in out['talent_rules'] for n in t.get('names',[])]
                    out['operations'].append(op)
                else:issue(s,'本能の対象と上限Lvに対する割合を指定してください。')
            continue
        if section=='unknown':issue(s,'未対応の見出しに属するため操作を確定できません。');continue
        command(s)

    for g in out['level_groups']:
        if not g['characters']:issue(g['name']+'グループ','対象キャラが指定されていません。')
        if g['rule']:out['operations'].append({'kind':'level','group':g['name'],'names':[c['name'] for c in g['characters']],
                                             'rule':g['rule'],'collaboration_plus_range':g.get('collaboration_plus_range'),'source':g['name']+'グループ'})
    for t in out['talent_rules']:
        if t.get('names') and t.get('percent') is not None and 0<=t['percent']<=100:
            out['operations'].append({'kind':'talent','names':t['names'],'rule':{'talent':{'op':'percent','value':t['percent']}},'source':t['request']})
    out['summary']={'character_level_groups':len(out['level_groups']),
                    'characters_in_level_groups':sum(len(g['characters']) for g in out['level_groups']),
                    'talent_characters':sum(len(t.get('names',[])) for t in out['talent_rules']),
                    'unparsed_lines':len(out['issues'])}
    out['unparsed']=[x['input'] for x in out['issues']]
    out['warnings']=[x['input']+'：'+x['reason'] for x in out['issues']]
    out['requires_manual_review']=True
    out['execution_supported']=False  # Preview is never an execution endpoint.
    return out


def interpret_document(text,previous,metadata):
    doc=parse_document(text)
    # A whole structured order replaces its earlier contribution. Keep the
    # original non-document draft and the latest dedicated credentials.
    base=copy.deepcopy((previous or {}).get('_document_base',previous or {}))
    base.pop('_document_base',None)
    replaced_conditional=bool((previous or {}).get('conditional_mode') or base.get('conditional_mode') or (previous or {}).get('conditional_rules') or base.get('conditional_rules'))
    if replaced_conditional:
        # A new document is a replacement request, not an additive migration of
        # old heuristic rules. Only dedicated credentials survive the switch.
        base={}
    for key in ('transfer_code','auth_code'):
        if previous is not None:base.pop(key,None)
        if previous and previous.get(key):base[key]=previous[key]
    plan=parse_request('お願いします',base)
    issues=copy.deepcopy(plan.get('unresolved',[]))+doc['issues']
    plan['section']='';plan.setdefault('document_rules',[])
    kept_talent_names=set()
    index=NameIndex(metadata) if metadata else None
    from .targeted_requests import ownership_exclusions
    excluded,exclusion_labels,exclusion_issues=ownership_exclusions(doc,metadata)
    issues.extend(exclusion_issues)
    plan['_pending_document_ownership']=bool(exclusion_issues and any(op['kind']=='ownership' for op in doc['operations']))
    plan['_document_source']=text
    plan['_year_exclusion_cancellable']=bool(doc['collab_cutoffs'] and not doc['excluded_collab_names'])
    removal_names=[name for op in doc['operations'] if op.get('removal') for name in op.get('names',[])]
    for op in doc['operations']:
        # Ratios have no meaningful population until exclusion membership is
        # known. Do not show a provisional ownership count as a reviewed one.
        if op['kind']=='ownership' and exclusion_issues:continue
        if op['kind']=='command':
            try:
                plan['section']='';plan=parse_request(op['command'],plan);plan['section']=''
            except ParseError as e:issues.append({'input':op['source'],'reason':str(e)})
            continue
        invalid=False
        for field,expr in op['rule'].items():
            nums=expr.get('bounds',[]) if expr['op']=='range' else [expr['value']] if expr['op']=='set' else []
            if any(n < (1 if field=='base' else 0) for n in nums) or (len(nums)==2 and nums[0]>nums[1]):invalid=True
        bounds=op.get('collaboration_plus_range')
        if bounds and (len(bounds)!=2 or not 0<=bounds[0]<=bounds[1]):invalid=True
        if invalid:
            issues.append({'input':op['source'],'reason':'レベル・＋値の範囲の大小関係を確認してください。'});continue
        selector=copy.deepcopy(op.get('selector',{}))
        if op['kind']=='ownership' and (excluded or removal_names):
            selector['exclude_names']=list(dict.fromkeys(selector.get('exclude_names',[])+excluded+removal_names))
        def resolve_names(queries):
            if not index:return [],list(queries)
            names=[];errors=[]
            for name in queries:
                exact=index.exact(name)
                if len(exact)!=1:errors.append(name)
                else:names.append('No.'+str(next(iter(exact))))
            return list(dict.fromkeys(names)),errors
        if 'names' in op:
            if not index:
                issues.append({'input':op['source'],'reason':'キャラ定義を読み込み後、依頼を送り直してください。'});continue
            names,errors=resolve_names(op['names'])
            if errors:
                issues.append({'input':op['source'],'reason':'正式名・形態名を一意に照合できません：'+'、'.join(errors)});continue
            selector={'names':names}
            if not names:continue
        if op.get('exclude_names'):
            excluded,errors=resolve_names(op['exclude_names'])
            if errors:
                issues.append({'input':op['source'],'reason':'本能から除外する指定キャラを照合できません：'+'、'.join(errors)});continue
            selector['exclude_names']=excluded
        if op['kind']=='talent' and metadata and doc.get('keep_unknown_talents'):
            from .conditional_engine import select_ids
            # Freeze the explicit omission in this review. A later definition
            # update must not turn an omitted character into a new edit target.
            try:
                target_ids=select_ids(selector,metadata,index,set())
                byid={int(c['id']):c for c in metadata['characters']}
                absent=[cid for cid in target_ids if not byid[cid].get('talents')]
                kept_talent_names.update(byid[cid]['names'][0] for cid in absent)
                if len(absent)==len(target_ids):continue
                selector['exclude_names']=list(dict.fromkeys(selector.get('exclude_names',[])+['No.'+str(cid) for cid in absent]))
            except ParseError as e:
                issues.append({'input':op['source'],'reason':'【定義確認】'+str(e),'category':'metadata'});continue
        elif op['kind']=='talent' and selector.get('names'):
            byid={int(c['id']):c for c in metadata['characters']}
            no_talents=[byid[int(n[3:])]['names'][0] for n in selector['names'] if not byid[int(n[3:])].get('talents')]
            if no_talents:issues.append({'input':op['source'],'reason':'【定義確認】本能の実装定義がない対象があります。除外してよいか確認してください：'+'、'.join(no_talents),'category':'metadata'})
        for field,expr in op['rule'].items():
            rule={'domain':op['kind'] if op['kind'] in ('ownership','talent','profile','story','helper_mix','event_all','aku_ex') else 'cat','field':field,'expr':copy.deepcopy(expr),
                  'selector':copy.deepcopy(selector),'label':op['source'],'strict_bounds':True}
            for key in ('chapters','composition','families'):
                if key in op:rule[key]=copy.deepcopy(op[key])
            if op['kind']=='ownership' and (doc['collab_cutoffs'] or doc['excluded_collab_names']):
                rule['exclusion_policy']={key:copy.deepcopy(doc[key]) for key in ('collab_cutoffs','excluded_collab_names')}
                rule['exclusion_policy']['expected_names']=excluded
            if op.get('removal'):rule['explicit_removal']=True
            if op.get('expected_exclusions'):rule['expected_exclusions']=op['expected_exclusions']
            plan['document_rules'].append(rule)
        if op.get('collaboration_plus_range'):
            from .characters import collaboration_ids
            chars=[c for c in metadata['characters'] if int(c['id']) in {int(n[3:]) for n in selector.get('names',[])}]
            try:
                collabs=collaboration_ids(metadata,chars)
            except ParseError as e:
                issues.append({'input':op['source']+'（コラボ例外）','reason':str(e)});continue
            if not any(int(c['id']) in collabs for c in chars):continue
            plan['document_rules'].append({'domain':'cat','field':'plus',
                'expr':{'op':'range','bounds':op['collaboration_plus_range']},
                'selector':{**selector,'collab':True},'label':op['source']+'（コラボ例外）','strict_bounds':True})
    plan['section']=''
    character_rules=[r for r in plan['document_rules'] if r['domain'] in ('cat','talent','ownership')]
    if metadata and character_rules:
        from .conditional_engine import select_ids,priority
        from .characters import collaboration_ids
        try:
            needs_collab=any(r['selector'].get('collab') is not None for r in character_rules)
            collabs=collaboration_ids(metadata,metadata['characters']) if needs_collab else set()
            for rule in character_rules:
                try:
                    rule['expected_ids']=select_ids(rule['selector'],metadata,index,collabs,allow_empty=rule['domain']=='ownership' and bool(rule['selector'].get('exclude_names')))
                    from .character_forms import freeze_form_rule
                    freeze_form_rule(rule,metadata)
                except ParseError as e:issues.append({'input':rule['label'],'reason':'【定義確認】'+str(e),'category':'metadata'})
            assigned=set()
            ownership=[r for r in plan['document_rules'] if r['domain']=='ownership']
            for r in sorted(ownership,key=lambda r:(priority(r['selector']),len(r['selector'])),reverse=True):
                ids=set(r.get('expected_ids',[]));count=len(ids-assigned);assigned.update(ids)
                r['population_count']=count;r['target_count']=count*r['expr']['value']//100
        except ParseError as e:issues.append({'input':'キャラの所属照合','reason':'【定義確認】'+str(e),'category':'metadata'})
    elif character_rules:
        issues.append({'input':'キャラの定義','reason':'【定義確認】キャラ定義を読み込んだ後、依頼を送り直してください。','category':'metadata'})
    plan['unresolved']=list({(x['input'],x['reason']):x for x in issues}.values())
    from .conditional_migration import display
    plan['labels']=[x for x in plan['labels'] if not x['key'].startswith('document_')]
    plan['labels'].extend({'key':'document_exclusion_'+str(i),'label':label} for i,label in enumerate(exclusion_labels))
    if exclusion_issues and any(op['kind']=='ownership' for op in doc['operations']):
        plan['labels'].append({'key':'document_exclusion_pending','label':'解放割合：コラボ除外対象を確定してから、対象数と所持目標数を計算します。'})
    for n,r in enumerate(plan['document_rules']):
        sel=r['selector'];target=[]
        if sel.get('names'):target=[index.characters[int(name[3:])]['name']+'（'+name+'）' for name in sel['names'] if int(name[3:]) in r.get('expected_ids',[int(x[3:]) for x in sel['names']])]
        if sel.get('rarity') is not None:target.append(next(k for k,v in RARITIES.items() if v==sel['rarity']))
        if r['domain']=='cat' and r['field']=='unlocked' and sel=={'rarity':0}:
            ids=r.get('expected_ids',[])
            target.append('全'+str(len(ids))+'体：'+'・'.join(index.characters[cid]['name']+'（No.'+str(cid)+'）' for cid in ids))
        if sel.get('collab'):target.append('コラボのみ')
        if sel.get('regular_gacha'):target.append('通常ガチャの所属定義にあるキャラ')
        if sel.get('exclude_names'):target.append('除外指定・本能定義なしを除く' if doc.get('keep_unknown_talents') else '個別指定キャラを除く')
        quota=f' / 対象{r["population_count"]}体・所持目標{r["target_count"]}体（現所持が超過なら削除せず停止）' if 'population_count' in r else ''
        plan['labels'].append({'key':'document_'+str(n),'label':display(r)+' / 対象：'+'・'.join(target)+quota})
    if kept_talent_names:
        plan['labels'].append({'key':'document_kept_talents','label':f'本能定義がない{len(kept_talent_names)}体：本能のみ現在のまま保持（'+'・'.join(sorted(kept_talent_names))+'）'})
    plan['_document_base']=base
    if replaced_conditional:
        plan.setdefault('inferences',[]).append({'input':'以前の条件付き下書き','suggested':'今回の注文書で置き換え。以前の数量・レベル・対象は引き継ぎません。','method':'注文書の再読み取り'})
    plan['document_summary']=doc['summary']
    plan['blockers']=list(plan.get('menu_pending',{}).values())+[f'未解釈項目{i+1}：{x["input"]} — {x["reason"]}' for i,x in enumerate(plan['unresolved'])]
    plan['missing']=[x for x in plan['missing'] if x!='代行内容']
    if not has_edit_actions(plan):plan['missing'].append('代行内容')
    plan['ready']=not plan['blockers'] and not plan['missing']
    plan['_review_required']=True;plan.pop('_review_confirmed',None)
    return plan
