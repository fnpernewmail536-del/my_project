"""Resolved additional edits; all bounds and targets come from declared data."""
import copy
from .parser import ParseError
from .named_characters import NameIndex, name_key


def compile_extended(module, compiled):
    rules=compiled['rules']
    advanced=[r for r in rules if r['domain'] in ('profile','helper_mix','tower','labyrinth','account')]
    if not advanced:return
    def download(location,name):
        raw=module._latest_jp_game_data_getter().download(location,name)
        if raw is None:raise ParseError('追加指定の定義を取得できません：'+name)
        return raw
    if any(r['domain'] in ('tower','labyrinth') for r in advanced):
        compiled.setdefault('legend_metadata',module.get_legend_metadata())
    if any(r['domain']=='helper_mix' for r in advanced):
        raw=download('resLocal','GamatotoExpedition_Members_name_ja.csv')
        rows=list(module.core.CSV(raw,module.core.Delimeter.from_country_code_res(module.core.CountryCode.from_code('jp')),remove_empty=False))
        classes={}
        for row in rows[1:]:
            if len(row)>=5 and row[0].to_int()>=0:
                classes.setdefault(row[4].to_str(),[]).append(row[0].to_int())
        compiled['helper_classes']={name:sorted(set(ids)) for name,ids in classes.items()}
    accounts=[r for r in advanced if r['domain']=='account']
    if accounts:
        metadata=module.get_character_metadata();index=NameIndex(metadata)
        compiled['characters']={int(c['id']):c for c in metadata['characters'] if c.get('selectable',True)}
        for r in accounts:
            if not r.get('selector',{}).get('names'):raise ParseError('ランク・所持数の調整対象キャラ名を指定してください。')
            ids=set()
            for query in r['selector']['names']:
                matches=index.exact(query)
                if len(matches)!=1:raise ParseError('調整対象を一意に特定できません：'+query)
                ids.update(matches)
            rarity=r['selector'].get('rarity')
            if rarity is not None:ids={i for i in ids if int(compiled['characters'][i]['rarity'])==rarity}
            if not ids:raise ParseError('指定レア度の調整対象がありません。')
            r['ids']=sorted(ids)
            if r.get('expected_ids') is not None and r['expected_ids']!=r['ids']:raise ParseError('調整対象の定義が変わりました。確認し直してください。')
            if r['field']=='rank':
                if not 0<=r['expr']['value']<=2147483647:raise ParseError('ユーザーランクの数値が範囲外です。')
                # Rank adjustment is confined to plus values in the explicit list.
                if any(other['domain']=='cat' and other['field']=='plus' and set(other.get('ids',[]))&ids for other in rules):
                    raise ParseError('ランク調整対象に＋値の個別指定があります。別の対象を指定するか別依頼に分けてください。')
                rows=list(module.core.CSV(download('DataLocal','unitbuy.csv')))
                caps=compiled.setdefault('caps',{})
                for cid in ids:
                    if cid>len(rows) or len(rows[cid-1])<=51 or rows[cid-1][13].to_int()!=int(compiled['characters'][cid]['rarity']):
                        raise ParseError('ランク調整対象の上限定義が一致しません：No.'+str(cid))
                    caps[cid]={'base':max(1,rows[cid-1][50].to_int()),'plus':max(0,rows[cid-1][51].to_int())}
            elif not 0<=r['expr']['value']<=len(compiled['characters']):raise ParseError('所持数がキャラ定義の範囲外です。')
    for r in advanced:
        domain=r['domain'];number=r['expr']['value']
        if domain=='profile':
            if r['field']=='playtime' and not 0<=number<=9999*3600+3599:raise ParseError('プレイ時間が範囲外です。')
            if r['field']=='gamatoto_level':
                rows=list(module.core.CSV(download('DataLocal','GamatotoExpedition.csv')))
                limits=list(module.core.CSV(download('DataLocal','GamatotoExpedition_Limit.csv')))
                if not limits or not 1<=number<=limits[0][0].to_int() or number>len(rows):raise ParseError('ガマトトLvが取得済みの定義を超えています。')
                r['xp']=rows[number-1][0].to_int() if number>1 else 0
        elif domain=='helper_mix':
            for name,count in list(r['composition'].items()):
                matches=[key for key in compiled['helper_classes'] if name_key(key)==name_key(name)]
                if len(matches)!=1 or len(compiled['helper_classes'][matches[0]])<count:raise ParseError('指定クラスの隊員定義が不足しています：'+name)
                if matches[0]!=name:r['composition'][matches[0]]=r['composition'].pop(name)
            if sum(r['composition'].values())+r.get('keep_nonlegend',0)>10:raise ParseError('隊員は合計10体以内です。')
            if any(other['domain']=='helpers' for other in rules):raise ParseError('隊員の一括指定とクラス別指定を混在させないでください。')
        elif domain=='tower':
            maps=compiled['legend_metadata'].get('event_stage_families',{}).get('tower',{}).get('maps',[])
            matches=[m for m in maps if name_key(m['name'])==name_key(r['map_name'])]
            if len(matches)!=1:raise ParseError('塔の対象定義を一意に特定できません：'+r['map_name'])
            r['map']=copy.deepcopy(matches[0])
            if not 1<=r['floor']<=len(r['map']['stages']):raise ParseError('塔の階数が取得済み定義を超えています。')
        elif domain=='labyrinth':
            maps=compiled['legend_metadata'].get('event_stage_families',{}).get('labyrinth',{}).get('maps',[])
            matches=[m for m in maps if int(m['id'])==0]
            if len(matches)!=1 or not 1<=number<=len(matches[0]['stages']):raise ParseError('地底迷宮の階数が取得済み定義を超えています。')
            if not all(callable(getattr(module,n,None)) for n in ('_labyrinth_value','_set_labyrinth_value','apply_labyrinth_stages')):
                raise ParseError('元main13.pyに地底迷宮の実行接続がありません。対応版を適用してください。')


