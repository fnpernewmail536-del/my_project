"""Reviewed event definitions and Fuji EX, with native save roundtrip checks."""
import copy
from .parser import ParseError
from .named_characters import NameIndex

DOMAINS={'event_all','aku_ex'}
KINDS={'event','tower','catamin_stages','legend_quest','gauntlets','enigma_clears','collab_gauntlets','behemoth_culling','labyrinth','dojo_chapters'}


def target_manifest(module,rule):
    meta=module.get_legend_metadata()
    if rule['domain']=='aku_ex':
        chapters=meta.get('aku_ex',{}).get('chapters',[])
        if len(chapters)!=1 or int(chapters[0].get('id',-1))!=0 or not chapters[0].get('stages'):
            raise ParseError('富士山EXのステージ定義が不足しています。')
        names=NameIndex(module.get_character_metadata())
        if set(names.exact('ジャガンドーJr'))!={623}:
            raise ParseError('富士山EXの報酬キャラ定義（No.623）を照合できません。')
        return [{'family':'aku_ex','kind':'event','group':1,'id':42,'name':chapters[0]['name'],'crowns':1,'stages':[chapters[0]['stages'][0]],'reward_cat':623}]
    families=meta.get('event_stage_families',{});specs=getattr(module,'EVENT_STAGE_FAMILIES',{})
    if not families:raise ParseError('イベントの対象定義を読み込めません。')
    wanted=rule.get('families',[])
    if wanted!=['all']:raise ParseError('イベントの対象範囲を確認してください。')
    result=[]
    for key,family in sorted(families.items()):
        maps=family.get('maps',[])
        if not maps:continue
        spec=specs.get(key,{})
        if spec.get('kind') not in KINDS:raise ParseError('イベントの保存接続を照合できません：'+key)
        for m in maps:
            if not isinstance(m.get('id'),int) or not 0<=m['id']<1000 or not 1<=m.get('crowns',1)<=4 or not isinstance(m.get('stages'),list) or not 1<=len(m['stages'])<=(256 if spec['kind']=='labyrinth' else 64):
                raise ParseError('イベントのマップ・冠・ステージ定義が不正です：'+key)
            if spec['kind']=='labyrinth' and (m['id']!=0 or m.get('crowns',1)!=1):raise ParseError('地底迷宮のマップ定義が未対応の形式です。')
            if spec['kind']=='event' and not isinstance(spec.get('group'),int):raise ParseError('イベントのグループ定義がありません。')
            result.append({'family':key,'kind':spec['kind'],'group':spec.get('group'), 'base_index':spec.get('base_index'), 'id':m['id'],'name':m['name'],'crowns':m.get('crowns',1),'stages':list(m['stages'])})
    if not result:raise ParseError('クリア対象のイベント定義がありません。')
    if len({(m['family'],m['id']) for m in result})!=len(result):raise ParseError('イベントの対象定義が重複しています。')
    if sum(len(m['stages'])*m['crowns'] for m in result)>50000:raise ParseError('イベントの対象が50000ステージを超えています。系統を分けてください。')
    return result


def review_targets(module,plan):
    from .review_constraints import enforce_review_constraints
    enforce_review_constraints(plan)
    key='conditional_rules' if plan.get('conditional_mode') else 'document_rules'
    rules=[r for r in plan.get(key,[]) if r['domain'] in DOMAINS]
    prior_review=any(l['key'].startswith('target_review_') for l in plan.get('labels',[])) or any(x.get('scope')=='target_review' for x in plan.get('unresolved',[]))
    if not rules and not prior_review:return plan
    plan['labels']=[l for l in plan['labels'] if not l['key'].startswith('target_review_')]
    plan['unresolved']=[x for x in plan.get('unresolved',[]) if x.get('scope')!='target_review']
    for i,rule in enumerate(rules):
        try:
            if module is None:raise ParseError('エンジンを読み込んだ後、注文書を送り直してください。')
            rule['reviewed_targets']=target_manifest(module,rule)
            for j,m in enumerate(rule['reviewed_targets']):
                label=f'{m["name"]}：全{len(m["stages"])}ステージ・★1〜{m["crowns"]}（既存クリア回数は減らしません）'
                if m.get('reward_cat'):label+='・報酬キャラ：ジャガンドーJr（No.623）を取得'
                plan['labels'].append({'key':f'target_review_{i}_{j}','label':label})
        except (ParseError,RuntimeError) as exc:
            rule.pop('reviewed_targets',None)
            plan['unresolved'].append({'input':rule['label'],'reason':'【定義確認】'+str(exc),'category':'metadata','scope':'target_review'})
    plan['blockers']=list(plan.get('menu_pending',{}).values())+[f'未解釈項目{i+1}：{x["input"]} — {x["reason"]}' for i,x in enumerate(plan['unresolved'])]
    plan['ready']=not plan['blockers'] and not plan['missing']
    return plan


