"""Native save/reload, precedence, preservation and authenticated API tests."""
import copy
import contextlib
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from bcsfe import core
import test_vip_form_extras as fixtures
from vip_conditions.engine import apply_conditional
from vip_conditions.validation import validate


def rule(domain, field, op='set', value=1, **kw):
    expr={'op':op}
    if op not in ('max','keep'):expr['bounds' if op=='range' else 'value']=value
    return {'domain':domain,'field':field,'expr':expr,**kw}


class ConditionalTests(unittest.TestCase):
    setUpClass=classmethod(fixtures.VipFormExtraTests.setUpClass.__func__)
    save=fixtures.VipFormExtraTests.save
    reload=fixtures.VipFormExtraTests.reload

    def setUp(self):
        self.metadata={'characters':[
            {'id':i+1,'names':[f'fixture-{i+1}'],'rarity':r,'form_count':3,'selectable':True,
             'talents':[{'id':1,'name':'移動速度アップ','max_level':10},
                        {'id':2,'name':'攻撃力アップ','max_level':10}]}
            for i,r in enumerate([0,2,2,4,4,5])],
            'groups':[{'kind':'collab','name':'fixture-collab','cat_ids':[2,3,4]},
                      {'kind':'gacha','name':'fixture-gacha','cat_ids':[2,5]}]}
        rows=[]
        for info in self.metadata['characters']:
            row=[0]*52;row[13]=info['rarity'];row[50]=50;row[51]=0 if info['id']==6 else 90
            rows.append(','.join(map(str,row)))
        self.getter=Mock()
        self.getter.download.return_value=core.Data('\n'.join(rows).encode())
        self.legend={'vip_item_groups':{'battle_items':['speed','radar','bon','cpu','xp','sniper'],
                                       'catfruit':['赤マタタビ','青獣石']},
                     'talent_orbs':[{'ranks':[{'id':1,'rank':'A'},{'id':2,'rank':'S'}]}],
                     **{series:{'maps':[{'id':0,'name':'fixture-map','stages':['stage1','stage2'],'crowns':4}]}
                        for series in ('legend','true_legend','zero_legend')},
                     'event_stage_families':{'event':{'maps':[{'id':0,'name':'fixture-event','crowns':1,'stages':['stage1']}]}},
                     'facilities':[]}
        self.context=patch.dict(self.app,{'get_character_metadata':lambda:self.metadata,
                                       'get_legend_metadata':lambda:self.legend,
                                       '_latest_jp_game_data_getter':lambda:self.getter})
        self.context.start();self.addCleanup(self.context.stop)

    def compile(self,rules,**kw):
        return self.app['compile_vip_form_extras']({'vip_form_extras':{'conditional':{'rules':rules,**kw}}})

    def apply(self,save,rules,**kw):
        compiled=self.compile(rules,**kw)
        return self.app['_apply_all_segments_unlocked'](save,{},[],{'_vip_form_extras':compiled})

    def test_add_not_double_and_multiplier_cap_and_parity(self):
        s=self.save();s.catfood=50000;s.xp=11;s.rare_tickets=19
        self.apply(s,[rule('resource','catfood','add',15000),rule('resource','catfood','add',1000),
                      rule('resource','xp','mul','1.5'),
                      rule('resource','rare_tickets','add',5,when=[{'field':'rare_tickets','op':'odd'}])])
        loaded=self.reload(s)
        self.assertEqual((loaded.catfood,loaded.xp,loaded.rare_tickets),(51000,16,24))
        self.apply(s,[rule('resource','catfood','add',30000)])
        self.assertEqual(self.reload(s).catfood,58999)

    def test_cat_threshold_plus_only_collab_filler_exclusion_and_caps(self):
        s=self.save();s.cats.cats[1].upgrade.plus=22;s.cats.cats[2].upgrade.plus=7
        original=[(c.upgrade.base,c.upgrade.plus) for c in s.cats.cats]
        self.apply(s,[rule('cat','plus',value=30,selector={'rarity':2},when=[{'field':'plus','op':'lt','value':20}]),
                      rule('cat','base',value=50,selector={'rarity':4,'collab':False},when=[{'field':'base','op':'lt','value':30}]),
                      rule('cat','plus','max',selector={'rarity':5})])
        loaded=self.reload(s)
        self.assertEqual(loaded.cats.cats[1].upgrade.plus,22)
        self.assertEqual(loaded.cats.cats[2].upgrade.plus,30)
        self.assertEqual(loaded.cats.cats[2].upgrade.base,original[2][0])
        self.assertEqual(loaded.cats.cats[3].upgrade.base,original[3][0])
        self.assertEqual(loaded.cats.cats[4].upgrade.base,49)
        self.assertEqual(loaded.cats.cats[5].upgrade.plus,0)

    def test_specific_keep_beats_later_bulk_and_preserves_ui_state(self):
        s=self.save();s.order_ids=['1','2','3'];s.cats.favourites={0:True,1:False,2:True}
        before=copy.deepcopy(s.lineups.serialize());levels=[(c.upgrade.base,c.upgrade.plus) for c in s.cats.cats]
        self.apply(s,[rule('cat','base','keep',selector={'names':['No.3']}),
                      rule('cat','plus','keep',selector={'names':['No.3']}),
                      rule('cat','base',value=40,selector={'rarity':2}),
                      rule('cat','plus','add',20,selector={'rarity':2})])
        loaded=self.reload(s)
        self.assertEqual((loaded.cats.cats[2].upgrade.base,loaded.cats.cats[2].upgrade.plus),levels[2])
        self.assertEqual(loaded.cats.cats[1].upgrade.base,39)
        self.assertEqual(s.lineups.serialize(),before)
        self.assertEqual(loaded.order_ids,['1','2','3'])
        self.assertEqual(loaded.cats.favourites,s.cats.favourites)

    def test_unlock_only_does_not_edit_levels_and_locked_units_stay_untouched(self):
        s=self.save();s.menu_unlocks=[0]*3;s.cats.cats[5].unlocked=False;s.cats.cats[2].unlocked=False
        levels=[(c.upgrade.base,c.upgrade.plus) for c in s.cats.cats]
        with patch.object(core.core_data,'get_chara_drop',return_value=Mock()):
            self.apply(s,[rule('cat','unlocked',value=1,selector={'rarity':5},when=[{'field':'unlocked','op':'eq','value':0}]),
                          rule('cat','base',value=50,selector={'rarity':2})])
        loaded=self.reload(s)
        self.assertTrue(loaded.cats.cats[5].unlocked)
        self.assertFalse(loaded.cats.cats[2].unlocked)
        self.assertEqual((loaded.cats.cats[5].upgrade.base,loaded.cats.cats[5].upgrade.plus),levels[5])
        self.assertEqual((loaded.cats.cats[2].upgrade.base,loaded.cats.cats[2].upgrade.plus),levels[2])

    def test_talents_speed_keep_specific_keep_and_conditional_talent(self):
        s=self.save()
        self.app['_set_cat_talent_level'](s.cats.cats[1],1,3)
        self.app['_set_cat_talent_level'](s.cats.cats[1],2,2)
        self.apply(s,[rule('talent','talent','max',selector={'rarity':2},exclude_speed=True),
                      rule('talent','talent','keep',selector={'names':['No.3']})])
        loaded=self.reload(s)
        self.assertEqual({t.id:t.level for t in loaded.cats.cats[1].talents},{1:3,2:10})
        self.assertFalse(loaded.cats.cats[2].talents)
        self.apply(s,[rule('talent','talent','max',selector={'names':['No.2']},when=[{'field':'talent','op':'lt','value':4}])])
        self.assertEqual({t.id:t.level for t in self.reload(s).cats.cats[1].talents},{1:10,2:10})

    def test_item_specific_precedence_ranges_and_s_orbs_keep_other_ranks(self):
        s=self.save();s.battle_items.items[0].amount=7;s.battle_items.items[5].amount=400
        s.talent_orbs.set_orb(1,8);s.talent_orbs.set_orb(2,20)
        self.apply(s,[rule('items','amount','max',group='battle_items'),
                      rule('items','amount','keep',group='battle_items',names=['speed']),
                      rule('items','amount','mul','2',group='battle_items',names=['sniper']),
                      rule('orbs','amount',value=10,rank='S',when=[{'field':'amount','op':'lt','value':10}])])
        loaded=self.reload(s)
        self.assertEqual(loaded.battle_items.items[0].amount,7)
        self.assertEqual(loaded.battle_items.items[5].amount,800)
        self.assertEqual((loaded.talent_orbs.orbs[1].value,loaded.talent_orbs.orbs[2].value),(8,20))
        self.apply(s,[rule('items','amount','range',[4,8],group='battle_items')])
        self.assertTrue(all(4<=i.amount<=8 for i in self.reload(s).battle_items.items))

    def test_story_normal_treasure_and_uncleared_space_preserved(self):
        s=self.save();s.story.chapters[0].stages[0].treasure=2
        space=s.story.chapters[9].stages;space[0].clear_times=5;space[1].treasure=1
        self.apply(s,[rule('story','treasure',value=3,chapters=[0,1,2],when=[{'field':'treasure','op':'ne','value':2}]),
                      rule('story','treasure',value=3,chapters=[8],when=[{'field':'clear','op':'gt','value':0}])])
        loaded=self.reload(s)
        self.assertEqual([st.treasure for st in loaded.story.chapters[0].stages[:2]],[2,3])
        self.assertEqual((loaded.story.chapters[9].stages[0].clear_times,loaded.story.chapters[9].stages[0].treasure),(5,3))
        self.assertEqual((loaded.story.chapters[9].stages[1].clear_times,loaded.story.chapters[9].stages[1].treasure),(0,1))

    def test_crown4_and_penultimate_and_progressive_and_version(self):
        s=self.save();self.apply(s,[rule('legend','clear','keep',series='legend',stars=4)])
        s.event_stages.chapters[0].chapters[0].chapters[3].stages[0].clear_amount=5
        self.apply(s,[rule('legend','clear','atleast',1,series='legend',stars=3),
                      rule('legend','clear','atleast',1,series='true_legend',stars=1,until_penultimate=True)])
        loaded=self.reload(s)
        self.assertEqual([x.clear_amount for x in loaded.event_stages.chapters[0].chapters[0].chapters[3].stages[:2]],[5,0])
        self.assertEqual([x.clear_times for x in loaded.uncanny.chapters.chapters[0].chapters[0].stages[:2]],[1,0])
        self.apply(s,[rule('legend','clear','atleast',1,series='legend',stars=4,progressive=True)])
        self.assertEqual(self.reload(s).event_stages.chapters[0].chapters[0].chapters[3].stages[1].clear_amount,1)
        self.assertEqual(loaded.game_version.game_version,150501)

    def test_zero_crown_and_event_reload(self):
        s=self.save()
        self.apply(s,[rule('legend','clear','atleast',1,series='zero_legend',stars=1),
                      rule('event','clear','atleast',1,family='event',map_name='fixture-event')])
        loaded=self.reload(s)
        self.assertEqual(loaded.zero_legends.chapters[0].chapters[0].stages[1].clear_times,1)
        self.assertEqual(loaded.zero_legends.chapters[0].chapters[0].unlock_state,3)
        self.assertEqual(loaded.event_stages.chapters[1].chapters[0].chapters[0].stages[0].clear_amount,1)

    def test_helpers_fill_and_replace_preserve_existing_legend(self):
        s=self.save();Helper=core.game.gamoto.gamatoto.Helper
        s.gamatoto.helpers.helpers=[Helper(99),Helper(200)]+[Helper(-1) for _ in range(8)]
        delimiter=core.Delimeter.from_country_code_res(core.CountryCode.from_code('jp')).delimiter.value
        self.getter.download.return_value=core.Data(('header\n'+'\n'.join(delimiter.join(map(str,[i,0,0,'fixture','レジェンド'])) for i in range(200,212))).encode())
        self.apply(s,[rule('helpers','fill','max')])
        loaded=self.reload(s);self.assertEqual([h.id for h in loaded.gamatoto.helpers.helpers[:2]],[99,200])
        self.assertEqual(len(loaded.gamatoto.helpers.helpers),10)
        self.apply(s,[rule('helpers','legend','max')])
        self.assertEqual(self.reload(s).gamatoto.helpers.helpers[1].id,200)
        self.assertTrue(all(200<=h.id<212 for h in self.reload(s).gamatoto.helpers.helpers))

    def test_malformed_conditions_and_mixed_input_and_missing_metadata_fail_before_jobs(self):
        valid=rule('resource','catfood','add',100)
        invalid=[{**valid,'expr':{'op':'__import__','value':1}},
                 {**valid,'expr':{'op':'mul','value':'NaN'}},
                 {**valid,'expr':{'op':'range','bounds':[True,3]}},
                 {**valid,'when':[{'field':'unknown','op':'lt','value':3}]},
                 {**valid,'ids':[1]},rule('cat','unlocked',value=0),
                 {**valid,'group':'battle_items'},rule('cat','base',value=50,selector={'names':['不存在']})]
        for r in invalid:
            with self.subTest(rule=r),self.assertRaises(ValueError):self.compile([r])
        with self.assertRaisesRegex(ValueError,'解除'):
            self.app['compile_vip_form_extras']({'selected':{'s1':['catfood']},'vip_form_extras':{'conditional':{'rules':[valid]}}})
        self.getter.download.return_value=None
        with self.assertRaisesRegex(ValueError,'上限定義'):self.compile([rule('cat','base',value=50)])

    def test_auxiliary_max_actions_preserve_higher_values_and_reload(self):
        s=self.save();s.special_skills.skills[0].upgrade.base=25
        s.ototo.cannons.cannons[0]=core.game.gamoto.ototo.Cannon(0,[35])
        s.cat_shrine.xp_offering=100000001
        levels=Mock();levels.get_max_level.return_value=120
        levels.get_xp_from_level.return_value=12345
        with patch.object(core.core_data,'get_gamatoto_levels',return_value=levels):
            self.apply(s,[rule('action',field,'max') for field in
                          ('facilities','ototo','shrine','gamatoto_max')])
        loaded=self.reload(s)
        self.assertEqual(loaded.special_skills.skills[0].upgrade.base,25)
        self.assertEqual(loaded.special_skills.skills[1].upgrade.base,19)
        self.assertEqual(loaded.ototo.cannons.cannons[0].levels,[35])
        self.assertEqual(loaded.cat_shrine.xp_offering,100000001)
        self.assertEqual(loaded.gamatoto.xp,12345)

    def test_auxiliary_failure_stops_processing(self):
        with patch.object(core.core_data,'get_gamatoto_levels',side_effect=RuntimeError('fixture missing definition')):
            with self.assertRaisesRegex(RuntimeError,'fixture missing definition'):
                self.apply(self.save(),[rule('action','gamatoto_max','max')])

    def test_authenticated_api_validation_and_worker_connection(self):
        for path,handler,index in (('/api/run_daiko','api_run_daiko',7),('/api/run_create','api_run_create',4)):
            for vip,operation,expected in ((False,'add',403),(True,'invalid',400),(True,'add',200)):
                executor,reserve=Mock(),Mock(return_value=(None,None))
                payload={'transfer_code':'abcde1234','auth_code':'1234','api_key':'test','operation_id':'A'*30,
                         'vip_form_extras':{'conditional':{'rules':[rule('resource','catfood',operation,100)]}}}
                with self.app['app'].test_request_context(path,method='POST',json=payload),patch.dict(self.app,{
                    'validate_and_consume_api_key':lambda _:True,'count_inflight_jobs':lambda:0,
                    'site_vip_confirmed':lambda:vip,'executor':executor,'_reserve_site_vip_job':reserve,
                    '_create_operation':Mock(),'_create_job':Mock(),'register_job_to_session':Mock()}):
                    response=self.app[handler]()
                    self.assertEqual(self.app['app'].make_response(response).status_code,expected)
                if expected==200:
                    reserve.assert_called_once();executor.submit.assert_called_once()
                    self.assertIn('conditional',executor.submit.call_args.args[index]['_vip_form_extras'])
                else:reserve.assert_not_called();executor.submit.assert_not_called()


if __name__=='__main__':unittest.main()
