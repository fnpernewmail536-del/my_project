"""Matched JP revisions, missing legacy rows, and native No.879 save roundtrips."""
import ast,copy,csv,io,json,unicodedata,hashlib,re,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from local_chat.character_caps import get_level_caps,resolve_level_caps,_PUBLIC_CACHE
from local_chat.conditional_engine import compile_conditional,apply_conditional
from local_chat.interpreter import interpret_request
from local_chat.details import compile_details,apply_details
from local_chat.parser import ParseError
from test_advanced_requests import fixture,core

ROOT=Path(__file__).resolve().parent
FIXTURE=ROOT/'testdata/published_caps_characters.json'
REVISION='4baf8c1b468230a14f9350d646f60d888e3fe75d'


def module_and_metadata():
    m,s=fixture();meta=json.loads(FIXTURE.read_text())
    m.get_character_metadata=lambda:meta
    m._latest_jp_game_data_getter=Mock(side_effect=AssertionError('old unitbuy must not be used'))
    m._apply_all_segments_unlocked=Mock()
    return m,s,meta


class CharacterCapsTests(unittest.TestCase):
    def setUp(self):_PUBLIC_CACHE.clear()

    def test_new_879_uses_pinned_definition_instead_of_old_unitbuy(self):
        m,s,meta=module_and_metadata()
        caps=get_level_caps(m,meta,[878,879,880,881,882])
        self.assertEqual(caps[879],{'base':60,'plus':70})
        self.assertEqual(caps[878],{'base':60,'plus':70})
        self.assertEqual(caps[882],{'base':50,'plus':9})
        m._latest_jp_game_data_getter.assert_not_called()

    def test_no_879_level_edit_roundtrips_without_changing_other_characters(self):
        m,s,meta=module_and_metadata()
        s.cats.cats.extend(core.Cat.init(i) for i in range(len(s.cats.cats),882))
        s.cats.cats[878].unlocked=True;s.cats.cats[878].upgrade.base=9;s.cats.cats[878].upgrade.plus=1
        before=copy.deepcopy(s.cats.serialize())
        text='## キャラレベル変更\n### B+ランク:基本Lv30、プラス値+1〜+3\n悠久山安慈'
        p=interpret_request(text,character_metadata=meta)
        self.assertFalse(p['unresolved'],p['unresolved'])
        compiled=compile_details(m,p)
        self.assertEqual(compiled['document_conditional']['caps'],{879:{'base':60,'plus':70}})
        with patch('local_chat.conditional_engine.roll',side_effect=lambda b:b[1]):apply_details(m,s,compiled)
        loaded=core.SaveFile(core.Data(s.to_data().data),cc=s.cc)
        before['cats'][878]['upgrade'].update(base=29,plus=3)
        self.assertEqual(loaded.cats.serialize(),before)
        self.assertEqual(loaded.game_version.game_version,150501)
        self.assertEqual(loaded.lineups.serialize(),s.lineups.serialize())

    def test_requested_values_above_verified_caps_still_stop(self):
        m,s,meta=module_and_metadata()
        p=interpret_request('## キャラレベル変更\n### B+ランク:基本Lv61、プラス値+1〜+3\n悠久山安慈',character_metadata=meta)
        with self.assertRaisesRegex(ParseError,'上限は60'):compile_details(m,p)
        self.assertEqual(s.cats.cats[0].upgrade.base,19)

    def test_all_bound_conflicts_are_reported_together_before_apply(self):
        m,s,meta=module_and_metadata()
        text='## キャラレベル変更\n### Aグループ:基本Lv60、プラス値+5〜+20\n暁のイザナギ\n### Bグループ:基本Lv50、プラス値+5〜+10\n比古清十郎'
        p=interpret_request(text,character_metadata=meta)
        with self.assertRaises(ParseError) as error:compile_details(m,p)
        message=str(error.exception)
        self.assertIn('3箇所',message);self.assertIn('No.732',message);self.assertIn('No.882',message)
        self.assertIn('基本Lv上限は50',message);self.assertIn('＋値上限は9',message)
        self.assertEqual(s.cats.cats[0].upgrade.base,19)

    def test_pinned_rarity_or_form_mismatch_is_not_ignored(self):
        for field,value in [('rarity',2),('form_count',3)]:
            m,s,meta=module_and_metadata();next(c for c in meta['characters'] if c['id']==879)[field]=value
            with self.assertRaisesRegex(ParseError,'No.879'):get_level_caps(m,meta,[879])

    def test_a_different_revision_cannot_use_bundled_caps_silently(self):
        m,s,meta=module_and_metadata();meta['revision']='f'*40
        with self.assertRaisesRegex(ParseError,'同じコミット'):get_level_caps(m,meta,[879])
        m._latest_jp_game_data_getter.assert_not_called()

    def test_a_new_pinned_revision_downloads_once_and_uses_jp_plus_field(self):
        m,s,meta=module_and_metadata();meta['revision']='e'*40
        header='rarity\tform_count\tmax_base_level\tmax_plus_level\tmax_plus_level_tw\tversion\n'
        text=header+''.join('4\t2\t60\t70\t99\t150700\n' for _ in range(879))
        m._game_update_get=Mock(return_value=text.encode())
        self.assertEqual(get_level_caps(m,meta,[879])[879],{'base':60,'plus':70})
        self.assertEqual(get_level_caps(m,meta,[879])[879]['plus'],70)
        m._game_update_get.assert_called_once_with('https://raw.githubusercontent.com/battlecatsinfo/battlecatsinfo.github.io/'+'e'*40+'/data/cat.tsv')

    def test_public_cap_table_version_or_columns_cannot_be_substituted(self):
        for bad in ('rarity\tform_count\tversion\n4\t2\t150700\n', 'rarity\tform_count\tmax_base_level\tmax_plus_level\tversion\n4\t2\t60\t70\t150600\n'):
            m,s,meta=module_and_metadata();meta['revision']='d'*40;m._game_update_get=Mock(return_value=bad.encode())
            with self.assertRaises(ParseError):get_level_caps(m,meta,[879])

    def test_unavailable_public_caps_stop_without_using_legacy_data(self):
        m,s,meta=module_and_metadata();meta['revision']='c'*40;m._game_update_get=Mock(side_effect=OSError('offline fixture'))
        with self.assertRaisesRegex(ParseError,'取得できません'):get_level_caps(m,meta,[879])
        m._latest_jp_game_data_getter.assert_not_called()

    def test_embedded_caps_require_matching_provenance(self):
        m,s,meta=module_and_metadata();target=next(c for c in meta['characters'] if c['id']==879)
        target['level_caps']={'base':50,'plus':2}
        self.assertEqual(get_level_caps(m,meta,[879])[879],{'base':60,'plus':70})
        meta['level_caps_provenance']={'source':meta['source'],'revision':REVISION,'data_version':meta['data_version'],'region':'jp'}
        self.assertEqual(get_level_caps(m,meta,[879])[879],{'base':50,'plus':2})
        target['level_caps']['base']=False
        with self.assertRaises(ParseError):get_level_caps(m,meta,[879])

    def test_legacy_missing_row_reports_versions_and_actual_length(self):
        m,s=fixture();meta=m.get_character_metadata();meta['data_version']='15.7.0'
        meta['characters'].append({'id':879,'names':['fixture-new'],'rarity':4,'form_count':2,'selectable':True})
        getter=m._latest_jp_game_data_getter();getter.version='15.6.0';m._latest_jp_game_data_getter=lambda:getter
        with self.assertRaises(ParseError) as error:get_level_caps(m,meta,[879])
        self.assertIn('No.879',str(error.exception));self.assertIn('15.7.0',str(error.exception));self.assertIn('15.6.0',str(error.exception));self.assertIn('No.8まで',str(error.exception))

    def test_rank_caps_also_use_the_pinned_definition(self):
        m,s,meta=module_and_metadata()
        r={'domain':'account','field':'rank','expr':{'op':'set','value':1234},'selector':{'names':['No.879']},'label':'fixture rank'}
        compiled=compile_conditional(m,{'conditional_rules':[r]})
        self.assertEqual(compiled['caps'],{879:{'base':60,'plus':70}})

    def test_main_entry_point_and_metadata_parser_emit_matching_caps(self):
        tree=ast.parse((ROOT/'main13.py').read_text())
        names={'get_character_level_caps','_parse_published_characters','_validate_character_update','_game_version_number'}
        functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
        env={'core':core,'csv':csv,'io':io,'copy':copy,'unicodedata':unicodedata,'re':re,'hashlib':hashlib,'ERROR_CAT_IDS':set(),'SUMMONED_SPIRIT_CAT_IDS':set(), '_latest_jp_game_data_getter':Mock(side_effect=AssertionError('old unitbuy')),'_game_update_get':Mock()}
        exec(compile(ast.Module(body=functions,type_ignores=[]),'caps-entry','exec'),env)
        current={'data_version':'15.6.0','rarity_names':['basic','ex','rare','super','uber','legend'],'characters':[{'id':1,'names':['fixture'],'rarity':4,'form_count':1}],'groups':[]}
        cats='rarity\tform_count\tversion\ttalents\tobtain\tmax_base_level\tmax_plus_level\n4\t1\t150700\t\t\t60\t70\n'
        stats='id\tname_jp\tname_tw\n0\tfixture\tfixture\n'
        metadata=env['_parse_published_characters'](cats,stats,[],current,REVISION)
        self.assertEqual(metadata['characters'][0]['level_caps'],{'base':60,'plus':70})
        self.assertEqual(metadata['level_caps_provenance']['revision'],REVISION)
        self.assertEqual(env['get_character_level_caps']([1],metadata),{1:{'base':60,'plus':70}})

if __name__=='__main__':unittest.main()