def compile_targets(module,compiled):
    for rule in compiled['rules']:
        if rule['domain']=='story' and rule['field'] in ('clear','score'):
            mapping=getattr(module,'MAIN_STORY_REAL_INDEX',None)
            if not isinstance(mapping,(list,tuple)) or len(mapping)!=9:raise ParseError('本編の章と保存先の対応定義がありません。')
            if not rule.get('chapters') or not set(rule['chapters'])<=set(range(9)):raise ParseError('本編の対象章が不正です。')
            if rule['field']=='score' and not set(rule['chapters'])<={3,4,5}:raise ParseError('採点スコアは未来編の対象章を指定してください。')
        if rule['domain'] not in DOMAINS:continue
        manifest=target_manifest(module,rule)
        if rule.get('reviewed_targets')!=manifest:
            raise ParseError('イベント・富士山EXの対象定義が未確認、または変更されています。注文書を送り直し、対象一覧を確認してください。')
        if rule['domain']=='aku_ex' and any(other['domain']=='cat' and other['field']=='unlocked' and other.get('expr')=={'op':'set','value':0} and 623 in other.get('ids',[]) for other in compiled['rules']):
            raise ParseError('富士山EXの報酬キャラ取得と、そのキャラの削除指定が矛盾しています。')
        rule['targets']=manifest


def container_for(save,kind):
    return save.tower.chapters if kind=='tower' else save.catamin_stages.chapters if kind=='catamin_stages' else getattr(save,kind)


def stage_for(save,m,star,index):
    if m['kind']=='event':container=save.event_stages.chapters[m['group']]
    else:container=container_for(save,m['kind'])
    return container.chapters[m['id']].chapters[star].stages[index]


def clear_value(stage):
    return int(stage.clear_amount if hasattr(stage,'clear_amount') else stage.clear_times)


