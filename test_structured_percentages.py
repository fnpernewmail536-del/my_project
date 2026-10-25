import copy,json,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from bcsfe.core.game.catbase.cat import Talent
from local_chat.interpreter import interpret_request
from local_chat.details import compile_details,apply_details
from local_chat.named_characters import NameIndex
from local_chat.parser import parse_request,ParseError
from test_advanced_requests import fixture,core
import test_render_chat as render_tests


def prepare(module):module._apply_all_segments_unlocked=Mock()


class PercentageTests(unittest.TestCase):
    def test_ownership_counts_existing_and_roundtrips_without_touching_levels(self):
        m,s=fixture();prepare(m)
        for c in s.cats.cats:c.unlocked=False
        # One of two targets is already owned; 80% rounds down to one.
        s.cats.cats[3].unlocked=True
        p=interpret_request('## キャラ解放\n【恒常ガチャ】\n超激レア:全対象の80%を解放',character_metadata=m.get_character_metadata())
        self.assertFalse(p['blockers'],p['blockers'])
        self.assertIn('対象2体・所持目標1体',p['labels'][0]['label'])
        compiled=compile_details(m,p)
        before=[(c.upgrade.base,c.upgrade.plus) for c in s.cats.cats]
        with patch.object(core.core_data,'get_chara_drop',return_value=Mock()):apply_details(m,s,compiled)
        loaded=core.SaveFile(core.Data(s.to_data().data),cc=s.cc)
        self.assertEqual([i+1 for i,c in enumerate(loaded.cats.cats) if c.unlocked],[4])
        self.assertEqual([(c.upgrade.base,c.upgrade.plus) for c in loaded.cats.cats],before)

    def test_new_ownership_sample_is_within_population(self):
        m,s=fixture();prepare(m)
        for c in s.cats.cats:c.unlocked=False
        p=interpret_request('## キャラ解放\n【恒常ガチャ】\n超激レア:全対象の80%を解放',character_metadata=m.get_character_metadata())
        with patch.object(core.core_data,'get_chara_drop',return_value=Mock()):apply_details(m,s,compile_details(m,p))
        ids={i+1 for i,c in enumerate(s.cats.cats) if c.unlocked}
        self.assertEqual(len(ids),1);self.assertTrue(ids<={4,5})

    def test_existing_above_target_stops_without_mutation(self):
        m,s=fixture();prepare(m)
        p=interpret_request('## キャラ解放\n【恒常ガチャ】\n超激レア:全対象の80%を解放',character_metadata=m.get_character_metadata())
        before=copy.deepcopy(s.cats.serialize())
        with self.assertRaisesRegex(ParseError,'削除せず停止'):
            apply_details(m,s,compile_details(m,p))
        self.assertEqual(s.cats.serialize(),before)

    def test_narrow_100_percent_exception_partitions_broad_quota(self):
        m,s=fixture();prepare(m)
        for c in s.cats.cats:c.unlocked=False
        p=interpret_request('## キャラ解放\n【コラボキャラ】\nコラボ系:0%解放\nコラボのレア:100%解放',character_metadata=m.get_character_metadata())
        self.assertFalse(p['blockers'],p['blockers'])
        with patch.object(core.core_data,'get_chara_drop',return_value=Mock()):apply_details(m,s,compile_details(m,p))
        self.assertEqual({i+1 for i,c in enumerate(s.cats.cats) if c.unlocked},{7,8})

    def test_talent_percentage_means_each_max_level_and_persists(self):
        m,s=fixture();prepare(m);meta=m.get_character_metadata()
        # Synthetic talent definitions, never external accounts.
        for c in meta['characters']:c['talents']=[{'id':1,'name':'攻撃力アップ','max_level':10},{'id':2,'name':'体力アップ','max_level':3}]
        def put(cat,tid,v):
            if cat.talents is None:cat.talents=[]
            t=next((t for t in cat.talents if t.id==tid),None)
            if t:t.level=v
            else:cat.talents.append(Talent(tid,v))
        m._set_cat_talent_level=put
        text='''## 本能解放
以下のキャラは、本能の80%を解放してください。
鬼にゃんま
【その他の本能解放割合】
上記以外の超激レア:60%
激レア:100%'''
        p=interpret_request(text,character_metadata=meta)
        self.assertFalse(p['blockers'],p['blockers'])
        with patch.object(core.core_data,'get_chara_drop',return_value=Mock()):apply_details(m,s,compile_details(m,p))
        loaded=core.SaveFile(core.Data(s.to_data().data),cc=s.cc)
        levels=lambda cid:{t.id:t.level for t in loaded.cats.cats[cid-1].talents or []}
        self.assertEqual(levels(4),{1:8,2:2})
        self.assertEqual(levels(5),{1:6,2:1})
        self.assertEqual(levels(2),{1:10,2:3})
        self.assertEqual(levels(1),{})

    def test_same_document_replaces_prior_values_without_label_duplicates(self):
        text='## アイテム\n4.猫缶:5000\n61.施設レベル:Lv30\n## レベル変更\nレア:基本Lv30、プラス値+2〜+6'
        m,_=fixture();p=interpret_request(text,character_metadata=m.get_character_metadata())
        p=interpret_request('引き継ぎコード:abc123456\n認証番号:0123',p,m.get_character_metadata())
        for _ in range(3):p=interpret_request(text,p,m.get_character_metadata())
        self.assertEqual(len(p['document_rules']),2)
        self.assertEqual(len([x for x in p['labels'] if x['key']=='custom_base_upgrades']),1)
        p=interpret_request(text.replace('基本Lv30','基本Lv40').replace('5000','6000'),p,m.get_character_metadata())
        self.assertEqual(len(p['document_rules']),2)
        self.assertEqual(p['document_rules'][0]['expr']['value'],40)
        self.assertEqual(p['custom_amounts']['custom_catfood'],6000)
        self.assertEqual(p['auth_code'],'0123')

    def test_playtime_clock_and_invalid_minutes(self):
        p=interpret_request('## その他の仕様\n84.プレイ時間:4363時間42分')
        self.assertEqual(p['document_rules'][0]['expr']['value'],4363*3600+42*60)
        self.assertFalse(p['unresolved'])
        for text in ('メニュー84=4363:60:0','メニュー84=10:2:60'):
            with self.assertRaises(ParseError):parse_request(text)

    def test_structured_playtime_seconds_survive_actual_save_reload(self):
        m,s=fixture();prepare(m)
        p=interpret_request('## その他の仕様\n84.プレイ時間:4363時間42分17秒')
        apply_details(m,s,compile_details(m,p))
        loaded=core.SaveFile(core.Data(s.to_data().data),cc=s.cc)
        self.assertEqual(loaded.officer_pass.play_time,core.PlayTime.from_hours_mins_secs(4363,42,17).frames)

    def test_catalogue_variations_annotations_and_explicit_formal_priority(self):
        index=NameIndex(json.loads(Path('testdata/character_name_cases.json').read_text()))
        cases={'デストロイ(超竜戦機デストロイ)':633,'伯爵真田(伯爵真田幸村)':684,
               'レイ(閃雷機兵レイ)':287,'モモコ(ワンダーモモコ)':456,'第6使徒':550,
               '煉獄杏寿郎':847,'さくら(風隼さくら)':398,'初号機(エヴァ初号機のアイコン)':414,
               '白ガル(幼獣ガルのアイコン)':586,'竈門禰豆子':842}
        for q,cid in cases.items():self.assertEqual(set(index.exact(q)),{cid},q)
        self.assertFalse(index.exact('存在しない新キャラ'))

    def test_definition_changed_between_review_and_confirm_stops(self):
        m,s=fixture();prepare(m)
        p=interpret_request('## キャラ解放\n超激レア:100%解放',character_metadata=m.get_character_metadata())
        m.get_character_metadata()['characters'][3]['rarity']=3
        with self.assertRaisesRegex(ParseError,'定義が変わりました'):compile_details(m,p)

    def test_explicit_keep_unknown_talents_preserves_saved_values_after_reload(self):
        m,s=fixture();prepare(m);meta=m.get_character_metadata()
        meta['characters'][3]['talents']=[{'id':1,'name':'攻撃力アップ','max_level':10}]
        s.cats.cats[6].talents=[Talent(33,7)]
        m._set_cat_talent_level=lambda cat,tid,v:setattr(cat,'talents',[Talent(tid,v)])
        text='''## 本能解放
以下のキャラは、本能の80%を解放してください。
鬼にゃんま、ノノ
※本能の定義が取得できないキャラは、本能だけ現在のままにしてください。'''
        p=interpret_request(text,character_metadata=meta)
        self.assertFalse(p['unresolved'],p['unresolved'])
        self.assertEqual(p['document_rules'][0]['expected_ids'],[4])
        # A definition appearing later must not silently add a new target.
        meta['characters'][6]['talents']=[{'id':33,'name':'新たな定義','max_level':10}]
        apply_details(m,s,compile_details(m,p))
        loaded=core.SaveFile(core.Data(s.to_data().data),cc=s.cc)
        self.assertEqual([(t.id,t.level) for t in loaded.cats.cats[3].talents],[(1,8)])
        self.assertEqual([(t.id,t.level) for t in loaded.cats.cats[6].talents],[(33,7)])
        self.assertIn('本能のみ現在のまま保持',p['labels'][-1]['label'])

    def test_keep_all_unknown_talents_is_not_an_edit(self):
        m,_=fixture()
        p=interpret_request('## 本能解放\n以下のキャラは、本能の80%を解放してください。\nノノ\n本能の定義がないキャラは、本能だけ現在のまま',character_metadata=m.get_character_metadata())
        self.assertFalse(p['document_rules'])
        self.assertIn('代行内容',p['missing'])

    def test_both_b_rank_groups_use_level_30_plus_one_to_three(self):
        m,s=fixture();prepare(m)
        text='## レベル変更\n### B+ランク：基本Lv30、プラス値+1〜+3\n鬼にゃんま\n### Bランク：基本Lv30、プラス値+1〜+3\nノノ'
        p=interpret_request(text,character_metadata=m.get_character_metadata())
        self.assertFalse(p['blockers'],p['blockers'])
        apply_details(m,s,compile_details(m,p))
        loaded=core.SaveFile(core.Data(s.to_data().data),cc=s.cc)
        for i in (3,6):
            self.assertEqual(loaded.cats.cats[i].upgrade.base,29)
            self.assertTrue(1<=loaded.cats.cats[i].upgrade.plus<=3)

class PercentageHttpTests(unittest.TestCase):
    def test_native_worker_receives_exact_playtime(self):
        h=render_tests.ChatTests();h.setUp();h.m.core=core
        r=h.post('chat',{'message':'## その他の仕様\n84.プレイ時間:4363時間42分','request_id':'time-doc-1'})
        self.assertEqual(r.status_code,200,r.json)
        r=h.post('credentials',{'transfer_code':'abc123456','auth_code':'0123'})
        self.assertTrue(r.json['ready'],r.json)
        r=h.post('confirm',{'confirmation_id':r.json['confirmation_id'],'request_id':'time-confirm-1'})
        self.assertEqual(r.status_code,200,r.json)
        compiled=h.executor.submit.call_args.kwargs['chat_execution']['compiled']['document_conditional']
        self.assertEqual(compiled['rules'][0]['expr']['value'],4363*3600+42*60)

if __name__=='__main__':unittest.main()
