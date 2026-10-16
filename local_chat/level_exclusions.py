"""Named exclusions preserve base and plus levels, independent of other services."""
import re
from .parser import normalize,ParseError
from .named_characters import NameIndex,name_key

def exclusion_sentence(text):
    s=normalize(text).rstrip('。!！')
    if s=='レベル除外なし':return s
    header=re.fullmatch(r'(?:変更対象から除外|レベル変更から除外|レベル除外)\s*[:：=]?\s*(.*)',s)
    if header:return 'レベル除外='+header[1] if header[1] else '【レベル変更から除外】'
    unchanged=re.fullmatch(r'(.+?)(?:だけ|のみ)(?:は|を)?(?:そのまま|変更しない(?:で)?)(?:でお願い(?:します|致します|いたします)?|お願い(?:します)?|ください)?(?:追加失礼します)?',s)
    if unchanged:return 'レベル除外='+unchanged[1]
    return None

def resolve_level_exclusions(plan,metadata=None):
    plan['inferences']=[item for item in plan.get('inferences',[]) if not item.get('method','').startswith('レベル除外：')]
    queries=plan.get('level_exclusion_queries',[])
    if not queries:return plan
    if callable(metadata):metadata=metadata()
    if metadata is None:
        plan['menu_pending']['level_exclusions']='除外するキャラ名を照合する必要があります。エンジン起動後に送り直してください。'
        return plan
    matches,issues=NameIndex(metadata).resolve_all(queries)
    for issue in issues:
        issue['reason']=issue['reason'].replace('指定キャラ「','除外対象「').replace('「指定キャラ解放をNo.番号に変更して」で対象を指定し直せます。','「レベル除外なし」で除外一覧を取り消し、「レベル除外=No.番号」で必要な全対象を指定し直してください。')
    count=plan.get('level_exclusion_expected_count')
    if count is not None and len(matches)!=count:issues.append({'reason':f'除外する上記{count}体と、照合した{len(matches)}体が一致しません。対象名と体数を確認してください。'})
    plan['level_exclusion_resolved']=matches;plan['level_exclusion_issues']=issues
    plan['labels'][:]=[x for x in plan['labels'] if not x['key'].startswith('level_exclusion')]
    for match in matches:
        text=f'レベル変更から除外：{match["name"]}（No.{match["id"]}）・基本Lvと＋値を現在のまま保持'
        if name_key(match['query'])!=name_key(match['name']):text+='・入力「'+match['query']+'」→照合名「'+match['matched_name']+'」'
        if '候補' in match['method']:
            text+='【推測候補・対象を確認してください】'
            plan['inferences'].append({'input':match['query'],'suggested':text,'method':'レベル除外：'+match['method']})
        plan['labels'].append({'key':'level_exclusion_'+str(match['id']),'label':text})
    if issues:
        reason='\n'.join(issue['reason'] for issue in issues)
        plan['menu_pending']['level_exclusions']=reason
        plan['labels'].append({'key':'level_exclusions_pending','label':reason})
    else:plan['menu_pending'].pop('level_exclusions',None)
    return plan

def reviewed_level_exclusions(metadata,plan):
    matches,issues=NameIndex(metadata).resolve_all(plan['level_exclusion_queries'])
    identity=lambda values:{(value['id'],name_key(value['name'])) for value in values}
    if issues or plan.get('level_exclusion_issues') or identity(matches)!=identity(plan.get('level_exclusion_resolved',[])):
        raise ParseError('除外するキャラが確認内容と一致しません。最新の対象を確認し直してください。引き継ぎ取得前に停止しました。')
    return matches
