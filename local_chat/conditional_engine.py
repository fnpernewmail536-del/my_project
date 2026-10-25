"""Bounded declarative edits evaluated once against the pre-edit save.
No user expressions are executed. Unresolved metadata fails before transfer.
"""
import copy
import secrets
from decimal import Decimal, ROUND_FLOOR
from .parser import ParseError
from .named_characters import NameIndex, name_key
from .characters import collaboration_ids
from .menus import MAX_ITEM_AMOUNTS
from .range_input import roll

def plain(obj):
    if hasattr(obj,'serialize'):return plain(obj.serialize())
    if isinstance(obj,dict):return {k:plain(v) for k,v in obj.items()}
    if isinstance(obj,(list,tuple)):return [plain(v) for v in obj]
    if isinstance(obj,(str,int,float,bool)) or obj is None:return obj
    if hasattr(obj,'__dict__'):return {k:plain(v) for k,v in vars(obj).items() if not callable(v)}
    raise ParseError('保持対象の検証用データを読み取れません。')

SCALARS={k:k for k in ('catfood','xp','np','normal_tickets','rare_tickets','platinum_tickets','legend_tickets','leadership','platinum_shards')}
CAPS={**MAX_ITEM_AMOUNTS,'catfruit':998,'orbs':998,'clear':2147483647,'treasure':3,'platinum_shards':999}

def value(expr,old,cap,no_decrease=False):
    op=expr['op']
    if op=='keep':return old
    if op=='max':v=cap
    elif op=='set':v=int(expr['value'])
    elif op=='range':v=roll(expr['bounds'])
    elif op=='add':v=old+int(expr['value'])
    elif op=='mul':v=int((Decimal(str(old))*Decimal(str(expr['value']))).to_integral_value(rounding=ROUND_FLOOR))
    elif op=='percent':v=cap*int(expr['value'])//100
    elif op=='atleast':v=max(old,int(expr['value']))
    else:raise ParseError('未知の計算方法です。')
    v=max(0,min(cap,v))
    return max(old,v) if no_decrease else v

def predicate(conditions,original,field):
    for c in conditions or []:
        x=original.get(c.get('field',field))
        if x is None:raise ParseError('条件判定に必要な保存値がありません。')
        n=c.get('value');op=c['op']
        valid={'lt':lambda:x<n,'le':lambda:x<=n,'ge':lambda:x>=n,'gt':lambda:x>n,'eq':lambda:x==n,'ne':lambda:x!=n,'odd':lambda:x%2==1,'even':lambda:x%2==0}[op]()
        if not valid:return False
    return True

def select_ids(selector,metadata,index,collabs,allow_empty=False):
    chars={int(c['id']):c for c in metadata.get('characters',[]) if c.get('selectable',True)}
    ids=set(chars)
    if 'names' in selector:
        ids=set()
        for query in selector['names']:
            exact=index.exact(query)
            if len(exact)!=1:raise ParseError('キャラを一意に特定できません：'+query+'。正式名またはNo.番号を指定してください。')
            ids.add(next(iter(exact)))
    for query in selector.get('exclude_names',[]):
        exact=index.exact(query)
        if len(exact)!=1:raise ParseError('除外キャラを一意に特定できません：'+query)
        ids.difference_update(exact)
    if selector.get('rarity') is not None:ids={i for i in ids if int(chars[i]['rarity'])==selector['rarity']}
    if selector.get('collab') is not None:ids={i for i in ids if (i in collabs)==selector['collab']}
    if selector.get('regular_gacha'):
        ordinary=set(int(cid) for group in metadata.get('groups',[]) if group.get('kind')=='gacha' for cid in group.get('cat_ids',[]))
        if not ordinary:raise ParseError('通常ガチャの所属定義がありません。')
        ids &= ordinary
        ids={cid for cid in ids if chars[cid].get('collab') is not True}
    if selector.get('forms_min'):
        ids={i for i in ids if int(chars[i].get('form_count',1))>=selector['forms_min']}
    if selector.get('group'):
        query=selector['group'];matches=[g for g in metadata.get('groups',[]) if g.get('kind')=='collab' and name_key(query) in name_key(g.get('name',''))]
        if not matches:raise ParseError('コラボ名の所属定義がありません：'+query)
        ids&=set(int(i) for g in matches for i in g['cat_ids'])&collabs
    if selector.get('series'):
        series=selector['series'];terms={'ちび':['ちび'],'大狂乱':['大狂乱'],'特急':['ネコ特急','ネコ超特急','ネコエクスプレス']}[series]
        ids={i for i in ids if any(any(t in n for t in terms) for n in chars[i].get('names',[]))}
    if not ids and not allow_empty:raise ParseError('対象キャラの定義がありません：'+str(selector))
    return sorted(ids)

