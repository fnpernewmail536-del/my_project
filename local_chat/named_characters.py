"""Resolve names against engine metadata; approximate matches always need review."""
from difflib import SequenceMatcher
import re
import unicodedata
from .parser import ParseError

SEPARATOR=re.compile(r'\s*(?:、|,|/|&|;|\n|および|及び|と|・|\s+)\s*')
MODIFIER=re.compile(r'以外|除く|だけ|のみ|不要|しない|変更|レベル|プラス|本能|max|解放|開放|[+＋=]|\d+円')
ID_PATTERN=r'(?:(?:no\.?|id|キャラid|キャラ番号|#)\s*[:=]?\s*)?(\d+)'
POLITE_SUFFIX=r'(?:を|の)?(?:お願いします|お願い|希望(?:です)?|ください)$'
NAME_INFERENCES={'略称からの候補（要確認）','似た名前からの候補（要確認）'}

def clean_name(text):
    value=unicodedata.normalize('NFKC',text).casefold().strip()
    wrappers={'「':'」','『':'』','"':'"',"'":"'",'(':")"}
    while len(value)>1 and wrappers.get(value[0])==value[-1]:
        opening,closing=value[0],value[-1]
        if opening==closing:
            if value.count(opening)!=2:break
        else:
            depth=0;end=None
            for pos,char in enumerate(value):
                if char==opening:depth+=1
                elif char==closing:
                    depth-=1
                    if depth==0:end=pos;break
            if end!=len(value)-1:break
        value=value[1:-1].strip()
    return value

def name_key(text):
    value=re.sub(r'\s+','',clean_name(text))
    return ''.join(chr(ord(c)-0x60) if '\u30a1'<=c<='\u30f6' else c for c in value)

