import unittest
from unittest.mock import Mock,patch
from pathlib import Path
from local_chat.structured_requests import parse_document
from local_chat.interpreter import interpret_request
from local_chat.details import compile_details,apply_details
from test_advanced_requests import fixture,core

HEADER='### Aグループ：基本Lv30、プラス値+5〜+20'
NOTE='※コラボキャラのプラス値は+5〜+7'

class InlineNoteTests(unittest.TestCase):
    def test_same_line_and_next_line_create_same_exception(self):
        for space in ('',' ','  ','\n','\r\n'):
            for markdown in (True,False):
                text=HEADER+space+NOTE+'\n鬼にゃんま、ノノ'
                if not markdown:text=text.replace('### ','')
                with self.subTest(space=repr(space),markdown=markdown):
                    doc=parse_document(text)
                    self.assertFalse(doc['issues'],doc['issues'])
                    self.assertEqual(doc['level_groups'][0]['rule']['plus']['bounds'],[5,20])
                    self.assertEqual(doc['level_groups'][0]['collaboration_plus_range'],[5,7])

    def test_inline_exception_changes_only_collab_plus_after_reload(self):
        m,s=fixture();m._apply_all_segments_unlocked=Mock()
        p=interpret_request(HEADER+' '+NOTE+'\n鬼にゃんま、ノノ',character_metadata=m.get_character_metadata())
        self.assertFalse(p['blockers'],p['blockers'])
        exception=[r for r in p['document_rules'] if r['selector'].get('collab')]
        self.assertEqual(len(exception),1)
        self.assertEqual(exception[0]['expected_ids'],[7])
        with patch('local_chat.conditional_engine.roll',side_effect=lambda b:b[1]):
            apply_details(m,s,compile_details(m,p))
        loaded=core.SaveFile(core.Data(s.to_data().data),cc=s.cc)
        self.assertEqual(loaded.cats.cats[3].upgrade.plus,20)
        self.assertEqual(loaded.cats.cats[6].upgrade.plus,7)
        self.assertEqual(loaded.cats.cats[3].upgrade.base,29)
        self.assertEqual(loaded.cats.cats[6].upgrade.base,29)

    def test_actual_order_contains_inline_collab_exception(self):
        text=Path('testdata/structured_order.txt').read_text()
        doc=parse_document(text)
        a=next(g for g in doc['level_groups'] if g['name']=='A')
        self.assertEqual(a['collaboration_plus_range'],[5,7])
        self.assertEqual(a['rule']['plus']['bounds'],[5,20])

    def test_unknown_inline_restriction_blocks_instead_of_being_dropped(self):
        m,_=fixture()
        p=interpret_request(HEADER+' ※明日以降だけ適用してください\n鬼にゃんま',character_metadata=m.get_character_metadata())
        self.assertTrue(p['unresolved'])
        self.assertFalse(p['ready'])

    def test_decoration_only_lines_are_ignored_in_every_section(self):
        for heading in ('アイテム・所持数変更','キャラ解放','キャラレベル変更','本能解放','全体の処理ルール'):
            for separator in ('⋮⋮⋮⋮','⋯⋯⋯','………','...','- ⋮⋮⋮⋮',' • … … '):
                with self.subTest(heading=heading,separator=separator):
                    doc=parse_document('## '+heading+'\n'+separator)
                    self.assertFalse(doc['issues'],doc['issues'])
                    self.assertFalse(doc['operations'])

    def test_full_order_decoration_keeps_real_blockers(self):
        text=Path('examples/現在の注文書_確認用.md').read_text()
        before=parse_document(text)
        after=parse_document(text+'\n⋮⋮⋮⋮\n')
        self.assertEqual(len(before['issues']),0)
        self.assertEqual(before,after)

    def test_instruction_with_ellipsis_still_requires_review(self):
        doc=parse_document('## 全体の処理ルール\n⋮⋮対象は後で指定する⋮⋮')
        self.assertEqual(len(doc['issues']),1)
        self.assertIn('対象は後で指定する',doc['issues'][0]['input'])

if __name__=='__main__':unittest.main()
