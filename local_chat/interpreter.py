"""Local intent suggestions; every proposed edit still needs explicit customer review."""
import copy
from difflib import SequenceMatcher
import re
from .parser import parse_request,ParseError,normalize,split_clauses,RESOURCES,has_edit_actions
from .menu_input import parse_menu_line, inline_collab_annotation
from .language import suggest_phrase
from .unlock_input import unlock_sentence
from .named_characters import resolve_unlocks
from .level_exclusions import exclusion_sentence,resolve_level_exclusions

FIELDS = ['猫缶','xp','np','通常チケット','レアチケ','プラチケ','レジェチケ',
          'リーダーシップ','バトルアイテム','キャッツアイ','ネコビタン',
          '城素材','マタタビ','本能玉','施設','オトート','神社','ガマトト']
HEADINGS = {'アイテム類':'アイテム','チケット':'チケット類','キャラクター':'キャラ類',
            'キャラ開放':'キャラ類','キャラ解放':'キャラ類','レベル':'レベル変更',
            'その他メニュー':'その他','ガマトト＆オトート':'ガマトト'}


def prepare_menu_text(text):
    rows, output = [], []
    for piece in re.split(r'([【《][^】》]+[】》])', text):
        if not piece.strip(): continue
        if piece[0] in '【《':
            output.append(piece)
            continue
        for line in piece.splitlines():
            if not line.strip(): continue
            exclusion=exclusion_sentence(line)
            if exclusion:
                output.append(exclusion);continue
            translated = inline_collab_annotation(line)
            if translated:
                output.append(translated)
                continue
            menu = parse_menu_line(line)
            if menu:
                rows.append(menu); output.append(menu['command'])
                continue
            unlock=unlock_sentence(line)
            if unlock:
                output.append(unlock)
                continue
            # Only separate multiple sales rows if all clauses are grounded.
            # Otherwise keep shared quantities and limitations intact.
            parts = split_clauses(line)
            menus = [parse_menu_line(part) for part in parts]
            if len(parts) > 1 and all(menus):
                for item in menus:
                    rows.append(item); output.append(item['command'])
            else:
                output.append(line)
    return '\n'.join(output), rows


def resolve_named_pending(plan, row):
    if not any(number in (44,45,46) for number in row['numbers']): return
    key='name:未来編採点スコア指定'
    plan.get('menu_pending',{}).pop(key,None)
    plan['labels']=[item for item in plan.get('labels',[]) if item['key']!='menu_'+key]
    for quote in plan.get('quoted_prices',[]):
        if quote['key']==key and len(row['numbers'])==1:
            quote.update(key='menus:'+str(row['numbers'][0]),numbers=row['numbers'],
                         candidates=[],name=row['name'])
    if len(row['numbers'])!=1:
        plan['quoted_prices']=[quote for quote in plan.get('quoted_prices',[]) if quote['key']!=key]


def infer_clause(text):
    """Preserve quantities; infer only bounded grammar and one clear field-name match."""
    s=normalize(text)
    if re.match(r'(?:引き継ぎ|引継ぎ|transfer|認証|auth)',s):return None
    suggestion=suggest_phrase(text)
    if suggestion:return suggestion
    original=s
    typo = re.fullmatch(r'(伝説(?:レア)?(?:も|は)?(?:プラス(?:値)?|\+(?:値)?))([0-9]+)z', s)
    if typo: return typo[1] + typo[2]
    s=re.sub(r'(?:マックス|カンスト|最大|上限)','max',s)
    s=s.replace('開放','解放')
    s=re.sub(r'[\U0001f300-\U0001faff\ufe0f\u200d]','',s).strip()
    # Common particle and polite-tail variants, only adjacent to a known field.
    names='|'.join('(?:'+pattern+')' for pattern,*_ in RESOURCES)
    s=re.sub(r'('+names+r'|施設|キャラ|オトート|神社|ガマトト)(?:は|も|を)\s*',r'\1',s)
    s=re.sub(r'(?:にして(?:ほしい|ください)?|にしたい|でお願いします|お願いします|おねがい|ください|にしてもらいたい)$','',s).strip()
    if re.fullmatch(r'(?:全キャラ|キャラ)(?:全部|全て)?(?:が)?(?:欲しい|ほしい)',s):s='キャラ全解放'
    if re.fullmatch(r'(?:全キャラ|キャラ)(?:全部|全て)?(?:強くして|強くしたい)',s):s='キャラMAX'
    level=re.fullmatch(r'(?:全キャラ|キャラ)(?:全部|全て)?(?:レベル|lv\.?)?(\d+(?:\+(?:\d+(?:[~〜-]\d+)?|max|なし))?)',s)
    if level:s='メニュー29='+level[1]
    only=re.fullmatch(r'(?:レアチケ|レアチケット)(?:だけ|のみ)\s*(max|\d+)?',s)
    if only:s='【チケット類】レアチケ'+(only[1] or 'MAX')+'、その他不要'
    no=re.fullmatch(r'('+names+r')(?:は)?(?:いらない|要らない|変更しない)',s)
    if no:s=no[1]+'不要'
    # More complex limitations and negations require a question, not fuzzy guessing.
    if re.search(r'以外|除く|だけ|のみ|しない|じゃない|ない|(?<!\d)-\s*\d',s):
        if s!=original:
            try:parse_request(s);return s
            except ParseError:pass
        return None
    if s!=original:return s
    match=re.fullmatch(r'([^\d+]+?)(max|\d+(?:万\d*)?)?',s)
    if not match:return None
    field,quantity=match[1].strip(),match[2] or ''
    scores=sorted([(SequenceMatcher(None,field,target).ratio(),target) for target in FIELDS],reverse=True)
    score,target=scores[0]
    if score>=.82 and score-scores[1][0]>=.10 and field!=target:
        return target+quantity
    return None


