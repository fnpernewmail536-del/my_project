"""Bounded declarative edits evaluated once against the pre-edit save.
No user expressions are executed. Unresolved metadata fails before transfer.
"""
import copy
from decimal import Decimal, ROUND_FLOOR
from .support import ParseError, NameIndex, name_key, collaboration_ids, MAX_ITEM_AMOUNTS, roll

def plain(obj):
    if hasattr(obj,'serialize'):return plain(obj.serialize())
    if isinstance(obj,dict):return {k:plain(v) for k,v in obj.items()}
    if isinstance(obj,(list,tuple)):return [plain(v) for v in obj]
    if isinstance(obj,(str,int,float,bool)) or obj is None:return obj
    if hasattr(obj,'__dict__'):return {k:plain(v) for k,v in vars(obj).items() if not callable(v)}
    raise ParseError('保持対象の検証用データを読み取れません。')

SCALARS={k:k for k in ('catfood','xp','np','normal_tickets','rare_tickets','platinum_tickets','legend_tickets','leadership')}
CAPS={**MAX_ITEM_AMOUNTS,'catfruit':998,'orbs':998,'clear':32767,'treasure':3}

def value(expr,old,cap,no_decrease=False):
    op=expr['op']
    if op=='keep':return old
    if op=='max':v=cap
    elif op=='set':v=int(expr['value'])
    elif op=='range':v=roll(expr['bounds'])
    elif op=='add':v=old+int(expr['value'])
    elif op=='mul':v=int((Decimal(str(old))*Decimal(str(expr['value']))).to_integral_value(rounding=ROUND_FLOOR))
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

def select_ids(selector,metadata,index,collabs):
    chars={int(c['id']):c for c in metadata.get('characters',[]) if c.get('selectable',True)}
    ids=set(chars)
    if 'names' in selector:
        ids=set()
        for query in selector['names']:
            exact=index.exact(query)
            if len(exact)!=1:raise ParseError('キャラを一意に特定できません：'+query+'。正式名またはNo.番号を指定してください。')
            ids.add(next(iter(exact)))
    if selector.get('rarity') is not None:ids={i for i in ids if int(chars[i]['rarity'])==selector['rarity']}
    if selector.get('collab') is not None:ids={i for i in ids if (i in collabs)==selector['collab']}
    if selector.get('forms_min'):
        ids={i for i in ids if int(chars[i].get('form_count',1))>=selector['forms_min']}
    if selector.get('group'):
        query=selector['group'];matches=[g for g in metadata.get('groups',[]) if g.get('kind')=='collab' and name_key(query) in name_key(g.get('name',''))]
        if len(matches)!=1:raise ParseError('コラボ名の所属定義を一意に特定できません：'+query)
        ids&=set(int(i) for g in matches for i in g['cat_ids'])&collabs
    if selector.get('series'):
        series=selector['series'];terms={'ちび':['ちび'],'大狂乱':['大狂乱'],'特急':['ネコ特急','ネコ超特急','ネコエクスプレス']}[series]
        ids={i for i in ids if any(any(t in n for t in terms) for n in chars[i].get('names',[]))}
    if not ids:raise ParseError('対象キャラの定義がありません：'+str(selector))
    return sorted(ids)

def priority(selector):
    return 3 if selector.get('names') else 2 if selector.get('group') or selector.get('series') or selector.get('collab') is not None else 1 if selector.get('rarity') is not None else 0

