"""Carry earlier conventional drafts into conditional evaluation without losing edits."""
from .menus import MAX_ITEM_AMOUNTS

def migrate(plan):
    if plan.get('conditional_mode'):return [],[]
    rules=[];issues=[]
    def add(domain,field,expr,**kw):rules.append({'domain':domain,'field':field,'expr':expr,'label':'以前の指定：'+field,**kw})
    def expr(v):return {'op':'keep' if v=='keep' else 'max'} if v in ('keep','max') else {'op':'range','bounds':list(v)} if isinstance(v,(list,tuple)) else {'op':'set','value':int(v)}
    for group,keys in plan.get('selected',{}).items():
        for raw in keys:
            key=raw.removeprefix('custom_');v=plan.get('custom_amounts',{}).get(raw,MAX_ITEM_AMOUNTS.get(key))
            if key in ('catfood','xp','np','leadership','normal_tickets','rare_tickets','platinum_tickets','legend_tickets'):
                add('resource',key,expr(v),priority=3)
            elif key in ('battle_items','catseyes','catamins','base_materials','matatabi'):
                add('items','amount',expr(v),group='catfruit' if key=='matatabi' else key)
            elif key=='talent_orbs':add('orbs','amount',expr(v))
            elif key=='playtime' and plan.get('custom_playtime'):pass
            elif key=='gamatoto_level' and plan.get('gamatoto_level') is not None:pass
            elif key=='unlock_all_cats':add('cat','unlocked',{'op':'set','value':1},selector={})
            elif key in ('max_all_cats','max_all_cats_plus'):
                add('cat','base',{'op':'max'},selector={})
                if key=='max_all_cats_plus':add('cat','plus',{'op':'max'},selector={})
            elif key in ('legend_clear','true_legend_clear','zero_legend_clear'):
                add('legend','clear',{'op':'atleast','value':1},series=key.removesuffix('_clear'),stars=4)
            elif key=='main_story_clear':
                add('story','clear',{'op':'atleast','value':1},chapters=list(range(9)))
                add('story','treasure',{'op':'set','value':3},chapters=list(range(9)))
            elif key=='all_stage_clear':
                add('story','clear',{'op':'atleast','value':1},chapters=list(range(9)))
                add('story','treasure',{'op':'set','value':3},chapters=list(range(9)))
                for series in ('legend','true_legend','zero_legend'):add('legend','clear',{'op':'atleast','value':1},series=series,stars=4)
                for family in ('event','aku'):add('event','clear',{'op':'atleast','value':1},family=family)
            elif key=='zombie_clear':add('action','zombies',{'op':'max'})
            elif key in ('aku_clear','event_clear'):add('event','clear',{'op':'atleast','value':1},family='aku' if key=='aku_clear' else 'event')
            elif key in ('gamatoto_max','ototo_max','cat_shrine_max','base_upgrades_max','remove_error_cats'):
                add('action',{'gamatoto_max':'gamatoto_max','ototo_max':'ototo','cat_shrine_max':'shrine','base_upgrades_max':'facilities','remove_error_cats':'remove_errors'}[key],{'op':'max'})
            else:issues.append({'input':key,'reason':'以前の指定を条件付き処理へ引き継げません。この項目を別依頼に分けるか、不要と明示してください。'})
    for rarity,rule in plan.get('level_rules',{}).items():
        for field in ('base','plus'):add('cat',field,expr(rule[field]),selector={'rarity':int(rarity)})
        if rule.get('collab_plus'):add('cat','plus',expr(rule['collab_plus']),selector={'rarity':int(rarity),'collab':True})
    for scope,v in plan.get('plus_only_rules',{}).items():
        sel={}
        if scope.startswith('rarity:'):sel['rarity']=int(scope.split(':')[1])
        if scope.startswith('collab'):
            sel['collab']=True
            if ':' in scope:sel['rarity']=int(scope.split(':')[1])
        add('cat','plus',expr(v),selector=sel)
    for rarity in plan.get('unlock_rarities',[]):add('cat','unlocked',{'op':'set','value':1},selector={'rarity':int(rarity)})
    for query in plan.get('unlock_queries',[]):add('cat','unlocked',{'op':'set','value':1},selector={'names':[query]})
    for query in plan.get('level_exclusion_queries',[]):
        for field in ('base','plus'):add('cat',field,{'op':'keep'},selector={'names':[query]})
    if plan.get('talents_max'):add('talent','talent',{'op':'max'},selector={})
    if plan.get('orb_s_amount') is not None:add('orbs','amount',expr(plan['orb_s_amount']),rank='S')
    if plan.get('helpers_legend'):add('helpers','legend',{'op':'set','value':10})
    if plan.get('custom_playtime'):
        parts=list(map(int,plan['custom_playtime'].split(':')))
        add('profile','playtime',{'op':'set','value':parts[0]*3600+parts[1]*60+(parts[2] if len(parts)>2 else 0)})
    if plan.get('gamatoto_level') is not None:add('profile','gamatoto_level',{'op':'set','value':int(plan['gamatoto_level'])})
    for key in ('future_score_settings','dojo_score_settings','labyrinth_amount','fruit_filters'):
        if plan.get(key):issues.append({'input':key,'reason':'以前の詳細指定を条件付き処理へ引き継げません。別依頼に分けるか不要と指定してください。'})
    for reason in plan.get('menu_pending',{}).values():issues.append({'input':'以前の確認待ち','reason':reason})
    return rules,issues

def display(rule):
    expr=rule['expr'];op=expr['op']
    value={'keep':'現在の値を保持','max':'上限まで','set':str(expr.get('value','')),'range':('〜'.join(map(str,expr.get('bounds',[])))+'から個別に抽選'),'add':('現在値に'+str(expr.get('value',''))+'追加'),'mul':('現在値の'+str(expr.get('value',''))+'倍・切り捨て'),'atleast':(str(expr.get('value',''))+'以上へ')}.get(op,op)
    fields={'base':'基本Lv','plus':'＋値','unlocked':'解放状態','treasure':'お宝','clear':'クリア状況','talent':'本能','amount':'数量','forms':'解放形態','playtime':'プレイ時間','gamatoto_level':'ガマトトLv','rank':'ユーザーランク（指定キャラの＋値で調整）','count':'所持数（指定候補だけ解放）','highest':'最高到達階','composition':'隊員構成'}
    if rule['field']=='playtime' and op=='set':
        seconds=int(expr['value']);value=f'{seconds//3600}時間{seconds//60%60}分{seconds%60}秒'
    if rule['domain']=='helper_mix':value='・'.join(f'{key}{count}体' for key,count in rule['composition'].items())+('・既存非レジェンド'+str(rule['keep_nonlegend'])+'体を保持' if rule.get('keep_nonlegend') else '')
    conditional='（変更前の値で条件判定）' if rule.get('when') else ''
    return rule.get('label',rule['field'])+' → '+fields.get(rule['field'],rule['field'])+'：'+value+conditional