def _interpret_request_existing(text, previous=None, character_metadata=None):
    if not isinstance(text,str) or not text.strip() or len(text)>16000:
        raise ParseError('依頼を1〜16000文字で入力してください。')
    from .conditional_language import wants_conditional, interpret_conditional
    from .advanced_requests import is_extended
    if (wants_conditional(text, previous) or is_extended(text)) and not re.fullmatch(r'未解釈項目\d+(?:不要|\s*=.*)', text, re.DOTALL):
        metadata=character_metadata() if callable(character_metadata) else character_metadata
        return interpret_conditional(text, previous, metadata)
    text=re.sub(r'(?m)^\s*コラボ(?:を)?含め(?:て)?全キャラ(?:を)?(?:解放|開放)(?:してください)?\s*$', 'キャラ全解放', text)
    original=copy.deepcopy(previous or {})
    unresolved=copy.deepcopy(original.get('unresolved',[]))
    inferences=copy.deepcopy(original.get('inferences',[]))
    # Explicitly remove an unclear item; never silently ignore one.
    remove=re.fullmatch(r'未解釈項目(\d+)不要',normalize(text))
    correction=re.fullmatch(r'未解釈項目(\d+)\s*=\s*(.+)',text.strip(),re.DOTALL)
    if correction:
        index=int(correction[1])-1
        if not 0<=index<len(unresolved):raise ParseError('未解釈項目の番号を確認してください。')
        old=unresolved.pop(index)
        original['unresolved']=unresolved
        plan=interpret_request(correction[2],original,character_metadata)
        plan['inferences'].append({'input':old['input'],'suggested':correction[2],'method':'お客さんの訂正'})
        return plan
    if remove:
        index=int(remove[1])-1
        if not 0<=index<len(unresolved):raise ParseError('未解釈項目の番号を確認してください。')
        unresolved.pop(index)
        plan=parse_request('お願いします',original)
    else:
        text, menu_rows = prepare_menu_text(text)
        for row in menu_rows:
            if row['price_jpy'] is None and row['input'] != row['command']:
                inferences.append({'input':row['input'],'suggested':row['command'],
                                   'method':'メニュー名称・括弧内の指定を整理'})
        # A scoped or numbered choice resolves a previous ambiguous menu name.
        for row in menu_rows:
            resolve_named_pending(original,row)
        try:
            plan=parse_request(text,original)
        except ParseError as error:
            # Invalid credentials and invalid numeric values do not become suggestions.
            if not str(error).startswith(('読み取れない指定','未対応の見出し','レベルの補足')):
                raise
            plan=copy.deepcopy(original)
            pieces=re.split(r'([【《][^】》]+[】》])',text)
            for piece in pieces:
                if not piece.strip():continue
                if piece[0] in '【《':
                    heading=normalize(piece[1:-1]);replacement=HEADINGS.get(heading)
                    candidate='【'+replacement+'】' if replacement else piece
                    try:
                        plan=parse_request(candidate,plan)
                        if candidate!=piece:inferences.append({'input':piece,'suggested':candidate,'method':'見出しの表現'})
                    except ParseError:
                        unresolved.append({'input':piece,'reason':'この見出しの対象を判断できません。対応する見出しで指定してください。'})
                    continue
                for line in piece.splitlines():
                    line=line.strip()
                    if not line:continue
                    try:
                        plan=parse_request(line,plan);continue
                    except ParseError as line_error:
                        if not str(line_error).startswith(('読み取れない指定','未対応の見出し','レベルの補足')):raise
                    # Try the full sentence before splitting shared-quantity clauses.
                    candidate=infer_clause(line)
                    if candidate:
                        try:
                            plan=parse_request(candidate,plan)
                            inferences.append({'input':line,'suggested':candidate,'method':'表現から推測'});continue
                        except ParseError as suggestion_error:
                            if not str(suggestion_error).startswith(('読み取れない指定','未対応の見出し','レベルの補足')):raise
                    try:parts=split_clauses(line)
                    except ParseError:parts=[line]
                    for part in parts:
                        try:
                            plan=parse_request(part,plan);continue
                        except ParseError as part_error:
                            if not str(part_error).startswith(('読み取れない指定','未対応の見出し','レベルの補足')):raise
                        candidate=infer_clause(part)
                        if candidate:
                            try:
                                plan=parse_request(candidate,plan)
                                inferences.append({'input':part,'suggested':candidate,'method':'項目名・表現から推測'});continue
                            except ParseError as suggestion_error:
                                if not str(suggestion_error).startswith(('読み取れない指定','未対応の見出し','レベルの補足')):raise
                        unresolved.append({'input':part,'reason':'数量・対象・操作を特定できません。具体的な項目名またはメニュー番号で指定してください。'})
            if not plan.get('selected') and 'selected' not in plan:
                plan=parse_request('お願いします',original)
        for row in menu_rows:
            resolve_named_pending(plan,row)
            if row.get('pending_key'):
                key=row['pending_key']; reason=row['pending_reason']
                plan['menu_pending'][key]=reason
                plan['labels'][:]=[item for item in plan['labels'] if item['key']!='menu_'+key]
                plan['labels'].append({'key':'menu_'+key,'label':reason})
            active_row = not row['numbers'] or all(number in plan['menu_numbers'] for number in row['numbers'])
            if row['price_jpy'] is not None and active_row:
                key=row.get('pending_key') or 'menus:' + ','.join(map(str,row['numbers']))
                plan['quoted_prices'][:]=[item for item in plan.get('quoted_prices',[]) if item['key']!=key]
                plan['quoted_prices'].append({'key':key,'numbers':row['numbers'],'candidates':row['candidates'],
                                             'name':row['name'],'price_jpy':row['price_jpy'],'input':row['input']})
    # Preserve unresolved limitations across messages, even when later input parses.
    unique=[]
    for item in unresolved:
        if item not in unique:unique.append(item)
    plan['unresolved']=unique
    plan['inferences']=inferences[-80:]
    metadata=character_metadata() if callable(character_metadata) and (plan.get('unlock_queries') or plan.get('level_exclusion_queries')) else character_metadata
    resolve_unlocks(plan,metadata)
    resolve_level_exclusions(plan,metadata)
    if not has_edit_actions(plan) and not plan.get('menu_pending') and '代行内容' not in plan['missing']:plan['missing'].append('代行内容')
    base_blockers=list(plan.get('menu_pending',{}).values())
    plan['blockers']=base_blockers+[f'未解釈項目{i+1}：{item["input"]} — {item["reason"]}' for i,item in enumerate(unique)]
    if plan.get('menu_pending'):
        plan['missing']=[item for item in plan.get('missing',[]) if item!='代行内容']
    plan['ready']=not plan.get('missing') and not plan['blockers']
    plan['_review_required']=True
    plan.pop('_review_confirmed',None)
    return plan


def interpret_request(text, previous=None, character_metadata=None):
    if not isinstance(text, str):
        raise ParseError('依頼を1〜16000文字で入力してください。')
    from .japanese import rewrite, suggestions
    rewritten, changes = rewrite(text)
    plan = _interpret_request_existing(rewritten, previous, character_metadata)
    known = {(x.get('input'), x.get('suggested')) for x in plan.get('inferences', [])}
    for change in changes:
        if (change['input'], change['suggested']) not in known:
            plan.setdefault('inferences', []).append(change)
    for issue in plan.get('unresolved', []):
        issue['suggestions'] = suggestions(issue.get('input', ''))
    return plan