def priority(selector):
    return 3 if selector.get('names') else 2 if selector.get('group') or selector.get('series') or selector.get('collab') is not None else 1 if selector.get('rarity') is not None else 0

def compile_conditional(module,plan):
    if plan.get('_pending_document_ownership'):
        raise ParseError('解放割合の除外対象が未確定です。条件を修正した注文書を送り直して、対象数を確認してください。')
    rules=copy.deepcopy(plan.get('conditional_rules',[]))
    if len(rules)>2000:raise ParseError('条件指定が多すぎます。分割してください。')
    for r in rules:
        if r.get('expr',{}).get('op') not in ('keep','max','set','range','add','mul','atleast','percent'):raise ParseError('条件式の操作が不正です。')
        if r['expr']['op']=='range' and not 0<=r['expr']['bounds'][0]<=r['expr']['bounds'][1]<=2147483647:raise ParseError('条件式の範囲が不正です。')
    result={'rules':rules,'no_decrease':plan.get('conditional_no_decrease',False),'cat_no_decrease':plan.get('conditional_cat_no_decrease',False)}
    char_rules=[r for r in rules if r['domain'] in ('cat','talent','ownership')]
    if char_rules:
        metadata=module.get_character_metadata();index=NameIndex(metadata)
        need_collab=any(r.get('selector',{}).get('collab') is not None or r.get('selector',{}).get('group') for r in char_rules)
        collabs=collaboration_ids(metadata,metadata['characters']) if need_collab else set()
        result['characters']={int(c['id']):c for c in metadata['characters'] if c.get('selectable',True)}
        levels=any(r['domain']=='cat' and r['field'] in ('base','plus') for r in rules)
        if levels:
            target_ids = set()
            for rule in char_rules:
                if rule['domain'] == 'cat' and rule['field'] in ('base', 'plus'):
                    target_ids.update(select_ids(rule.get('selector', {}), metadata, index, collabs))
            from .character_caps import get_level_caps
            result['caps']=get_level_caps(module,metadata,target_ids)
        for r in char_rules:
            if r.get('exclusion_policy'):
                from .targeted_requests import ownership_exclusions
                policy=r['exclusion_policy'];excluded,_,issues=ownership_exclusions(policy,metadata)
                if issues:raise ParseError(issues[0]['reason'])
                if excluded!=policy['expected_names']:raise ParseError('コラボの除外対象が変わりました。注文書を送り直して確認してください。')
            r['ids']=select_ids(r.get('selector',{}),metadata,index,collabs,allow_empty=r['domain']=='ownership' and bool(r.get('selector',{}).get('exclude_names')));r['priority']=priority(r.get('selector',{}))
            if r['expr']['op']=='percent' and not 0<=int(r['expr']['value'])<=100:
                raise ParseError('割合は0〜100%で指定してください。')
            expected=r.get('expected_ids')
            if expected is not None and expected!=r['ids']:raise ParseError('対象キャラの定義が変わりました。確認し直してください。')
            if r['domain']=='cat' and r['field']=='form':
                if r['expr']!={'op':'set','value':3} or r.get('selector',{}).get('forms_min')!=3:
                    raise ParseError('形態指定は対応キャラの第3形態への切替だけに対応しています。')
                if not callable(getattr(module,'character_third_form_state',None)):
                    raise ParseError('第3形態の実行接続がありません。修正版のmain13.pyへ更新してください。')
                from .character_forms import form_specs
                specs=form_specs(result['characters'],r['ids'])
                if r.get('expected_form_specs')!=specs:raise ParseError('第3形態・進化権の定義が変わりました。依頼を再送して確認してください。')
            if r['domain']=='talent' and r.get('exclude_speed'):
                for cid in r['ids']:
                    if any(not t.get('name') for t in result['characters'][cid].get('talents',[])):raise ParseError('移動速度本能の名前定義がありません。')
        # Narrow ownership exceptions take their own population; broad quotas
        # apply only to remaining units, without counting any unit twice.
        from .character_caps import strict_cap_issues
        cap_issues=strict_cap_issues(char_rules,result['characters'],result.get('caps',{}))
        if cap_issues:raise ParseError(f'キャラ上限を超える指定が{len(cap_issues)}箇所あります。値は変更していません。\n'+'\n'.join(cap_issues))
        assigned=set()
        ownership=[r for r in char_rules if r['domain']=='ownership']
        for r in sorted(ownership,key=lambda r:(r['priority'],len(r.get('selector',{}))),reverse=True):
            r['quota_ids']=sorted(set(r['ids'])-assigned);assigned.update(r['ids'])
    if any(r['domain'] in ('legend','event','items','orbs','facilities','action') for r in rules):result['legend_metadata']=module.get_legend_metadata()
    if any(r['domain']=='helpers' for r in rules):
        raw=module._latest_jp_game_data_getter().download('resLocal','GamatotoExpedition_Members_name_ja.csv')
        if raw is None:raise ParseError('隊員定義がありません。')
        rows=list(module.core.CSV(raw,module.core.Delimeter.from_country_code_res(module.core.CountryCode.from_code('jp')),remove_empty=False))
        result['legend_helpers']=[row[0].to_int() for row in rows[1:] if len(row)>4 and row[0].to_int()>=0 and 'レジェンド' in row[4].to_str()]
        if not result['legend_helpers']:raise ParseError('レジェンド隊員定義がありません。')
    for r in rules:
        if r['domain']=='items' and r.get('names'):
            names=result['legend_metadata'].get('vip_item_groups',{}).get(r['group']) or getattr(module,'VIP_ITEM_LABELS',{}).get(r['group'])
            if not names:raise ParseError('アイテム種類の名前定義がありません：'+r['group'])
            indices=[]
            for name in r['names']:
                matches=[i for i,n in enumerate(names) if name_key(name)==name_key(n)]
                if len(matches)!=1:raise ParseError('アイテム種類を一意に特定できません：'+name)
                indices.append(matches[0])
            r['indices']=indices
        if r['domain']=='event'  and r.get('family')!='aku':
            maps=result['legend_metadata'].get('event_stage_families',{}).get(r['family'],{}).get('maps',[])
            if r.get('map_name'):maps=[m for m in maps if r['map_name'] in m['name']]
            if not maps:raise ParseError('対象イベントのステージ定義がありません。')
            r['maps']=maps
    from .advanced_engine import compile_extended
    compile_extended(module,result)
    from .targeted_engine import compile_targets
    compile_targets(module,result)
    return result

