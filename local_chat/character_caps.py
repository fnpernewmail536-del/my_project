"""Read level caps from the same JP definition as the selected characters."""
import csv
import io
import json
import re
import threading
from pathlib import Path
from .parser import ParseError

_PUBLIC_CACHE={}
_CACHE_LOCK=threading.RLock()


def valid_caps(base,plus,label):
    if isinstance(base,bool) or isinstance(plus,bool) or not isinstance(base,int) or not isinstance(plus,int) or not 1<=base<=65535 or not 0<=plus<=65535:
        raise ParseError(label+'の基本Lv・＋値上限が不正です。値を推測せず停止しました。')
    return {'base':base,'plus':plus}


def published_records(text,metadata):
    rows=list(csv.DictReader(io.StringIO(text),delimiter='\t'))
    required={'rarity','form_count','max_base_level','max_plus_level','version'}
    if not rows or not required<=set(rows[0]):raise ParseError('公開キャラ上限表の列定義を照合できません。')
    records={}
    try:
        version=max(int(row['version']) for row in rows if row['version'])
        expected=sum(int(n)*scale for n,scale in zip(metadata['data_version'].split('.'),(10000,100,1)))
        if version!=expected:raise ParseError('キャラ名と公開上限表の版数が一致しません。')
        for cid,row in enumerate(rows,1):
            caps=valid_caps(int(row['max_base_level']),int(row['max_plus_level']),'No.'+str(cid))
            rarity,count=int(row['rarity']),int(row['form_count'])
            if rarity not in range(6) or count not in (1,2,3,4):raise ValueError('identity')
            records[cid]={'id':cid,'rarity':rarity,'form_count':count,**caps}
    except (TypeError,ValueError) as exc:raise ParseError('公開キャラ上限表の数値が不正です。') from exc
    return records


def identity_record(record,info):
    cid=int(info['id']);label='No.'+str(cid)
    if record is None:raise ParseError(label+'の上限定義が同じ公開版にありません。')
    if record.get('id')!=cid or record.get('rarity')!=int(info['rarity']) or (info.get('form_count') is not None and record.get('form_count')!=int(info['form_count'])):
        raise ParseError(label+'のキャラ番号・レア度・形態数が公開上限定義と一致しません。注文書を読み直してください。')
    return valid_caps(record.get('base'),record.get('plus'),label)


def published_caps(metadata,characters,ids,fetch_public):
    revision=metadata.get('revision');version=metadata.get('data_version')
    if not isinstance(revision,str) or not re.fullmatch('[0-9a-f]{40}',revision) or not isinstance(version,str) or not re.fullmatch(r'\d+\.\d+\.\d+',version):
        raise ParseError('公開キャラ定義の版数・コミット番号が不正です。')
    key=(revision,version)
    with _CACHE_LOCK:records=_PUBLIC_CACHE.get(key)
    if records is None:
        path=Path(__file__).with_name('definitions')/'character_caps_jp_15_7_0.json'
        try:bundled=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):bundled={}
        if bundled.get('source')=='battlecatsinfo' and bundled.get('region')=='jp' and bundled.get('revision')==revision and bundled.get('data_version')==version:
            entries=bundled.get('characters',[])
            records={int(r['id']):r for r in entries}
            if len(records)!=len(entries):raise ParseError('同梱キャラ上限定義の番号が重複しています。')
        else:
            if not callable(fetch_public):raise ParseError('キャラ定義'+version+'と同じコミットの上限表がありません。対応する定義を取得してください。旧版のunitbuy.csvでは代用しません。')
            url='https://raw.githubusercontent.com/battlecatsinfo/battlecatsinfo.github.io/'+revision+'/data/cat.tsv'
            try:
                raw=fetch_public(url)
                text=raw.decode('utf-8-sig') if isinstance(raw,bytes) else raw
                records=published_records(text,metadata)
            except ParseError:raise
            except Exception as exc:raise ParseError('キャラ定義'+version+'と同じコミットの上限表を取得できません。通信を確認して再解析してください。') from exc
        # Bound this process cache; every lookup still checks the current identity.
        with _CACHE_LOCK:
            if len(_PUBLIC_CACHE)>=4:_PUBLIC_CACHE.pop(next(iter(_PUBLIC_CACHE)))
            _PUBLIC_CACHE[key]=records
    return {cid:identity_record(records.get(cid),characters[cid]) for cid in ids}


