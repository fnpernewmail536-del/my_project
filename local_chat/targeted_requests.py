"""Explicit scopes for structured requests; missing history is never guessed."""
import datetime
import json
import re
from pathlib import Path
from .parser import ParseError
from .named_characters import name_key
from .characters import collaboration_ids


def chapters(text):
    text=re.sub(r'\s+|第','',text)
    if text in ('本編9章','本編全9章','日本編・未来編・宇宙編の全章','日本編・未来編・宇宙編全章'):
        return list(range(9))
    result=[]
    for part in text.split('・'):
        m=re.fullmatch(r'(日本編|未来編|宇宙編)(?:の)?(?:全(?:3)?章|([123])章)',part)
        if not m:return None
        start={'日本編':0,'未来編':3,'宇宙編':6}[m[1]]
        result.extend([start+int(m[2])-1] if m[2] else range(start,start+3))
    return sorted(set(result)) or None


def scoped_menu_operation(number,arg,source):
    if number in (42,44):
        m=re.fullmatch(r'(?:対象[:=])?(.+?)[、,](?:(?:回数|スコア)[:=]|各)?(\d+)(?:[〜~～-]|から)(\d+)(?:回)?',arg)
        if not m:return None
        scope=m[1]
        if number==44 and scope in ('全3章','未来編全3章','未来編の全3章'):scope='未来編全章'
        ids=chapters(scope)
        if ids is None or (number==44 and not set(ids)<=set((3,4,5))):return None
        low,high=int(m[2]),int(m[3]);cap=9999 if number==44 else 32767
        if not 0<=low<=high<=cap:raise ParseError('回数・スコアの範囲が不正です。上限は'+str(cap)+'です。')
        return {'kind':'story','selector':{},'chapters':ids,'rule':{'score' if number==44 else 'clear':{'op':'range','bounds':[low,high]}},'source':source}
    if number==59:
        if arg in ('読み込めた全イベント','取得済み定義の全イベント','全イベント'):
            return {'kind':'event_all','selector':{},'families':['all'],'rule':{'clear':{'op':'atleast','value':1}},'source':source}
    if number in (67,68):
        m=re.fullmatch(r'(?:合計|全)(\d+)(?:人|体)[、,](?:全員)?(一人前|熟練|カリスマ|レジェンド)',arg)
        if not m:return None
        count=int(m[1])
        if not 1<=count<=10:raise ParseError('隊員の合計人数は1〜10人で指定してください。')
        return {'kind':'helper_mix','selector':{},'composition':{m[2]:count},'rule':{'composition':{'op':'set','value':count}},'source':source}
    return None


def history_data(metadata):
    history=(metadata or {}).get('collaboration_history')
    if history is None:
        path=Path(__file__).with_name('collaboration_history.json')
        if path.exists():
            try:history=json.loads(path.read_text(encoding='utf-8'))
            except (OSError,ValueError) as exc:raise ParseError('コラボ開催履歴ファイルを読み取れません。') from exc
    return history


def ownership_exclusions(doc,metadata):
    cutoffs=doc.get('collab_cutoffs',[]);explicit=doc.get('excluded_collab_names',[])
    if not cutoffs and not explicit:return [],[],[]
    source='コラボ取得対象の除外'
    def failure(reason):return [],[],[{'input':source,'reason':'【定義確認】'+reason,'category':'metadata'}]
    if not metadata:return failure('コラボの所属定義を読み込んだ後、注文書を送り直してください。')
    try:
        collabs=collaboration_ids(metadata,metadata['characters'])
        groups=[g for g in metadata.get('groups',[]) if g.get('kind')=='collab']
        excluded=set();labels=[]
        for query in explicit:
            key=name_key(query)
            matches=[g for g in groups if key in (name_key(g.get('id','')),name_key(g.get('name','')),name_key(re.sub(r'[「」『』]|コラボガチャ|コラボ','',g.get('name',''))))]
            if len(matches)!=1:raise ParseError('除外コラボを一意に特定できません：'+query)
            ids={int(i) for i in matches[0].get('cat_ids',[])}&collabs
            if not ids:raise ParseError('除外コラボの対象キャラ定義がありません：'+query)
            excluded.update(ids);labels.append('取得対象から除外（既存所持は保持）：'+matches[0]['name'])
        if cutoffs:
            years={int(c['year']) for c in cutoffs}
            if len(years)!=1:raise ParseError('コラボ除外の年指定が一致しません。')
            cutoff=next(iter(years));history=history_data(metadata)
            today=datetime.date.today()
            if not isinstance(history,dict) or history.get('as_of')!=today.isoformat() or not isinstance(history.get('series'),dict):
                raise ParseError('最終開催年を確定できる履歴が未登録です。確認済みの除外コラボ名を指定するか、基準日付きの開催履歴を登録してください。割合計算より先に照合し、既存所持は削除しません。')
            records=history['series'];membership={cid:[] for cid in collabs-excluded};missing=[]
            for group in groups:
                ids={int(i) for i in group.get('cat_ids',[])}&set(membership)
                if not ids:continue
                gid=str(group.get('id',''));record=records.get(gid,{})
                year=record.get('last_held_year');proof=record.get('source')
                if isinstance(year,bool) or not isinstance(year,int) or not 1900<=year<=today.year or not isinstance(proof,str) or not proof.strip():
                    missing.append(group.get('name',gid));continue
                for cid in ids:membership[cid].append(year)
            if missing:raise ParseError('最終開催年・確認元が不足しています：'+'、'.join(missing))
            uncovered=[cid for cid,years in membership.items() if not years]
            if uncovered:raise ParseError('最終開催年と所属を照合できないキャラがあります：'+','.join('No.'+str(i) for i in sorted(uncovered)))
            excluded.update(cid for cid,years in membership.items() if max(years)<=cutoff)
            labels.append(f'{cutoff}年以前を最後に開催したコラボ：{len(excluded)}体を解放割合の母集団から除外（登録履歴の基準日：{history["as_of"]}・既存所持は保持）')
        labels.append('取得除外キャラ：'+('・'.join('No.'+str(i) for i in sorted(excluded)) or '該当なし'))
        return ['No.'+str(i) for i in sorted(excluded)],labels,[]
    except ParseError as exc:return failure(str(exc))