def apply_conditional(module,save,compiled):
    """Calculate all final writes before mutating. Each path is written once."""
    pending={};originals={};checked=[]
    def stage_count(stage):return stage.clear_amount if hasattr(stage,'clear_amount') else stage.clear_times
    no_decrease=compiled['no_decrease']
    def put(key,old,getter,setter,rule,cap,context=None):
        old=int(old);context=context or {rule['field']:old}
        if not predicate(rule.get('when'),context,rule['field']):return
        rank=(rule.get('priority',0),rule['_order'])
        if key in pending and pending[key]['rank']>rank:return
        preserve_high=False if rule['domain']=='cat' and rule['field']=='form' else no_decrease or (compiled.get('cat_no_decrease',False) and rule['domain']=='cat') or rule.get('no_decrease',False)
        new=value(rule['expr'],old,cap,preserve_high)
        pending[key]={'old':old,'new':new,'get':getter,'set':setter,'rank':rank,'label':rule.get('label',key)}
    meta=compiled.get('legend_metadata',{})
    for order,r in enumerate(compiled['rules']):
        r={**r,'_order':order};domain=r['domain'];field=r['field'];expr=r['expr']
        if domain=='resource':
            attr=SCALARS[field];old=getattr(save,attr)
            put(field,old,lambda a=attr:getattr(save,a),lambda v,a=attr:setattr(save,a,v),r,CAPS[field])
        elif domain=='ownership':
            ids=r['quota_ids'];target=len(ids)*int(expr['value'])//100
            cats={cid:save.cats.get_cat_by_id(cid-1) for cid in ids}
            if any(c is None for c in cats.values()):raise ParseError('所持率の対象キャラがセーブにありません。')
            owned=[cid for cid,c in cats.items() if c.unlocked]
            if len(owned)>target:
                raise ParseError(f'現在の所持数{len(owned)}体が指定所持率の目標{target}体を超えています。キャラを削除せず停止しました。')
            missing=[cid for cid,c in cats.items() if not c.unlocked]
            additions=secrets.SystemRandom().sample(missing,target-len(owned))
            for cid in additions:
                cat=cats[cid]
                put(f'cat:{cid}:unlock',0,lambda c=cat:int(bool(c.unlocked)),lambda v,c=cat:c.unlock(save),{**r,'expr':{'op':'set','value':1}},1,{'unlocked':0})
        elif domain in ('cat','talent'):
            for cid in r['ids']:
                cat=save.cats.get_cat_by_id(cid-1)
                if cat is None:raise ParseError('セーブにキャラがありません：No.'+str(cid))
                old={'base':cat.upgrade.base+1,'plus':cat.upgrade.plus,'unlocked':int(bool(cat.unlocked))}
                if domain=='cat' and not predicate(r.get('when'),old,field):continue
                if domain=='talent':
                    info=compiled['characters'][cid]
                    for t in info.get('talents',[]):
                        if r.get('exclude_speed') and ('移動速度' in t.get('name','') or 'Movement Speed' in t.get('name','')):continue
                        def get(c=cat,tid=t['id']):return next((x.level for x in (c.talents or []) if x.id==tid),0)
                        put(f'cat:{cid}:talent:{t["id"]}',get(),get,lambda v,c=cat,tid=t['id']:module._set_cat_talent_level(c,tid,v),r,int(t['max_level']),{'talent':get(),'unlocked':old['unlocked']})
                    continue
                if field=='unlocked':
                    def setter(v,c=cat):
                        if v and not c.unlocked:c.unlock(save)
                        elif not v:c.remove(reset=False)
                    put(f'cat:{cid}:unlock',old['unlocked'],lambda c=cat:int(bool(c.unlocked)),setter,r,1,old)
                elif field in ('base','plus'):
                    # Existing locked units only receive levels if this order unlocks them.
                    getter=(lambda c=cat:c.upgrade.base+1) if field=='base' else (lambda c=cat:c.upgrade.plus)
                    setter=(lambda v,c=cat:setattr(c.upgrade,'base',v-1)) if field=='base' else (lambda v,c=cat:setattr(c.upgrade,'plus',v))
                    put(f'cat:{cid}:{field}',old[field],getter,setter,r,compiled['caps'][cid][field],old)
                elif field=='forms':
                    put(f'cat:{cid}:forms',cat.unlocked_forms,lambda c=cat:c.unlocked_forms,lambda v,c=cat:setattr(c,'unlocked_forms',v),r,min(3,int(compiled['characters'][cid].get('form_count',1))))
                elif field=='form':
                    fields=module.character_third_form_state(cat,compiled['characters'][cid])
                    for attr,new in fields.items():
                        put(f'cat:{cid}:{attr}',getattr(cat,attr),lambda c=cat,a=attr:getattr(c,a),
                            lambda v,c=cat,a=attr:setattr(c,a,v),{**r,'expr':{'op':'set','value':new},'no_decrease':False},3,old)
        elif domain=='items':
            group=r['group'];names=meta.get('vip_item_groups',{}).get(group) or getattr(module,'VIP_ITEM_LABELS',{}).get(group)
            if group=='battle_items':items=save.battle_items.items
            elif group=='base_materials':items=save.ototo.base_materials.materials
            else:items=getattr(save,group)
            target=r.get('names')
            if r.get('indices') is not None:ids=r['indices']
            elif target:
                if not names:raise ParseError('アイテム種類の名前定義がありません：'+group)
                ids=[]
                for name in target:
                    matches=[i for i,n in enumerate(names) if name_key(name)==name_key(n) or (group=='catseyes' and name_key(name) in name_key(n))]
                    if len(matches)!=1:raise ParseError('アイテム種類を一意に特定できません：'+name)
                    ids.append(matches[0])
            else:ids=range(len(items))
            for i in ids:
                if i>=len(items):raise ParseError('アイテム配列が定義と一致しません。')
                item=items[i]
                if hasattr(item,'amount'):get=lambda x=item:x.amount;setter=lambda v,x=item:setattr(x,'amount',v)
                else:get=lambda a=items,j=i:a[j];setter=lambda v,a=items,j=i:a.__setitem__(j,v)
                cap=998 if group=='catfruit' and names and 'マタタビ' in names[i] else 999
                put(f'item:{group}:{i}',get(),get,setter,r,cap)
        elif domain=='orbs':
            ranks=[rank for group in meta.get('talent_orbs',[]) for rank in group.get('ranks',[]) if not r.get('rank') or rank['rank']==r['rank']]
            if not ranks:raise ParseError('本能玉ランク定義がありません。')
            for rank in ranks:
                oid=int(rank['id'])
                get=lambda i=oid:getattr(save.talent_orbs.orbs.get(i,0),'value',save.talent_orbs.orbs.get(i,0))
                put(f'orb:{oid}',get(),get,lambda v,i=oid:save.talent_orbs.set_orb(i,v),r,998)
        elif domain=='story':
            for position in r['chapters']:
                chapter=save.story.chapters[module.MAIN_STORY_REAL_INDEX[position]]
                stages=chapter.get_valid_treasure_stages()
                for i,stage in enumerate(stages):
                    context={'treasure':int(stage.treasure),'clear':int(stage.clear_times)}
                    attr='itf_timed_score' if field=='score' else 'treasure' if field=='treasure' else 'clear_times'
                    if field=='clear':
                        setter=lambda v,c=chapter,j=i:c.clear_stage(j,v)
                    else:setter=lambda v,s=stage,a=attr:setattr(s,a,v)
                    put(f'story:{position}:{i}:{field}',getattr(stage,attr),lambda s=stage,a=attr:getattr(s,a),setter,r,9999 if field=='score' else CAPS[field],context)
        elif domain=='legend':
            spec=meta.get(r['series'],{}).get('maps',[])
            if not spec:raise ParseError('レジェンドの章定義がありません。')
            flat=[(m,s) for m in spec for s in range(len(m['stages']))]
            if r.get('until_penultimate'):flat=flat[:-1]
            for m,i in flat:
                max_stars=min(m['crowns'],4 if r.get('progressive') else r.get('stars',4))
                for star in range(max_stars):
                    if r['series']=='legend':chapter=save.event_stages.chapters[0].chapters[m['id']].chapters[star]
                    elif r['series']=='true_legend':chapter=save.uncanny.chapters.chapters[m['id']].chapters[star]
                    else:chapter=save.zero_legends.chapters[m['id']].chapters[star]
                    stage=chapter.stages[i]
                    if r.get('progressive'):
                        # Advancement is decided per map from original completed crowns.
                        completed=0
                        for st in range(m['crowns']):
                            groups=save.event_stages.chapters[0].chapters[m['id']].chapters
                            if all(stage_count(x)>0 for x in groups[st].stages[:len(m['stages'])]):completed=st+1
                            else:break
                        target=3 if completed<=2 else 4 if completed==3 else 0
                        if star>=target:continue
                    def clear(v,s=stage,c=chapter,j=i):
                        c.chapter_unlock_state=3;c.clear_progress=max(c.clear_progress,j+1);s.clear_stage(v,ensure_cleared_only=False)
                    put(f'legend:{r["series"]}:{m["id"]}:{star}:{i}',stage_count(stage),lambda s=stage:stage_count(s),clear,r,2147483647)
        elif domain=='event':
            if r['family']=='aku':
                for mi,maps in enumerate(save.aku.chapters):
                    for star,chapter in enumerate(maps.chapters):
                        for i,stage in enumerate(chapter.stages):
                            def setter(v,s=stage,c=chapter,j=i):s.clear_times=v;c.current_stage=max(c.current_stage,j)
                            put(f'aku:{mi}:{star}:{i}',stage.clear_times,lambda s=stage:s.clear_times,setter,r,2147483647)
            else:
                group=module.EVENT_STAGE_FAMILIES[r['family']]['group']
                for m in r['maps']:
                    for star in range(m.get('crowns',1)):
                        for i in range(len(m['stages'])):
                            def get(g=group,mid=m['id'],st=star,j=i):
                                try:return stage_count(save.event_stages.chapters[g].chapters[mid].chapters[st].stages[j])
                                except (KeyError,IndexError):return 0
                            def setter(v,g=group,mid=m['id'],st=star,j=i):save.event_stages.clear_stage(g,mid,st,j,clear_amount=v)
                            put(f'event:{group}:{m["id"]}:{star}:{i}',get(),get,setter,r,2147483647)
        elif domain=='helpers':
            helpers=save.gamatoto.helpers.helpers
            valid=[h for h in helpers if h.id>=0]
            if len(valid)>10:raise ParseError('既存隊員が10体を超えています。保持したまま10体にできません。')
            choices=compiled['legend_helpers'];used={h.id for h in valid};targets=[]
            for h in valid:
                if field=='legend' and h.id not in choices:
                    choice=next((x for x in choices if x not in used),choices[0]);used.add(choice);targets.append(module.core.game.gamoto.gamatoto.Helper(choice))
                else:targets.append(h)
            while len(targets)<10:
                choice=next((x for x in choices if x not in used),choices[0]);used.add(choice);targets.append(module.core.game.gamoto.gamatoto.Helper(choice))
            checked.append({**r,'targets':targets})
        elif domain=='action':
            # Explicit auxiliary actions cannot touch characters or story state.
            checked.append(r)
        elif domain=='aku_ex':
            for target in r['targets']:
                cid=target['reward_cat'];cat=save.cats.get_cat_by_id(cid-1)
                if cat is None:raise ParseError('富士山EXの報酬キャラがセーブにありません。')
                put(f'cat:{cid}:unlock',int(bool(cat.unlocked)),lambda c=cat:int(bool(c.unlocked)),lambda v,c=cat:c.unlock(save),r,1)
            checked.append(r)
        elif domain in ('profile','helper_mix','tower','labyrinth','account','event_all'):checked.append(r)
        else:raise ParseError('実行未対応の条件対象です：'+domain)
    # Filter levels/talents of units left locked after all unlock exceptions.
    for key in list(pending):
        parts=key.split(':')
        if parts[0]=='cat' and len(parts)>2 and parts[2]!='unlock':
            cid=int(parts[1]);cat=save.cats.get_cat_by_id(cid-1)
            final_unlocked=pending.get(f'cat:{cid}:unlock',{}).get('new',int(bool(cat.unlocked)))
            if not final_unlocked:pending.pop(key)
    protected={key:plain(getattr(save,key)) for key in ('lineups','order_ids','cat_order') if hasattr(save,key)}
    favorite_before=copy.deepcopy(save.cats.favourites) if hasattr(save.cats,'favourites') else None
    unit_protected={cid:{k:copy.deepcopy(getattr(save.cats.get_cat_by_id(cid-1),k)) for k in ('current_form','favourite','favorite') if hasattr(save.cats.get_cat_by_id(cid-1),k) and f'cat:{cid}:{k}' not in pending} for cid in compiled.get('characters',{})}
    for _,entry in sorted(pending.items(),key=lambda item:0 if item[0].endswith(':unlock') else 1):
        if entry['new']!=entry['old']:entry['set'](entry['new'])
    for r in checked:
        if r['domain'] in ('profile','helper_mix','tower','labyrinth','account','event_all','aku_ex'):continue
        if r['domain']=='helpers':
            save.gamatoto.helpers.helpers=r['targets'];continue
        if r['field']=='gamatoto_max':
            levels=module.core.core_data.get_gamatoto_levels(save);save.gamatoto.xp=levels.get_xp_from_level(levels.get_max_level())
        elif r['field']=='facilities':
            specs=meta.get('facilities',[])
            if not specs:raise ParseError('施設の上限定義がありません。')
            module.apply_vip_facilities(save,{x['id']:x['max_base']+x.get('max_plus',0) for x in specs})
        elif r['field']=='ototo':
            for cid,cannon in save.ototo.cannons.cannons.items():cannon.development=3;cannon.levels=[29] if cid==0 else [29,20,20]
        elif r['field']=='shrine':save.cat_shrine.xp_offering=max(save.cat_shrine.xp_offering,100000000)
        elif r['field']=='zombies':module._local_original_apply(save,{'s2':['zombie_clear']},[],{})
        elif r['field']=='remove_errors':module._local_original_apply(save,{'s3':['remove_error_cats']},[],{})
        else:raise ParseError('補助操作が未対応です：'+r['field'])
    from .advanced_engine import apply_extended
    apply_extended(module,save,compiled)
    from .targeted_engine import apply_targets
    apply_targets(module,save,compiled)
    mismatches=[e['label'] for e in pending.values() if e['get']()!=e['new']]
    for r in compiled['rules']:
        if r['domain']=='ownership':
            ids=r['quota_ids'];goal=len(ids)*int(r['expr']['value'])//100
            if sum(bool(save.cats.get_cat_by_id(cid-1).unlocked) for cid in ids)!=goal:
                mismatches.append('所持率：'+r.get('label','対象キャラ'))
    for key,before in protected.items():
        after=getattr(save,key)
        if plain(after)!=before:mismatches.append('保持指定：'+key)
    if favorite_before is not None and save.cats.favourites!=favorite_before:mismatches.append('保持指定：お気に入り')
    for r in checked:
        if r['domain']=='helpers' and [h.id for h in save.gamatoto.helpers.helpers]!=[h.id for h in r['targets']]:mismatches.append('ガマトト隊員')
    for cid,fields in unit_protected.items():
        c=save.cats.get_cat_by_id(cid-1)
        for k,v in fields.items():
            if getattr(c,k)!=v:mismatches.append('保持指定：No.'+str(cid)+':'+k)
    if mismatches:raise RuntimeError('最終検証で条件不一致：'+'・'.join(dict.fromkeys(mismatches))+'。アップロードを停止しました。')
    from .character_forms import verify_forms
    verify_forms(module,save,pending)
    logs=[f'条件付き指定：{len(pending)}項目を変更前の値で判定・最終検証済み']
    if any(r['domain']=='cat' and r['field']=='form' for r in compiled['rules']):
        logs.append(f'第3形態へ切替：{sum(k.endswith(":current_form") for k in pending)}体（未所持は保持）')
    return logs