def resolve_level_caps(core,metadata,ids,getter_factory=None,fetch_public=None):
    ids=sorted(set(int(i) for i in ids))
    if not ids:return {}
    characters={int(c['id']):c for c in metadata.get('characters',[])}
    if any(cid not in characters for cid in ids):raise ParseError('上限定義の対象キャラを照合できません。')
    provenance=metadata.get('level_caps_provenance',{})
    same=metadata.get('source') in ('battlecatsinfo','bcsfe') and isinstance(metadata.get('data_version'),str) and provenance.get('region')=='jp' and provenance.get('source')==metadata.get('source') and provenance.get('data_version')==metadata.get('data_version')
    if metadata.get('source')=='battlecatsinfo':same=same and provenance.get('revision')==metadata.get('revision')
    if same and all(isinstance(characters[cid].get('level_caps'),dict) for cid in ids):
        return {cid:valid_caps(characters[cid]['level_caps'].get('base'),characters[cid]['level_caps'].get('plus'),'No.'+str(cid)) for cid in ids}
    if metadata.get('source')=='battlecatsinfo':return published_caps(metadata,characters,ids,fetch_public)
    if not callable(getter_factory):raise ParseError('キャラ上限定義の取得接続がありません。')
    getter=getter_factory();raw=getter.download('DataLocal','unitbuy.csv')
    if raw is None:raise ParseError('キャラ上限定義を取得できません。引き継ぎ取得前に停止しました。')
    rows=list(core.CSV(raw));version=getattr(getter,'version',None) or '版数不明';output={}
    for cid in ids:
        info=characters[cid]
        prefix=f'No.{cid}（キャラ定義{metadata.get("data_version","版数不明")}／上限表{version}）'
        if cid>len(rows):raise ParseError(prefix+f'：上限表はNo.{len(rows)}までで、対象行がありません。キャラ名の定義と同じ版の上限表が必要です。')
        row=rows[cid-1]
        if len(row)<=51:raise ParseError(prefix+'：基本Lv・＋値の列が不足しています。')
        if row[13].to_int()!=int(info['rarity']):raise ParseError(prefix+'：上限表のレア度が一致しません。値を推測せず停止しました。')
        output[cid]=valid_caps(row[50].to_int(),row[51].to_int(),prefix)
    return output


def get_level_caps(module,metadata,ids):
    resolver=getattr(module,'get_character_level_caps',None)
    if callable(resolver):return resolver(ids,metadata=metadata)
    return resolve_level_caps(module.core,metadata,ids,getattr(module,'_latest_jp_game_data_getter',None),getattr(module,'_game_update_get',None))


def strict_cap_issues(rules,characters,caps):
    issues=[];seen=set()
    for rule in rules:
        if rule['domain']!='cat' or not rule.get('strict_bounds') or rule['field'] not in ('base','plus'):continue
        expr=rule['expr'];field=rule['field']
        numbers=expr.get('bounds',[]) if expr['op']=='range' else [expr['value']] if expr['op']=='set' else []
        for cid in rule.get('ids',[]):
            cap=caps[cid][field]
            if not any(n<(1 if field=='base' else 0) or n>cap for n in numbers):continue
            key=(cid,field,tuple(numbers),cap)
            if key in seen:continue
            seen.add(key)
            info=characters[cid];name=(info.get('names') or [str(cid)])[0]
            requested='〜'.join(map(str,numbers));label='基本Lv' if field=='base' else '＋値'
            issues.append(f'No.{cid} {name}の{label}上限は{cap}です（指定：{requested}）。')
    return issues