def compile_conditional(module,plan):
    rules=copy.deepcopy(plan.get('conditional_rules',[]))
    if len(rules)>2000:raise ParseError('条件指定が多すぎます。分割してください。')
    for r in rules:
        if r.get('expr',{}).get('op') not in ('keep','max','set','range','add','mul','atleast'):raise ParseError('条件式の操作が不正です。')
        if r['expr']['op']=='range' and not 0<=r['expr']['bounds'][0]<=r['expr']['bounds'][1]<=2147483647:raise ParseError('条件式の範囲が不正です。')
    result={'rules':rules,'no_decrease':plan.get('conditional_no_decrease',False),'cat_no_decrease':plan.get('conditional_cat_no_decrease',False)}
    char_rules=[r for r in rules if r['domain'] in ('cat','talent')]
    if char_rules:
        metadata=module.get_character_metadata();index=NameIndex(metadata)
        need_collab=any(r.get('selector',{}).get('collab') is not None or r.get('selector',{}).get('group') for r in char_rules)
        collabs=collaboration_ids(metadata,metadata['characters']) if need_collab else set()
        result['characters']={int(c['id']):c for c in metadata['characters'] if c.get('selectable',True)}
        levels=any(r['domain']=='cat' and r['field'] in ('base','plus') for r in rules)
        if levels:
            raw=module._latest_jp_game_data_getter().download('DataLocal','unitbuy.csv')
            if raw is None:raise ParseError('キャラ上限定義を取得できません。引き継ぎ取得前に停止しました。')
            rows=list(module.core.CSV(raw));result['caps']={}
            target_ids=set(i for rule in char_rules if rule['domain']=='cat' and rule['field'] in ('base','plus') for i in select_ids(rule.get('selector',{}),metadata,index,collabs))
            for cid in target_ids:
                info=result['characters'][cid]
                if cid>len(rows) or len(rows[cid-1])<=51 or rows[cid-1][13].to_int()!=int(info['rarity']):raise ParseError('キャラ上限定義が一致しません：No.'+str(cid))
                result['caps'][cid]={'base':max(1,rows[cid-1][50].to_int()),'plus':max(0,rows[cid-1][51].to_int())}
        for r in char_rules:
            r['ids']=select_ids(r.get('selector',{}),metadata,index,collabs);r['priority']=priority(r.get('selector',{}))
            expected=r.get('expected_ids')
            if expected is not None and expected!=r['ids']:raise ParseError('対象キャラの定義が変わりました。確認し直してください。')
            if r['domain']=='talent' and r.get('exclude_speed'):
                for cid in r['ids']:
                    if any(not t.get('name') for t in result['characters'][cid].get('talents',[])):raise ParseError('移動速度本能の名前定義がありません。')
    if any(r['domain'] in ('legend','event','items','orbs','facilities','action') for r in rules):result['legend_metadata']=module.get_legend_metadata()
    if any(r['domain']=='helpers' for r in rules):
        raw=module._latest_jp_game_data_getter().download('resLocal','GamatotoExpedition_Members_name_ja.csv')
        if raw is None:raise ParseError('隊員定義がありません。')
        rows=list(module.core.CSV(raw,module.core.Delimeter.from_country_code_res(module.core.CountryCode.from_code('jp')),remove_empty=False))
        result['legend_helpers']=[row[0].to_int() for row in rows[1:] if len(row)>4 and row[0].to_int()>=0 and 'レジェンド' in row[4].to_str()]
        if not result['legend_helpers']:raise ParseError('レジェンド隊員定義がありません。')
    for r in rules:
        if r['domain']=='items':r['priority']=3 if r.get('names') else 0
        if r['domain']=='orbs':r['priority']=1 if r.get('rank') else 0
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
    return result