def apply_extended(module,save,compiled):
    advanced=[r for r in compiled['rules'] if r['domain'] in ('profile','helper_mix','tower','labyrinth','account')]
    if not advanced and not any(r['domain'] in ('resource','items','cat','talent') for r in compiled['rules']):return
    protected=copy.deepcopy(save.lineups.serialize()) if hasattr(save,'lineups') else None
    expect=[]
    for r in advanced:
        domain=r['domain'];number=r['expr']['value']
        if domain=='profile':
            if r['field']=='playtime':
                frames=module.core.PlayTime.from_seconds(number).frames
                save.officer_pass.play_time=max(save.officer_pass.play_time,frames) if compiled['no_decrease'] else frames
                expect.append(('playtime',save.officer_pass.play_time))
            else:
                save.gamatoto.xp=max(save.gamatoto.xp,r['xp']) if compiled['no_decrease'] else r['xp']
                expect.append(('gamatoto_xp',save.gamatoto.xp))
        elif domain=='helper_mix':
            classes=compiled['helper_classes'];helpers=save.gamatoto.helpers.helpers
            inverse={cid:name for name,ids in classes.items() for cid in ids}
            existing=[h for h in helpers if h.id>=0]
            if any(h.id not in inverse for h in existing):raise ParseError('既存隊員のクラスを照合できません。')
            selected=[];used=set()
            for name,count in list(r['composition'].items()):
                keep=[h for h in existing if inverse[h.id]==name][:count]
                selected.extend(keep);used.update(h.id for h in keep)
                choices=[cid for cid in classes[name] if cid not in used]
                if len(choices)<count-len(keep):raise ParseError('隊員の候補が不足しています。')
                for cid in choices[:count-len(keep)]:selected.append(module.core.game.gamoto.gamatoto.Helper(cid));used.add(cid)
            if r.get('keep_nonlegend'):
                others=[h for h in existing if 'レジェンド' not in inverse[h.id]]
                if len(others)!=r['keep_nonlegend']:raise ParseError('保持する既存非レジェンド隊員の人数が指定と一致しません。クラスを明示してください。')
                selected.extend(others)
            save.gamatoto.helpers.helpers=selected+[module.core.game.gamoto.gamatoto.Helper(-1) for _ in range(10-len(selected))]
            expect.append(('helpers',[h.id for h in save.gamatoto.helpers.helpers]))
        elif domain=='tower':
            specs=compiled['legend_metadata']['event_stage_families']['tower']['maps']
            container=save.tower.chapters
            # Standard Chapters is a rectangular save structure. Preserve its old slots.
            count=max([len(container.chapters)]+[int(m['id'])+1 for m in specs])
            crowns=max([len(m.chapters) for m in container.chapters]+[int(m.get('crowns',1)) for m in specs])
            stages=max([len(ch.stages) for m in container.chapters for ch in m.chapters]+[len(m['stages']) for m in specs])
            kinds=module.core.game.map.chapters
            while len(container.chapters)<count:container.chapters.append(kinds.ChaptersStars.init(stages,crowns))
            for m in container.chapters:
                while len(m.chapters)<crowns:m.chapters.append(kinds.Chapter.init(stages))
                for ch in m.chapters:
                    while len(ch.stages)<stages:ch.stages.append(kinds.Stage.init())
            chapter=container.chapters[r['map']['id']].chapters[0]
            for i in range(r['floor']):chapter.clear_stage(i,max(1,chapter.stages[i].clear_times))
            expect.append(('tower',(r['map']['id'],r['floor'])))
        elif domain=='labyrinth':
            if r['field']=='highest':
                names=('highest_floor','max_cleared_floor','ush5')
                previous=module._labyrinth_value(save,names,None)
                if previous is None:raise ParseError('最高到達階の保存領域がありません。')
                module._set_labyrinth_value(save,names,max(previous,number))
                expect.append(('labyrinth_highest',max(previous,number)))
            else:
                module.apply_labyrinth_stages(save,{0:{0:list(range(number))}})
                expect.append(('labyrinth_clear',number))
    accounts=[r for r in advanced if r['domain']=='account']
    characters=compiled.get('characters',{})
    def count(r):
        rarity=r['selector'].get('rarity')
        owned=[cat for cat in save.cats.cats if cat.unlocked]
        if rarity is None:return len(owned)
        if any(cat.id+1 not in characters for cat in owned):raise ParseError('所持キャラのレア度定義が不足しています。')
        return sum(int(characters[cat.id+1]['rarity'])==rarity for cat in owned)
    # Rarity quotas precede the total quota. Never silently relock existing units.
    counts=sorted([r for r in accounts if r['field']=='count'],key=lambda r:r['selector'].get('rarity') is None)
    fixed={r['selector']['rarity'] for r in counts if r['selector'].get('rarity') is not None}
    for r in counts:
        remaining=r['expr']['value']-count(r)
        if remaining<0:raise ParseError('既存所持数が目標より多いため停止しました。削除するキャラを勝手に選びません。')
        candidates=[cid for cid in r['ids'] if not save.cats.get_cat_by_id(cid-1).unlocked and not (r['selector'].get('rarity') is None and int(characters[cid]['rarity']) in fixed)]
        if len(candidates)<remaining:raise ParseError('指定された解放候補だけでは目標所持数に届きません。')
        for cid in candidates[:remaining]:save.cats.get_cat_by_id(cid-1).unlock(save)
    for r in accounts:
        if r['field']=='count':
            if count(r)!=r['expr']['value']:raise ParseError('所持数の指定が互いに矛盾しています。')
            continue
        cats=[save.cats.get_cat_by_id(cid-1) for cid in r['ids']]
        if any(cat is None or not cat.unlocked for cat in cats):raise ParseError('ランク調整対象に未所持キャラがあります。')
        desired=r['expr']['value'];current=save.calculate_user_rank()
        fixed_rank=current-sum(cat.upgrade.plus for cat in cats)
        capacity=sum(compiled['caps'][cat.id+1]['plus'] for cat in cats)
        needed=desired-fixed_rank
        if not 0<=needed<=capacity:raise ParseError('指定したキャラの＋値上限内では目標ランクに合わせられません。調整対象を追加してください。')
        preserve=compiled['no_decrease'] or compiled.get('cat_no_decrease',False)
        if preserve:
            remaining=desired-current
            if remaining<0:raise ParseError('ランク目標が現在より低く、減らさない指定と矛盾しています。')
            available=sum(max(0,compiled['caps'][cat.id+1]['plus']-cat.upgrade.plus) for cat in cats)
            if remaining>available:raise ParseError('減らさない条件では指定候補の＋値容量が不足します。')
            for cat in cats:
                delta=min(remaining,max(0,compiled['caps'][cat.id+1]['plus']-cat.upgrade.plus))
                cat.upgrade.plus+=delta;remaining-=delta
        else:
            for cat in cats:
                plus=min(needed,compiled['caps'][cat.id+1]['plus']);cat.upgrade.plus=plus;needed-=plus
        if save.calculate_user_rank()!=desired:raise RuntimeError('ランクの最終検証が一致しません。')
        expect.append(('rank',desired))
    # Verify native serialization before the existing worker can upload.
    if hasattr(save,'to_data'):
        from .conditional_engine import plain
        touched={r['field'] for r in compiled['rules'] if r['domain']=='resource'}
        if any(r['domain'] in ('cat','talent','account') for r in compiled['rules']):touched.add('cats')
        touched.update('ototo' if r['group']=='base_materials' else r['group'] for r in compiled['rules'] if r['domain']=='items')
        snapshots={key:plain(getattr(save,key)) for key in touched}
        version=save.game_version.game_version
        loaded=module.core.SaveFile(module.core.Data(save.to_data().data),cc=save.cc)
        if loaded.game_version.game_version!=version:raise RuntimeError('保存版数が一致しません。')
        if any(plain(getattr(loaded,key))!=expected for key,expected in snapshots.items()):raise RuntimeError('数量の保存・再読込検証が一致しません。')
        if protected is not None and loaded.lineups.serialize()!=protected:raise RuntimeError('編成の保持検証が一致しません。')
        for key,expected in expect:
            if key=='playtime':actual=loaded.officer_pass.play_time
            elif key=='gamatoto_xp':actual=loaded.gamatoto.xp
            elif key=='helpers':actual=[h.id for h in loaded.gamatoto.helpers.helpers]
            elif key=='rank':actual=loaded.calculate_user_rank()
            elif key=='labyrinth_highest':actual=module._labyrinth_value(loaded,('highest_floor','max_cleared_floor','ush5'))
            elif key=='labyrinth_clear':
                actual=module._labyrinth_value(loaded,('current_floor','cleared_floor','ush4'));actual=(actual is not None and actual>=expected);expected=True
            else:
                mid,floor=expected
                actual=all(stage.clear_times>0 for stage in loaded.tower.chapters.chapters[mid].chapters[0].stages[:floor]);expected=True
            if actual!=expected:raise RuntimeError('保存・再読込の追加指定検証が一致しません：'+key)
        for r in counts:
            rarity=r['selector'].get('rarity');actual=sum(bool(cat.unlocked) and (rarity is None or int(characters[cat.id+1]['rarity'])==rarity) for cat in loaded.cats.cats)
            if actual!=r['expr']['value']:raise RuntimeError('保存・再読込の所持数が一致しません。')