def ensure_slots(module,save,targets):
    """Native rectangular formats require all old rows to keep equal dimensions."""
    maps=module.core.game.map
    event=[m for m in targets if m['kind']=='event']
    if event:
        groups=save.event_stages.chapters
        ng=max([len(groups)]+[m['group']+1 for m in event])
        nm=max([len(g.chapters) for g in groups]+[m['id']+1 for m in event])
        ns=max([len(m.chapters) for g in groups for m in g.chapters]+[m['crowns'] for m in event])
        nt=max([len(c.stages) for g in groups for m in g.chapters for c in m.chapters]+[len(m['stages']) for m in event])
        if ng>255 or nm>32767 or ns>255 or nt>255:raise ParseError('イベントの保存枠が形式の上限を超えます。')
        while len(groups)<ng:groups.append(maps.event.EventChapterGroup.init(0,0))
        for g in groups:
            while len(g.chapters)<nm:g.chapters.append(maps.event.EventSubChapterStars.init(0))
            for m in g.chapters:
                while len(m.chapters)<ns:m.chapters.append(maps.event.EventSubChapter.init(0))
                for c in m.chapters:
                    while len(c.stages)<nt:c.stages.append(maps.event.EventStage.init())
    kinds={m['kind'] for m in targets}-{'event','labyrinth','dojo_chapters'}
    for kind in kinds:
        selected=[m for m in targets if m['kind']==kind];container=container_for(save,kind)
        lib=maps.chapters if kind in ('tower','catamin_stages') else maps.legend_quest if kind=='legend_quest' else maps.gauntlets
        nm=max([len(container.chapters)]+[m['id']+1 for m in selected])
        ns=max([len(m.chapters) for m in container.chapters]+[m['crowns'] for m in selected])
        nt=max([len(c.stages) for m in container.chapters for c in m.chapters]+[len(m['stages']) for m in selected])
        if kind=='legend_quest' and nm>255:raise ParseError('レジェンドクエストの保存枠が形式の上限を超えます。')
        while len(container.chapters)<nm:container.chapters.append(lib.ChaptersStars.init(nt,ns))
        for m in container.chapters:
            while len(m.chapters)<ns:m.chapters.append(lib.Chapter.init(nt))
            for c in m.chapters:
                while len(c.stages)<nt:c.stages.append(lib.Stage.init())
                c.total_stages=nt
                if kind=='legend_quest':
                    for s in c.stages:
                        if not hasattr(s,'tries'):s.tries=0
        if hasattr(container,'unknown'):
            while len(container.unknown)<nm:container.unknown.append(0)
        if kind=='legend_quest' and len(container.ids)!=nt:
            raise ParseError('レジェンドクエストのステージID定義と保存枠が一致しません。')
        if kind=='catamin_stages':
            while len(save.catamin_stages.unknown)<nm:save.catamin_stages.unknown.append(0)
    for m in [m for m in targets if m['kind']=='dojo_chapters']:
        container=save.dojo_chapters
        while len(container.chapters)<=m['id']:container.chapters.append(maps.zero_legends.ChaptersStars.init())
        stars=container.chapters[m['id']].chapters
        while len(stars)<m['crowns']:stars.append(maps.zero_legends.Chapter.init())
        for c in stars:
            while len(c.stages)<len(m['stages']):c.stages.append(maps.zero_legends.Stage.init())


