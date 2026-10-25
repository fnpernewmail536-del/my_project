"""Rendered Markdown copies retain the same operation scopes as raw text."""
import copy,re,unittest
from pathlib import Path
from local_chat.structured_requests import parse_document,is_document
from local_chat.interpreter import interpret_request
from test_structured_requests import ORDER
from test_advanced_requests import fixture
import test_render_chat as render_tests

def rendered(text,brackets=True,numbering=True):
    text=re.sub(r'(?m)^#{1,4}\s*','',text)
    if not brackets:text=text.translate(str.maketrans('','','【】《》'))
    if not numbering:text=re.sub(r'(?m)^\d+[.]\s+(?=「|キャラ|同名|個別|数値|処理|解釈|2020)','',text)
    return text

def commands(doc):
    return [{k:v for k,v in op.items() if k!='source'} for op in doc['operations']]

class RenderedOrderTests(unittest.TestCase):
    def test_short_order_all_rendered_variants_keep_semantics(self):
        baseline=parse_document(ORDER)
        for brackets in (True,False):
            for numbering in (True,False):
                text=rendered(ORDER,brackets,numbering)
                with self.subTest(brackets=brackets,numbering=numbering):
                    self.assertTrue(is_document(text))
                    doc=parse_document(text)
                    self.assertEqual(commands(doc),commands(baseline))
                    self.assertEqual(doc['summary'],baseline['summary'])
                    self.assertEqual(len(doc['issues']),len(baseline['issues']))

    def test_full_user_order_rendered_copy_keeps_seven_ownership_rules(self):
        text=Path('testdata/structured_order_rendered.txt').read_text()
        baseline=parse_document(Path('examples/現在の注文書_確認用.md').read_text())
        for copy_text in (text,rendered(text,False,False),text.replace('\n','\r\n')):
            d=parse_document(copy_text)
            self.assertEqual(commands(d),commands(baseline))
            self.assertEqual(len(d['issues']),0)
            self.assertEqual(len([x for x in d['operations'] if x['kind']=='ownership']),7)
            self.assertEqual([len(g['characters']) for g in d['level_groups']],[51,73,55,38,69])
            self.assertFalse(any('見出し' in x['reason'] for x in d['issues']))

    def test_other_level_and_next_chapter_headers_never_become_character_names(self):
        text='''3 キャラレベル変更
Aグループ:基本Lv30、プラス値+1〜+3
鬼にゃんま
その他のレベル指定
レア:基本Lv20、プラス値+2〜+4
4 ステージ・お宝・スコア
ステージクリア
38.全ステージクリア'''
        d=parse_document(text)
        self.assertEqual([c['name'] for c in d['level_groups'][0]['characters']],['鬼にゃんま'])
        self.assertFalse(d['issues'],d['issues'])

    def test_heading_detection_requires_whole_title(self):
        d=parse_document('1 アイテム・所持数変更\n1 猫缶:999\n2 キャラ解放ではなく保留')
        self.assertFalse(d['operations'])
        self.assertEqual(len(d['issues']),2)

    def test_existing_short_named_unlock_header_keeps_legacy_behavior(self):
        m,_=fixture()
        text='キャラ解放\n鬼にゃんま'
        self.assertFalse(is_document(text))
        p=interpret_request(text,character_metadata=m.get_character_metadata())
        self.assertFalse(p['unresolved'],p['unresolved'])
        self.assertEqual([x['id'] for x in p['unlock_resolved']],[4])

    def test_plain_numbered_headings_recover_old_conditional_http_draft(self):
        h=render_tests.ChatTests();h.setUp()
        h.post('chat',{'message':'NPは現在値の2倍','request_id':'render-old-1'})
        h.post('credentials',{'transfer_code':'abc123456','auth_code':'0123'})
        r=h.post('chat',{'message':'1 アイテム・所持数変更\n16.レアチケット:42','request_id':'render-new-1'})
        self.assertEqual(r.status_code,200,r.json)
        self.assertTrue(r.json['ready'],r.json)
        self.assertFalse(r.json['unresolved'])
        self.assertEqual([x['label'] for x in r.json['labels']],['レアチケット：42'])

if __name__=='__main__':unittest.main()