class NameIndex:
    def __init__(self,metadata):
        if not isinstance(metadata,dict) or not isinstance(metadata.get('characters'),list):raise ParseError('キャラ名の定義を読み込めません。起動状態を確認して送り直してください。')
        self.characters={};self.names={};self.blocked_names=set()
        for info in metadata.get('characters',[]):
            if not isinstance(info,dict) or not isinstance(info.get('selectable',True),bool):raise ParseError('キャラ名の対象定義が不正です。')
            if not info.get('selectable',True):
                self.blocked_names.update(name_key(n) for n in info.get('names',[]) if isinstance(n,str))
                continue
            try:cid=int(info['id'])
            except (KeyError,TypeError,ValueError) as exc:raise ParseError('キャラ名の番号定義を読み取れません。') from exc
            if cid<1 or not str(info['id']).isdigit() or cid in self.characters:raise ParseError('キャラ名の番号定義が不正・重複しています。')
            names=info.get('names') or ([info['name']] if info.get('name') else [])
            if not isinstance(names,list):raise ParseError('キャラ名の一覧定義が不正です。')
            names=[n for n in names if isinstance(n,str) and n.strip()]
            aliases=info.get('aliases',[])
            if isinstance(aliases,list):names += [n for n in aliases if isinstance(n,str) and n.strip()]
            if not names:continue
            self.characters[cid]={'id':cid,'name':names[0],'names':names}
            for name in names:self.names.setdefault(name_key(name),{})[cid]=name
        if not self.characters:raise ParseError('キャラ名の定義がありません。エンジンを読み込んでから送り直してください。')
        # Ground short collaboration titles in the engine's membership list.
        title_aliases={'メルクストーリア':['メルスト']}
        for group in metadata.get('groups',[]):
            if group.get('kind')!='collab' or not isinstance(group.get('name'),str):continue
            title=re.sub(r'[「」『』]|コラボガチャ|コラボ','',group['name']).strip()
            titles=[title]+title_aliases.get(title,[])
            for raw_id in group.get('cat_ids',[]):
                if not str(raw_id).isdigit():raise ParseError('コラボ所属のキャラ番号定義が不正です。')
                cid=int(raw_id)
                if cid not in self.characters:continue
                for name in self.characters[cid]['names']:
                    for alias in titles:self.names.setdefault(name_key(alias+'の'+name),{})[cid]=name

    def exact(self,query):
        value=clean_name(query)
        literal=self.names.get(name_key(value),{})
        if literal:return literal
        value=re.sub(POLITE_SUFFIX,'',value).strip()
        numbered=re.fullmatch(ID_PATTERN,value)
        if numbered:
            cid=int(numbered[1]);return {cid:self.characters[cid]['name']} if cid in self.characters else {}
        return self.names.get(name_key(value),{})

    def fragments(self,text):
        """Prefer a whole catalogue name to separators occurring inside that name."""
        text=clean_name(text)
        if self.exact(text) or name_key(text) in self.blocked_names:return [text]
        cuts=list(SEPARATOR.finditer(text))
        for cut in reversed(cuts):
            head=text[:cut.start()].strip()
            if head and self.exact(head):return [head]+self.fragments(text[cut.end():])
        if cuts:
            first=cuts[0]
            return [text[:first.start()].strip()]+self.fragments(text[first.end():])
        return [text]

    def resolve(self,query):
        exact=self.exact(query)
        if len(exact)==1:
            cid,matched=next(iter(exact.items()))
            return self.match(query,cid,matched,'正式名・形態名・番号'),None
        if exact:return None,self.issue(query,'同じ名前のキャラが複数あります。',exact)
        if name_key(query) in self.blocked_names:return None,self.issue(query,'このキャラは解放可能な対象から除外されています。',{})
        if re.fullmatch(ID_PATTERN,re.sub(POLITE_SUFFIX,'',clean_name(query)).strip()):
            return None,self.issue(query,'そのNo.番号は解放できるキャラ定義にありません。',{})
        key=name_key(query)
        if key in {'全キャラ','基本','ex','レア','激レア','超激レア','伝説','伝説レア','コラボ','その他'}:
            return None,self.issue(query,'個別キャラの正式名またはNo.番号で指定してください。',{})
        if not key or MODIFIER.search(key) or len(key)>100 or re.fullmatch(r'[\d\W]+',key):
            return None,self.issue(query,'対象名だけを正式名またはNo.番号で指定してください。',{})
        # A short substring is a proposal only when it identifies one unit.
        contains={cid:matched for name,ids in self.names.items() if len(key)>=2 and key in name for cid,matched in ids.items()}
        if len(contains)==1:
            cid,matched=next(iter(contains.items()))
            return self.match(query,cid,matched,'略称からの候補（要確認）'),None
        if contains:return None,self.issue(query,'略称に合うキャラが複数あります。',contains)
        scores={}
        if len(key)>=4:
            for name,ids in self.names.items():
                if abs(len(name)-len(key))>max(3,len(key)//2):continue
                score=SequenceMatcher(None,key,name).ratio()
                for cid,matched in ids.items():
                    if score>scores.get(cid,(0,''))[0]:scores[cid]=(score,matched)
        ranked=sorted(scores.items(),key=lambda item:(-item[1][0],item[0]))
        if ranked and ranked[0][1][0]>=.8 and (len(ranked)==1 or ranked[0][1][0]-ranked[1][1][0]>=.15):
            cid,(_,matched)=ranked[0]
            return self.match(query,cid,matched,'似た名前からの候補（要確認）'),None
        candidates={cid:matched for cid,(score,matched) in ranked[:5] if score>=.55}
        return None,self.issue(query,'キャラを一意に照合できません。正式名またはNo.番号を指定してください。',candidates)

    def match(self,query,cid,matched,method):
        return {'query':query,'id':cid,'name':self.characters[cid]['name'],'matched_name':matched,'method':method}

    def issue(self,query,reason,candidates):
        choices=[{'id':cid,'name':self.characters[cid]['name'],'matched_name':matched} for cid,matched in list(candidates.items())[:8]]
        text='指定キャラ「'+query+'」：'+reason
        if choices:text+=' 候補：'+' / '.join(f'{c["name"]}（No.{c["id"]}）' for c in choices)
        text+=' 「指定キャラ解放をNo.番号に変更して」で対象を指定し直せます。'
        return {'query':query,'reason':text,'candidates':choices}

    def resolve_all(self,queries):
        matches=[];issues=[];seen=set()
        for query in queries:
            for fragment in self.fragments(query):
                if not fragment:
                    issues.append(self.issue(query,'名前の区切りを確認してください。',{}));continue
                match,issue=self.resolve(fragment)
                if issue:issues.append(issue)
                elif match['id'] not in seen:matches.append(match);seen.add(match['id'])
        return matches,issues

def resolve_unlocks(plan,metadata=None):
    plan['inferences']=[item for item in plan.get('inferences',[]) if item.get('method') not in NAME_INFERENCES]
    queries=plan.get('unlock_queries',[])
    if not queries:return plan
    if callable(metadata):metadata=metadata()
    if metadata is None:
        if not plan.get('unlock_resolved'):
            plan['menu_pending']['27']='指定キャラ解放：キャラ名の定義を読み込んで照合する必要があります。エンジン起動後に送り直してください。'
        else:
            for match in plan['unlock_resolved']:
                if match['method'] in NAME_INFERENCES:plan['inferences'].append({'input':match['query'],'suggested':f'指定キャラ解放：{match["name"]}（No.{match["id"]}）','method':match['method']})
        return plan
    index=NameIndex(metadata)
    matches,issues=index.resolve_all(queries)
    plan['unlock_resolved']=matches;plan['unlock_issues']=issues
    plan['labels'][:]=[x for x in plan['labels'] if x['key']!='menu_27' and not x['key'].startswith('unlock_named_')]
    for match in matches:
        text=f'指定キャラ解放：{match["name"]}（No.{match["id"]}）'
        if name_key(match['query'])!=name_key(match['name']):text+='・入力「'+match['query']+'」→照合名「'+match['matched_name']+'」'
        if '候補' in match['method']:text+='【推測候補・対象を確認してください】'
        plan['labels'].append({'key':'unlock_named_'+str(match['id']),'label':text})
        inference={'input':match['query'],'suggested':f'指定キャラ解放：{match["name"]}（No.{match["id"]}）','method':match['method']}
        if '候補' in match['method'] and inference not in plan.setdefault('inferences',[]):plan['inferences'].append(inference)
    if issues:
        plan['menu_pending']['27']='\n'.join(issue['reason'] for issue in issues)
        plan['labels'].append({'key':'menu_27','label':plan['menu_pending']['27']})
    else:plan['menu_pending'].pop('27',None)
    plan['inferences']=plan['inferences'][-80:]
    return plan

def reviewed_unlocks(metadata,plan):
    matches,issues=NameIndex(metadata).resolve_all(plan['unlock_queries'])
    reviewed=plan.get('unlock_resolved',[])
    identity=lambda values:{(value['id'],name_key(value['name'])) for value in values}
    if issues or not reviewed or plan.get('unlock_issues') or identity(matches)!=identity(reviewed):
        raise ParseError('指定キャラの名前・番号が確認内容と一致しません。最新の対象を確認し直してください。引き継ぎ取得前に停止しました。')
    return matches
