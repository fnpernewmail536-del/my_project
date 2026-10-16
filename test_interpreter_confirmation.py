"""Intent proposals and explicit, version-bound customer approval."""
import copy
import tempfile
from types import SimpleNamespace as NS
import unittest
from local_chat.interpreter import interpret_request
from local_chat.confirmation import is_confirmation_reply
from local_chat.parser import ParseError
from local_chat.engine import Engine
from local_chat.details import compile_details,apply_details
from test_detailed_requests import fixture_module,fixture_save
import test_local_chat as originals

CODES='\n引き継ぎコード:abc123456\n認証番号:0123'

class ConfirmationReplyTests(unittest.TestCase):
    def test_complete_reply_with_quotes_and_punctuation(self):
        for text in ['確認して実行', '「確認して実行」', '『確認して実行』。',
                     '"確認して実行"', '“確認して実行！”', '「\"確認して実行\"」。',
                     '  はい。  ', '『はい』', 'ＯＫ！', '"OK"', '了解', 'お願いします']:
            with self.subTest(text=text): self.assertTrue(is_confirmation_reply(text))

    def test_changes_negation_and_questions_are_not_approval(self):
        for text in ['確認して実行しない', '確認して実行？', 'はい、猫缶20000',
                     'はい\n猫缶20000', '「確認して実行」と送ればいい？', 'OK以外',
                     '猫缶MAX', '「はい', 'はい」', '', None, 1234]:
            with self.subTest(text=text): self.assertFalse(is_confirmation_reply(text))

class InterpreterTests(unittest.TestCase):
    def test_colloquial_terms_and_typos_keep_quantities(self):
        p=interpret_request('猫缶をカンストにして\nリーダシップMAX\nキャッツイ200\nキャラレベル50'+CODES)
        self.assertTrue(p['ready']);self.assertGreaterEqual(len(p['inferences']),4)
        self.assertIn('catfood',p['selected']['s1']);self.assertIn('leadership',p['selected']['s1'])
        self.assertEqual(p['custom_amounts']['custom_catseyes'],200)
        self.assertTrue(all(rule['base']==50 and rule['plus']=='keep' for rule in p['level_rules'].values()))
        self.assertTrue(p['_review_required']);self.assertNotIn('_review_confirmed',p)
    def test_only_rare_tickets_and_none_have_correct_scope(self):
        p=interpret_request('レアチケだけカンスト\n猫缶はいらない'+CODES)
        self.assertTrue(p['ready']);self.assertEqual(p['selected']['s1'],['rare_tickets'])
        self.assertNotIn('custom_catfood',p['custom_amounts'])
    def test_unknown_limitation_is_not_silently_ignored(self):
        p=interpret_request('猫缶10000、キャラ全解放、日本編だけ'+CODES)
        self.assertFalse(p['ready']);self.assertEqual(len(p['unresolved']),1)
        self.assertEqual(p['unresolved'][0]['input'],'日本編だけ')
        self.assertIn('unlock_all_cats',p['selected']['s3'])
        p=interpret_request('NP5000',p)
        self.assertFalse(p['ready']);self.assertEqual(len(p['unresolved']),1)
    def test_unresolved_can_be_corrected_or_explicitly_omitted(self):
        p=interpret_request('猫缶10000\nそれ全部いい感じに'+CODES)
        self.assertFalse(p['ready'])
        q=interpret_request('未解釈項目1=キャラレベル50',p)
        self.assertTrue(q['ready']);self.assertFalse(q['unresolved']);self.assertEqual(q['level_rules']['2']['base'],50)
        self.assertFalse(p['ready']);self.assertEqual(len(p['unresolved']),1)
        r=interpret_request('未解釈項目1不要',p)
        self.assertTrue(r['ready']);self.assertFalse(r['unresolved'])
    def test_invalid_credentials_and_numeric_values_are_not_guessed(self):
        p=interpret_request('猫缶10000');snapshot=copy.deepcopy(p)
        for text in ['引き継ぎコード:xxxxxxxxx','認証番号:12345','猫缶2147483648','メニュー82=32768']:
            with self.subTest(text=text),self.assertRaises(ParseError):interpret_request(text,p)
        self.assertEqual(p,snapshot)
    def test_negative_and_limited_text_stay_unresolved(self):
        for text in ['猫缶-3','猫缶10000にしない','プラチケ99以外']:
            with self.subTest(text=text):
                p=interpret_request(text+CODES)
                self.assertFalse(p['ready']);self.assertTrue(p['unresolved'])
    def test_engine_rejects_interpreted_draft_before_account_transfer(self):
        calls=[];p=interpret_request('猫缶10000'+CODES)
        with tempfile.TemporaryDirectory() as temp:
            engine=Engine(temp,temp);engine.module=NS(run_job_daiko=lambda *args,**kwargs:calls.append(args))
            with self.assertRaises(ParseError):engine.execute('jid','oid',p)
        self.assertFalse(calls)
    def test_numeric_level_without_plus_changes_only_base(self):
        for text in ['キャラレベル50','キャラ50','メニュー29=50','【レベル変更】\nレアレベル50']:
            with self.subTest(text=text):
                p=interpret_request(text)
                self.assertTrue(all(rule['plus']=='keep' for rule in p['level_rules'].values()))
                self.assertTrue(all('＋値は維持' in label['label'] for label in p['labels'] if label['key'].startswith('level_')))
                save=fixture_save();save.cats.cats[3].upgrade.plus=23
                apply_details(fixture_module(),save,compile_details(fixture_module(),p))
                self.assertEqual((save.cats.cats[3].upgrade.base+1,save.cats.cats[3].upgrade.plus),(50,23))
    def test_explicit_plus_and_max_remain_different(self):
        explicit=interpret_request('キャラ50+10');self.assertEqual(explicit['level_rules']['2'],{'base':50,'plus':[10,10]})
        maximum=interpret_request('メニュー29');save=fixture_save()
        apply_details(fixture_module(),save,compile_details(fixture_module(),maximum))
        self.assertTrue(all((cat.upgrade.base+1,cat.upgrade.plus)==(60,90) for cat in save.cats.cats))