def apply_conditional(module,save,compiled):
    """Calculate all final writes before mutating. Each path is written once."""
    pending={};checked=[]
    if any(r['domain']=='legend' for r in compiled['rules']):
        module._ensure_latest_stage_slots(save, compiled['legend_metadata'])
    # Extend only addressed slots from resolved map definitions. Existing newer
    # slots and progress are retained, including crowns outside the selection.
    def prepare_maps(container, specs, event=False):
        classes=module.core.game.map.event if event else module.core.game.map.chapters
        stars_type=classes.EventSubChapterStars if event else classes.ChaptersStars
        chapter_type=classes.EventSubChapter if event else classes.Chapter
        stage_type=classes.EventStage if event else classes.Stage
        count=max([len(container.chapters)]+[int(m['id'])+1 for m in specs])
        crowns=max([len(m.chapters) for m in container.chapters]+[int(m['crowns']) for m in specs]+[1])
        length=max([len(ch.stages) for m in container.chapters for ch in m.chapters]+[len(m['stages']) for m in specs]+[1])
        while len(container.chapters)<count:container.chapters.append(stars_type.init(0) if event else stars_type.init(0,0))
        for m in container.chapters:
            while len(m.chapters)<crowns:m.chapters.append(chapter_type.init(length))
            for chapter in m.chapters:
                while len(chapter.stages)<length:chapter.stages.append(stage_type.init())
    event_specs=[]
    for rule in compiled['rules']:
        if rule['domain']=='legend' and rule['series']=='true_legend':
            prepare_maps(save.uncanny.chapters,compiled['legend_metadata']['true_legend']['maps'])
            while len(save.uncanny.unknown)<len(save.uncanny.chapters.chapters):save.uncanny.unknown.append(0)
        elif rule['domain']=='legend' and rule['series']=='legend':
            event_specs.extend(compiled['legend_metadata']['legend']['maps'])
        elif rule['domain']=='event' and rule['family']!='aku':event_specs.extend(rule['maps'])
    if event_specs:
        required_groups=[0]+[module.EVENT_STAGE_FAMILIES[r['family']]['group'] for r in compiled['rules'] if r['domain']=='event' and r['family']!='aku']
        while len(save.event_stages.chapters)<=max(required_groups):save.event_stages.chapters.append(module.core.game.map.event.EventChapterGroup.init(0,0))
        for container in save.event_stages.chapters:prepare_maps(container,event_specs,event=True)
        # The event serialization uses shared rectangular dimensions.
        for container in save.event_stages.chapters:prepare_maps(container,event_specs,event=True)
    def stage_count(stage):
        return stage.clear_amount if hasattr(stage,'clear_amount') else stage.clear_times
    no_decrease=compiled['no_decrease']
    def put(key,old,getter,setter,rule,cap,context=None):
        old=int(old);context=context or {rule['field']:old}
        if not predicate(rule.get('when'),context,rule['field']):return
        rank=(rule.get('priority',0),rule['_order'])
        if key in pending and pending[key]['rank']>rank:return
        new=value(rule['expr'],old,cap,no_decrease or (compiled.get('cat_no_decrease',False) and rule['domain']=='cat') or rule.get('no_decrease',False))
        pending[key]={'old':old,'new':new,'get':getter,'set':setter,'rank':rank,'label':rule.get('label',key)}
    meta=compiled.get('legend_metadata',{})
    for order,r in enumerate(compiled['rules']):
        r={**r,'_order':order};domain=r['domain'];field=r['field'];expr=r['expr']
        if domain=='resource':
            attr=SCALARS[field];old=getattr(save,attr)
            put(field,old,lambda a=attr:getattr(save,a),lambda v,a=attr:setattr(save,a,v),r,CAPS[field])
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
                    put(f'cat:{cid}:unlock',old['unlocked'],lambda c=cat:int(bool(c.unlocked)),setter,r,1,old)
                elif field in ('base','plus'):
                    # Existing locked units only receive levels if this order unlocks them.
                    getter=(lambda c=cat:c.upgrade.base+1) if field=='base' else (lambda c=cat:c.upgrade.plus)
                    setter=(lambda v,c=cat:setattr(c.upgrade,'base',v-1)) if field=='base' else (lambda v,c=cat:setattr(c.upgrade,'plus',v))
                    put(f'cat:{cid}:{field}',old[field],getter,setter,r,compiled['caps'][cid][field],old)
                elif field=='forms':
                    put(f'cat:{cid}:forms',cat.unlocked_forms,lambda c=cat:c.unlocked_forms,lambda v,c=cat:setattr(c,'unlocked_forms',v),{**r,'no_decrease':True},max(cat.unlocked_forms,min(3,int(compiled['characters'][cid].get('form_count',1)))))
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
                    attr='treasure' if field=='treasure' else 'clear_times'
                    if field=='clear':
                        setter=lambda v,c=chapter,j=i:c.clear_stage(j,v)
                    else:setter=lambda v,s=stage:setattr(s,'treasure',v)
                    put(f'story:{position}:{i}:{field}',getattr(stage,attr),lambda s=stage,a=attr:getattr(s,a),setter,r,CAPS[field],context)
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
                            groups=save.event_stages.chapters[0].chapters[m['id']].chapters if r['series']=='legend' else save.uncanny.chapters.chapters[m['id']].chapters if r['series']=='true_legend' else save.zero_legends.chapters[m['id']].chapters
                            if all(stage_count(x)>0 for x in groups[st].stages[:len(m['stages'])]):completed=st+1
                            else:break
                        target=min(completed+1,m['crowns']) if completed<m['crowns'] else 0
                        if star>=target:continue
                    def clear(v,s=stage,c=chapter,j=i,series=r['series']):
                        c.chapter_unlock_state=3;
                        if series=='zero_legend':c.unlock_state=3
                        c.clear_progress=max(c.clear_progress,j+1);s.clear_stage(v,ensure_cleared_only=False)
                    put(f'legend:{r["series"]}:{m["id"]}:{star}:{i}',stage_count(stage),lambda s=stage:stage_count(s),clear,r,32767)
        elif domain=='event':
            if r['family']=='aku':
                specs=meta['aku']['chapters']
                known=max(len(spec['stages']) for spec in specs)
                if not save.aku.chapters:
                    save.aku.chapters.append(module.core.game.map.aku.ChaptersStars.init(known,1))
                for mi,maps in enumerate(save.aku.chapters):
                    for star,chapter in enumerate(maps.chapters):
                        for i,stage in enumerate(chapter.stages[:known]):
                            def setter(v,s=stage,c=chapter,j=i):s.clear_times=v;c.current_stage=max(c.current_stage,j)
                            put(f'aku:{mi}:{star}:{i}',stage.clear_times,lambda s=stage:s.clear_times,setter,r,32767)
            else:
                group=module.EVENT_STAGE_FAMILIES[r['family']]['group']
                for m in r['maps']:
                    for star in range(m.get('crowns',1)):
                        for i in range(len(m['stages'])):
                            def get(g=group,mid=m['id'],st=star,j=i):
                                try:return stage_count(save.event_stages.chapters[g].chapters[mid].chapters[st].stages[j])
                                except (KeyError,IndexError):return 0
                            def setter(v,g=group,mid=m['id'],st=star,j=i):save.event_stages.clear_stage(g,mid,st,j,clear_amount=v)
                            put(f'event:{group}:{m["id"]}:{star}:{i}',get(),get,setter,r,32767)
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
            checked=[x for x in checked if x['domain']!='helpers']
            checked.append({**r,'targets':targets})
        elif domain=='action':
            # Explicit auxiliary actions cannot touch characters or story state.
            checked.append(r)
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
    unit_protected={cid:{k:copy.deepcopy(getattr(save.cats.get_cat_by_id(cid-1),k)) for k in ('current_form','favourite','favorite') if hasattr(save.cats.get_cat_by_id(cid-1),k)} for cid in compiled.get('characters',{})}
    for entry in pending.values():
        if entry['new']!=entry['old']:entry['set'](entry['new'])
    for r in checked:
        if r['domain']=='helpers':
            save.gamatoto.helpers.helpers=r['targets'];continue
        selected={'s4':[{'gamatoto_max':'gamatoto_max','facilities':'base_upgrades_max','ototo':'ototo_max','shrine':'cat_shrine_max','remove_errors':'remove_error_cats'}[r['field']]]}
        module.apply_daiko_segment(save,selected['s4'],[],{})
    mismatches=[e['label'] for e in pending.values() if e['get']()!=e['new']]
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
    # Check the actual BCSFE byte representation before the existing worker can
    # upload it. A serialization mismatch raises into its recovery workflow.
    roots={*protected}
    for rule in compiled['rules']:
        domain=rule['domain']
        if domain=='resource':roots.add(rule['field'])
        elif domain in ('cat','talent'):roots.add('cats')
        elif domain=='items':roots.add('ototo' if rule['group']=='base_materials' else rule['group'] if rule['group']!='battle_items' else 'battle_items')
        elif domain=='orbs':roots.add('talent_orbs')
        elif domain=='story':roots.add('story')
        elif domain=='legend':roots.add({'legend':'event_stages','true_legend':'uncanny','zero_legend':'zero_legends'}[rule['series']])
        elif domain=='event':roots.add('aku' if rule['family']=='aku' else 'event_stages')
        elif domain=='helpers':roots.add('gamatoto')
    expected={key:plain(getattr(save,key)) for key in roots}
    version=save.game_version.game_version
    reloaded=module.core.SaveFile(module.core.Data(save.to_data().data),cc=save.cc)
    if reloaded.game_version.game_version!=version:
        raise RuntimeError('保存・再読込の版数が一致しません。アップロードを停止しました。')
    failed=[key for key,before in expected.items() if plain(getattr(reloaded,key))!=before]
    if failed:raise RuntimeError('保存・再読込の検証が一致しません：'+'・'.join(failed)+'。アップロードを停止しました。')
    return [f'条件付き指定：{len(pending)}項目を変更前の値で判定・保存／再読込検証済み']