def apply_targets(module,save,compiled):
    rules=[r for r in compiled['rules'] if r['domain'] in DOMAINS]
    if not rules:
        verify_story(module,save,compiled);return
    targets={ (m['family'],m['id']):m for r in rules for m in r['targets'] }
    targets=list(targets.values())
    protected={name:copy.deepcopy(getattr(save,name).serialize()) for name in ('lineups',) if hasattr(save,name)}
    old_cats=copy.deepcopy(save.cats.serialize())
    if any(m.get('reward_cat') for m in targets) and save.cats.get_cat_by_id(622) is None:raise ParseError('富士山EXの報酬キャラがセーブにありません。')
    for m in targets:
        if m['kind']=='labyrinth' and not all(callable(getattr(module,n,None)) for n in ('apply_labyrinth_stages','_labyrinth_value','_set_labyrinth_value')):
            raise ParseError('地底迷宮の実行接続がありません。')
        if m['kind']=='legend_quest' and len(save.legend_quest.ids)<len(m['stages']):raise ParseError('レジェンドクエストのステージID定義が不足しています。')
    if any(m.get('reward_cat') for m in targets):
        try:save.item_reward_stages.sub_chapters[42].sub_chapters[0].stages[0]
        except (AttributeError,IndexError) as exc:raise ParseError('富士山EXの報酬保存領域が不足しています。') from exc
    ensure_slots(module,save,targets)
    expected=[]
    for m in targets:
        if m['kind']=='labyrinth':
            n=len(m['stages']);before=module._labyrinth_value(save,('highest_floor','max_cleared_floor','ush5'),0)
            module.apply_labyrinth_stages(save,{0:{0:list(range(n))}})
            # Clearing all floors must never lower a separately stored best floor.
            module._set_labyrinth_value(save,('highest_floor','max_cleared_floor','ush5'),max(before,n))
            expected.append(('labyrinth',n,max(before,n)));continue
        for star in range(m['crowns']):
            for i in range(len(m['stages'])):
                stage=stage_for(save,m,star,i);value=max(1,clear_value(stage))
                if m['kind']=='event':chapter=save.event_stages.chapters[m['group']].chapters[m['id']].chapters[star]
                else:chapter=container_for(save,m['kind']).chapters[m['id']].chapters[star]
                chapter.clear_stage(i,value)
                if m['kind']=='dojo_chapters':chapter.unlock_state=3
                expected.append(('stage',m,star,i,value))
        if m['kind']=='catamin_stages':
            key=m['base_index']+m['id'];counts=save.event_stages.chapter_completion_count
            counts[key]=max(1,counts.get(key,0))
        if m.get('reward_cat'):
            reward=save.item_reward_stages.sub_chapters[42].sub_chapters[0].stages[0]
            reward.claimed=True
            cat=save.cats.get_cat_by_id(m['reward_cat']-1)
            if not cat.unlocked:cat.unlock(save)
    if not hasattr(save,'to_data'):raise ParseError('保存・再読込を検証できないため送信を停止しました。')
    loaded=module.core.SaveFile(module.core.Data(save.to_data().data),cc=save.cc)
    if loaded.game_version.game_version!=save.game_version.game_version:raise RuntimeError('ゲームの保存版数が一致しません。')
    for item in expected:
        if item[0]=='stage':
            _,m,star,i,value=item
            if clear_value(stage_for(loaded,m,star,i))!=value:raise RuntimeError('イベントの保存・再読込検証に失敗しました：'+m['name'])
        else:
            _,n,best=item
            if module._labyrinth_value(loaded,('current_floor','cleared_floor','ush4'),0)<n or module._labyrinth_value(loaded,('highest_floor','max_cleared_floor','ush5'),0)!=best:raise RuntimeError('地底迷宮の保存・再読込検証に失敗しました。')
    rewards={m['reward_cat'] for m in targets if m.get('reward_cat')}
    if rewards and (not loaded.cats.get_cat_by_id(622).unlocked or not loaded.item_reward_stages.sub_chapters[42].sub_chapters[0].stages[0].claimed):raise RuntimeError('富士山EXの報酬保存を検証できません。')
    for name,value in protected.items():
        if getattr(loaded,name).serialize()!=value:raise RuntimeError('編成の保持検証に失敗しました。')
    # Units other than the explicit Fuji reward remain exactly as they were.
    before=old_cats['cats'];after=loaded.cats.serialize()['cats']
    if len(before)!=len(after) or any(b!=a for i,(b,a) in enumerate(zip(before,after),1) if i not in rewards):raise RuntimeError('イベント処理によるキャラの保持検証に失敗しました。')
    for cid in rewards:
        for key,value in before[cid-1].items():
            if key not in ('unlocked','gatya_seen') and after[cid-1].get(key)!=value:raise RuntimeError('富士山EX報酬キャラの育成値保持検証に失敗しました。')
    for key,value in old_cats.items():
        if key!='cats' and loaded.cats.serialize()[key]!=value:raise RuntimeError('キャラのお気に入り・保存情報の保持検証に失敗しました。')
    verify_story(module,save,compiled,loaded)


def verify_story(module,save,compiled,loaded=None):
    rules=[r for r in compiled['rules'] if r['domain']=='story' and r.get('strict_bounds')]
    if not rules:return
    if loaded is None:
        if not hasattr(save,'to_data'):raise ParseError('本編の保存・再読込を検証できません。')
        loaded=module.core.SaveFile(module.core.Data(save.to_data().data),cc=save.cc)
    for r in rules:
        for position in r['chapters']:
            real=module.MAIN_STORY_REAL_INDEX[position]
            a=save.story.chapters[real].get_valid_treasure_stages();b=loaded.story.chapters[real].get_valid_treasure_stages()
            attr={'score':'itf_timed_score','clear':'clear_times','treasure':'treasure'}[r['field']]
            if not a or len(a)!=len(b) or [getattr(s,attr) for s in a]!=[getattr(s,attr) for s in b]:raise RuntimeError('本編の保存・再読込検証に失敗しました。')