class ConfirmationHttpTests(unittest.TestCase):
    setUp=originals.HttpTests.setUp;tearDown=originals.HttpTests.tearDown
    request=originals.HttpTests.request;chat=originals.HttpTests.chat;confirm=originals.HttpTests.confirm
    def reply(self, draft, text, request_id='chat-approval-id'):
        return self.request('POST','/api/chat',{'message':text,
            'confirmation_id':draft['confirmation_id'],'request_id':request_id})

    def test_typed_confirmation_and_quoted_replies_execute_reviewed_plan(self):
        for i, text in enumerate(['確認して実行', '「確認して実行」', '『はい』', '"OK"', 'ＯＫ！']):
            with self.subTest(text=text):
                draft=self.chat('猫缶10000'+CODES)[1]
                response=self.reply(draft,text,'quoted-approval-'+str(i))
                self.assertEqual(response[0],200);self.assertEqual(response[1]['kind'],'started')
                self.state.worker.submit(lambda: None).result(timeout=2)
                self.assertEqual(len(self.engine.calls),i+1)
                self.assertEqual(self.engine.calls[-1]['custom_amounts']['custom_catfood'],10000)
                self.assertFalse(self.engine.calls[-1]['unresolved'])

    def test_typed_reply_without_displayed_token_never_executes(self):
        before=self.chat('猫缶10000'+CODES)[1]
        response=self.chat('「確認して実行」')[1]
        self.assertEqual(response['kind'],'draft');self.assertTrue(response['ready'])
        self.assertEqual(response['labels'],before['labels'])
        self.assertEqual(response['confirmation_id'],before['confirmation_id'])
        self.assertFalse(response['unresolved']);self.assertFalse(self.engine.prepared)
        self.assertEqual(self.reply(response,'確認して実行')[1]['kind'],'started')

    def test_typed_reply_bound_to_stale_content_is_rejected(self):
        first=self.chat('猫缶10000'+CODES)[1]
        current=self.chat('猫缶20000')[1]
        self.assertEqual(self.reply(first,'「確認して実行」')[0],409)
        self.assertFalse(self.engine.calls);self.assertFalse(self.engine.prepared)
        self.assertEqual(self.reply(current,'確認して実行','new-chat-approval')[1]['kind'],'started')
        self.state.worker.submit(lambda: None).result(timeout=2)
        self.assertEqual(self.engine.calls[0]['custom_amounts']['custom_catfood'],20000)

    def test_typed_confirmation_without_order_or_codes_does_not_become_unknown(self):
        response=self.chat('「確認して実行」')[1]
        self.assertFalse(response['ready']);self.assertFalse(response['unresolved'])
        draft=self.chat('施設MAX')[1]
        self.assertEqual(self.reply(draft,'はい')[0],400)
        current=self.request('GET','/api/bootstrap')[1]['draft']
        self.assertEqual(current['confirmation_id'],draft['confirmation_id'])
        self.assertFalse(current['unresolved']);self.assertFalse(self.engine.prepared)

    def test_edit_in_reply_updates_draft_without_executing(self):
        draft=self.chat('猫缶10000'+CODES)[1]
        response=self.reply(draft,'はい、猫缶20000')[1]
        self.assertEqual(response['kind'],'draft')
        self.assertNotEqual(response['confirmation_id'],draft['confirmation_id'])
        self.assertFalse(self.engine.prepared)
        current=self.request('GET','/api/bootstrap')[1]['draft']
        self.assertTrue(any(item['label']=='猫缶：20,000' for item in current['labels']))
        self.assertFalse(self.engine.calls)

    def test_chat_approval_retry_and_new_reply_do_not_execute_twice(self):
        draft=self.chat('施設MAX'+CODES)[1]
        first=self.reply(draft,'「確認して実行」','reply-response-loss')[1]
        repeated=self.reply(draft,'「確認して実行」','reply-response-loss')[1]
        self.assertEqual(first['job_id'],repeated['job_id'])
        self.state.worker.submit(lambda: None).result(timeout=2)
        self.assertEqual(len(self.engine.calls),1)
        after=self.chat('「確認して実行」','reply-after-completion')[1]
        self.assertFalse(after['ready']);self.assertFalse(after['unresolved'])
        self.assertEqual(len(self.engine.prepared),1)

    def poison_old_reply(self, text='「確認して実行」'):
        draft=self.chat('猫缶10000\nプレイ時間8000〜8200'+CODES)[1]
        session=self.state.sessions[self.cookie.split('=',1)[1]]
        session['draft']=interpret_request(text,session['draft'])
        return draft

    def test_bootstrap_recovers_only_old_approval_and_requires_fresh_review(self):
        original=self.poison_old_reply()
        recovered=self.request('GET','/api/bootstrap')[1]['draft']
        self.assertTrue(recovered['ready']);self.assertFalse(recovered['unresolved'])
        self.assertEqual(recovered['labels'],original['labels'])
        self.assertNotEqual(recovered['confirmation_id'],original['confirmation_id'])
        self.assertFalse(self.engine.prepared)
        self.assertEqual(self.confirm(original)[0],409)
        self.assertEqual(self.confirm(recovered)[1]['kind'],'started')

    def test_old_bad_reply_is_restored_before_accepting_chat_approval(self):
        original=self.poison_old_reply()
        recovered=self.reply(original,'確認して実行')[1]
        self.assertEqual(recovered['kind'],'draft');self.assertTrue(recovered['ready'])
        self.assertEqual(recovered['labels'],original['labels']);self.assertFalse(self.engine.prepared)
        self.assertEqual(self.reply(recovered,'確認して実行','restored-chat-approval')[1]['kind'],'started')

    def test_button_restores_old_reply_without_executing_before_new_review(self):
        original=self.poison_old_reply()
        recovered=self.confirm(original)[1]
        self.assertEqual(recovered['kind'],'draft');self.assertFalse(self.engine.prepared)
        self.assertEqual(self.confirm(recovered)[1]['kind'],'started')

    def test_recovery_preserves_other_unresolved_limits(self):
        original=self.poison_old_reply('「確認して実行」\n日本編だけ')
        recovered=self.request('GET','/api/bootstrap')[1]['draft']
        self.assertFalse(recovered['ready'])
        self.assertEqual([item['input'] for item in recovered['unresolved']],['日本編だけ'])
        self.assertEqual(recovered['labels'],original['labels'])
        self.assertEqual(self.confirm(recovered)[0],400);self.assertFalse(self.engine.prepared)

    def test_ready_request_never_runs_without_approval_even_auto_true(self):
        data=self.request('POST','/api/chat',{'message':'猫缶10000'+CODES,'auto_run':True,'confirmed':True,'request_id':'auto-bypass-test'})[1]
        self.assertTrue(data['ready']);self.assertEqual(data['kind'],'draft')
        self.assertTrue(data['requires_confirmation']);self.assertFalse(self.engine.calls);self.assertFalse(self.engine.prepared)
        self.assertEqual(self.confirm(data)[1]['kind'],'started')
    def test_changed_content_invalidates_old_confirmation(self):
        first=self.chat('猫缶10000'+CODES)[1]
        second=self.chat('猫缶20000')[1]
        self.assertNotEqual(first['confirmation_id'],second['confirmation_id'])
        self.assertEqual(self.confirm(first)[0],409);self.assertFalse(self.engine.calls)
        self.assertEqual(self.confirm(second)[1]['kind'],'started')
        self.state.worker.shutdown(wait=True)
        self.assertEqual(self.engine.calls[0]['custom_amounts']['custom_catfood'],20000)
        self.assertTrue(self.engine.calls[0]['_review_confirmed'])
    def test_changed_credentials_require_new_confirmation(self):
        first=self.chat('施設MAX'+CODES)[1]
        second=self.chat('引き継ぎコード:fedcba123')[1]
        self.assertEqual(self.confirm(first)[0],409);self.assertFalse(self.engine.calls)
        self.confirm(second);self.state.worker.shutdown(wait=True)
        self.assertEqual(self.engine.calls[0]['transfer_code'],'fedcba123')
    def test_unresolved_cannot_be_approved(self):
        data=self.chat('猫缶10000、日本編だけ'+CODES)[1]
        self.assertFalse(data['ready']);self.assertEqual(self.confirm(data)[0],400);self.assertFalse(self.engine.calls)
        data=self.chat('未解釈項目1不要')[1]
        self.assertTrue(data['ready']);self.assertEqual(self.confirm(data)[1]['kind'],'started')
    def test_duplicate_approval_and_response_loss_execute_once(self):
        draft=self.chat('猫缶をカンストにして'+CODES,'infer-repeat-chat')[1]
        repeated=self.chat('猫缶をカンストにして'+CODES,'infer-repeat-chat')[1]
        self.assertEqual(draft['confirmation_id'],repeated['confirmation_id'])
        a=self.confirm(draft,'approval-repeat-id')[1];b=self.confirm(draft,'approval-repeat-id')[1]
        self.assertEqual(a['job_id'],b['job_id']);self.state.worker.shutdown(wait=True)
        self.assertEqual(len(self.engine.calls),1)
    def test_reset_and_other_browser_cannot_approve_old_draft(self):
        data=self.chat('施設MAX'+CODES)[1]
        original_cookie=self.cookie
        self.cookie='';self.assertEqual(self.confirm(data)[0],403)
        self.cookie=original_cookie;self.request('POST','/api/reset',{})
        self.assertEqual(self.confirm(data)[0],409);self.assertFalse(self.engine.calls)
    def test_bootstrap_restores_current_proposal_without_raw_codes(self):
        data=self.chat('キャラレベル50'+CODES)[1]
        current=self.request('GET','/api/bootstrap')[1]['draft']
        self.assertEqual(current['confirmation_id'],data['confirmation_id']);self.assertTrue(current['inferences'])
        self.assertNotIn('transfer_code',current);self.assertNotIn('auth_code',current)
        self.assertFalse(self.engine.calls)
    def test_malformed_confirmation_is_rejected(self):
        self.chat('施設MAX'+CODES)
        for value in ['',None,'あ'*64,'f'*64]:
            with self.subTest(value=value):
                status,_,_=self.request('POST','/api/confirm',{'confirmation_id':value,'request_id':'invalid-review-id'})
                self.assertEqual(status,409)
        self.assertFalse(self.engine.calls)

if __name__=='__main__':unittest.main(verbosity=2)
