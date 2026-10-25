"""Regressions for the reported poisoned conditional draft and wrong domains."""
import copy,unittest
from local_chat.interpreter import interpret_request
from local_chat.parser import parse_request
from test_advanced_requests import fixture
import test_render_chat as render_tests

class DraftReplacementTests(unittest.TestCase):
    def poisoned(self):
        p=parse_request('引き継ぎコード:abc123456\n認証番号:0123')
        p.update(conditional_mode=True,conditional_no_decrease=True,
                 conditional_rules=[{'domain':'cat','field':'base','expr':{'op':'set','value':42},'selector':{'rarity':2},'label':'誤ったレアチケットの旧ルール'}],
                 labels=[{'key':'conditional_0','label':'誤ったレアチケットの旧ルール → 基本Lv42'}],
                 unresolved=[{'input':'古い依頼','reason':'古い確認待ち'}])
        return p

    def test_document_replaces_poisoned_draft_and_keeps_only_credentials(self):
        m,_=fixture();old=self.poisoned();before=copy.deepcopy(old)
        text='''## チケット類
16.レアチケット:42
## キャラ解放
【恒常ガチャ】
超激レア:100%解放
## キャラレベル変更
### Aグループ:基本Lv30、プラス値+1〜+3
鬼にゃんま'''
        p=interpret_request(text,old,m.get_character_metadata())
        self.assertEqual(old,before)
        self.assertFalse(p.get('conditional_mode'))
        self.assertFalse(p.get('conditional_rules'))
        self.assertEqual(p['custom_amounts']['custom_rare_tickets'],42)
        self.assertEqual(p['transfer_code'],'abc123456')
        self.assertEqual(p['auth_code'],'0123')
        self.assertFalse(p['blockers'],p['blockers'])
        self.assertTrue(p['ready'])
        self.assertFalse(any('旧ルール' in x['label'] for x in p['labels']))
        self.assertFalse(any(r['domain']=='cat' and r['expr'].get('value') in (42,100) for r in p['document_rules']))
        self.assertTrue(any(r['domain']=='ownership' and r['expr']['value']==100 for r in p['document_rules']))

    def test_group_heading_without_markdown_still_uses_structured_parser(self):
        m,_=fixture()
        p=interpret_request('Aグループ:基本Lv30、プラス値+1〜+3\n鬼にゃんま',self.poisoned(),m.get_character_metadata())
        self.assertFalse(p['blockers'],p['blockers'])
        self.assertEqual(len(p['document_rules']),2)
        self.assertFalse(p.get('conditional_mode'))

    def test_conditional_fallback_never_converts_other_domains_to_levels(self):
        for text in ('激レア・レア:100%解放','EX:80%','68.隊員クラスアップ:レジェンド',
                     '隊員クラスアップ:レジェンド','隊員1人だけレジェンド','1. 「AからB」は範囲指定として扱う',
                     '※本能の定義がないキャラは、本能だけ現在のままにしてください'):
            with self.subTest(text=text):
                p=interpret_request(text,{'conditional_mode':True,'conditional_rules':[]})
                self.assertFalse(p['conditional_rules'])
                self.assertTrue(p['unresolved'])
                self.assertFalse(p['ready'])

    def test_numbered_ticket_in_conditional_mode_is_quantity_not_level(self):
        p=interpret_request('16.レアチケット:42',{'conditional_mode':True,'conditional_rules':[]})
        self.assertFalse(p['unresolved'],p['unresolved'])
        self.assertEqual([(r['domain'],r['field'],r['expr']['value']) for r in p['conditional_rules']],[('resource','rare_tickets',42)])

    def test_cleared_credentials_never_return_from_document_base(self):
        old=parse_request('引き継ぎコード:abc123456\n認証番号:0123')
        p=interpret_request('## アイテム\n4.猫缶:100',old)
        p.pop('transfer_code',None);p.pop('auth_code',None)
        revised=interpret_request('## アイテム\n4.猫缶:200',p)
        self.assertFalse(revised.get('transfer_code'))
        self.assertFalse(revised.get('auth_code'))
        self.assertIn('引き継ぎコード',revised['missing'])
        self.assertIn('認証番号',revised['missing'])

    def test_old_confirmation_id_cannot_execute_after_document_replacement(self):
        h=render_tests.ChatTests();h.setUp()
        r=h.post('chat',{'message':'NPは現在値の2倍','request_id':'old-draft-1'})
        self.assertEqual(r.status_code,200,r.json)
        r=h.post('credentials',{'transfer_code':'abc123456','auth_code':'0123'})
        old_confirmation=r.json['confirmation_id']
        r=h.post('chat',{'message':'## チケット類\n16.レアチケット:42','request_id':'new-document-1'})
        self.assertEqual(r.status_code,200,r.json)
        self.assertTrue(r.json['ready'],r.json)
        self.assertFalse(any('基本Lv' in row['label'] for row in r.json['labels']))
        fresh=r.json['confirmation_id']
        stale=h.post('confirm',{'confirmation_id':old_confirmation,'request_id':'stale-confirm'})
        self.assertEqual(stale.status_code,409)
        h.executor.submit.assert_not_called()
        confirmed=h.post('confirm',{'confirmation_id':fresh,'request_id':'fresh-confirm'})
        self.assertEqual(confirmed.status_code,200,confirmed.json)
        h.executor.submit.assert_called_once()

if __name__=='__main__':unittest.main()
