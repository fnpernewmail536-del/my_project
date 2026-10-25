"""Incomplete ownership must stay visible, including restored and create drafts."""
import copy,unittest
from unittest.mock import patch
from local_chat.interpreter import interpret_request
from local_chat.review_constraints import enforce_review_constraints
from local_chat.server import State
from local_chat.parser import ParseError
from test_advanced_requests import fixture

ORDER='''# 代行依頼内容
## キャラ解放
【恒常ガチャ】
超激レア:80%解放
【コラボキャラ】
コラボ系:90%解放
2020年以前を最後に開催されたコラボシリーズは、全レアリティで取得対象から除外
## アイテム・所持数変更
4.猫缶:5789
## 全体の処理ルール
6.2020年以前のコラボ除外条件は、他の解放割合より優先する。
'''


def draft():
    m,_=fixture()
    return interpret_request(ORDER,character_metadata=m.get_character_metadata()),m


def old_ready_draft():
    p,m=draft()
    p.pop('_document_source',None);p.pop('_year_exclusion_cancellable',None)
    p.update(ready=True,missing=[],blockers=[],unresolved=[],confirmation_id='a'*64)
    return p,m


class ReviewConstraintTests(unittest.TestCase):
    def test_existing_reason_is_preserved_and_not_duplicated(self):
        p,_=draft();reason=p['unresolved'][0]['reason']
        for _ in range(3):enforce_review_constraints(p)
        self.assertEqual(len(p['unresolved']),1)
        self.assertEqual(p['unresolved'][0]['reason'],reason)
        self.assertFalse(p['ready']);self.assertTrue(p['blockers'])
        self.assertTrue(p['unresolved'][0]['dismissible'])

    def test_old_ready_draft_is_repaired_without_losing_other_edits(self):
        p,_=old_ready_draft();amounts=copy.deepcopy(p['custom_amounts'])
        result=State.draft_response(p)
        self.assertFalse(result['ready']);self.assertEqual(len(result['unresolved']),1)
        self.assertFalse(result['unresolved'][0]['dismissible'])
        self.assertNotIn('よければ「確認して実行」',result['message'])
        self.assertIn('除外対象が未確定',result['message'])
        self.assertEqual(p['custom_amounts'],amounts)

    def test_old_draft_without_source_cannot_drop_the_missing_ratios(self):
        p,m=old_ready_draft();enforce_review_constraints(p)
        with self.assertRaisesRegex(ParseError,'注文書を全文再送'):
            interpret_request('未解釈項目1不要',p,m.get_character_metadata())
        self.assertTrue(p['_pending_document_ownership'])

    def test_explicit_year_cancel_reparses_original_ratios_and_preserves_credentials(self):
        p,m=draft();p.update(transfer_code='abc123456',auth_code='0123')
        p=interpret_request('未解釈項目1不要',p,m.get_character_metadata())
        self.assertFalse(p['_pending_document_ownership'])
        self.assertFalse(p['unresolved'],p['unresolved'])
        rules=[r for r in p['document_rules'] if r['domain']=='ownership']
        self.assertEqual([r['expr']['value'] for r in rules],[80,90])
        self.assertEqual([r['population_count'] for r in rules],[2,3])
        self.assertEqual([r['target_count'] for r in rules],[1,2])
        self.assertEqual(p['custom_amounts']['custom_catfood'],5789)
        self.assertEqual(p['transfer_code'],'abc123456');self.assertEqual(p['auth_code'],'0123')
        self.assertNotIn('2020年',p['_document_source'])
        self.assertTrue(any('元の解放割合' in x['suggested'] for x in p['inferences']))
        self.assertTrue(p['ready'])

    def test_unrelated_messages_do_not_clear_pending_ownership(self):
        p,m=old_ready_draft()
        p=interpret_request('猫缶6000',p,m.get_character_metadata())
        self.assertFalse(p['ready']);self.assertEqual(len(p['unresolved']),1)
        self.assertEqual(p['custom_amounts']['custom_catfood'],6000)

    def test_resending_revised_full_order_replaces_old_guard(self):
        p,m=old_ready_draft()
        text='\n'.join(line for line in ORDER.splitlines() if '2020年' not in line)
        p=interpret_request(text,p,m.get_character_metadata())
        self.assertFalse(p['_pending_document_ownership'])
        self.assertFalse(p['unresolved'])
        self.assertEqual(len([r for r in p['document_rules'] if r['domain']=='ownership']),2)

    def test_render_create_mode_keeps_blocker_and_explicit_cancel_restores_ratios(self):
        import test_render_chat
        h=test_render_chat.ChatTests();h.setUp();_,m=draft();h.m.get_character_metadata=m.get_character_metadata
        r=h.post('settings',{'mode':'create','account_type':'new','count':1});self.assertEqual(r.status_code,200,r.json)
        r=h.post('chat',{'message':ORDER,'request_id':'pending-create-01'})
        self.assertEqual(r.status_code,200,r.json);self.assertFalse(r.json['ready'])
        self.assertEqual(len(r.json['unresolved']),1)
        self.assertNotIn('よければ「確認して実行」',r.json['message'])
        first=r.json['confirmation_id']
        r=h.post('chat',{'message':'未解釈項目1不要','request_id':'cancel-year-create-01'})
        self.assertEqual(r.status_code,200,r.json);self.assertTrue(r.json['ready'],r.json)
        self.assertNotEqual(first,r.json['confirmation_id'])
        self.assertTrue(any('所持目標1体' in l['label'] for l in r.json['labels']))
        self.assertFalse(h.executor.submit.called)

    def test_render_configuration_repairs_restored_ready_true_flag(self):
        import test_render_chat
        h=test_render_chat.ChatTests();h.setUp();p,_=old_ready_draft()
        h.post('settings',{'mode':'create','account_type':'new','count':1})
        with patch('local_chat.web.interpret_request',return_value=p):
            r=h.post('chat',{'message':'猫缶5789','request_id':'restore-ready-01'})
        self.assertEqual(r.status_code,200,r.json);self.assertFalse(r.json['ready'])
        self.assertEqual(len(r.json['unresolved']),1)
        r=h.post('confirm',{'confirmation_id':r.json['confirmation_id'],'request_id':'restore-confirm-01'})
        self.assertEqual(r.status_code,400);self.assertFalse(h.executor.submit.called)

    def test_local_bootstrap_and_confirmation_repair_old_ready_draft(self):
        import test_local_chat
        h=test_local_chat.HttpTests();h.setUp()
        try:
            p,_=old_ready_draft()
            sid=h.cookie.split('=',1)[1];h.state.sessions[sid]['draft']=p
            status,r,_=h.request('GET','/api/bootstrap')
            self.assertEqual(status,200);self.assertFalse(r['draft']['ready'])
            self.assertEqual(len(r['draft']['blockers']),1)
            status,_,_=h.confirm(r['draft'])
            self.assertEqual(status,400);self.assertFalse(h.engine.prepared);self.assertFalse(h.engine.calls)
        finally:h.tearDown()

    def test_recovered_approval_reply_does_not_replace_pending_reason_with_execute_prompt(self):
        import test_local_chat
        h=test_local_chat.HttpTests();h.setUp()
        try:
            p,m=old_ready_draft();p['unresolved']=[{'input':'確認して実行','reason':'old malformed reply'}]
            sid=h.cookie.split('=',1)[1];h.state.sessions[sid]['draft']=p;h.engine.character_metadata=m.get_character_metadata()
            status,r,_=h.chat('はい')
            self.assertEqual(status,200);self.assertFalse(r['ready'])
            self.assertIn('除外対象が未確定',r['message'])
            self.assertNotIn('押してください',r['message'])
            self.assertFalse(h.engine.prepared);self.assertFalse(h.engine.calls)
        finally:h.tearDown()

if __name__=='__main__':unittest.main()
