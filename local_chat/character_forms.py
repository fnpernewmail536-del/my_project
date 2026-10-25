"""Explicit third-form selection, separate from ownership and level changes."""
import copy
import re
import unicodedata
from .parser import ParseError

REQUEST = '第3形態にできるキャラは第3形態にしてください。'


def is_third_form_request(text):
    value = re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))
    value = value.replace('第三形態', '第3形態').rstrip('。')
    return bool(re.fullmatch(
        r'第3形態に(?:できる|対応している)(?:キャラクター|キャラ)(?:は|を)'
        r'(?:全て|すべて)?第3形態に(?:してください|して|したい|する)', value))


def form_specs(characters, ids):
    specs = {}
    for cid in ids:
        info = characters[cid]
        count, state = info.get('form_count'), info.get('true_form_state')
        if type(count) is not int or count not in (3, 4) or type(state) is not int or state not in (0, 2):
            raise ParseError(f'No.{cid}の第3形態・進化権の定義を確認できません。値を推測せず停止しました。')
        specs[str(cid)] = {'form_count': count, 'true_form_state': state}
    return specs


def freeze_form_rule(rule, metadata):
    if rule.get('domain') != 'cat' or rule.get('field') != 'form':
        return
    characters = {int(c['id']): c for c in metadata['characters']}
    rule['expected_form_specs'] = form_specs(characters, rule['expected_ids'])


def append_to_document(previous, metadata):
    """Add one operation without migrating or discarding the rest of the order."""
    from .structured_requests import interpret_document
    from .conditional_migration import display
    if not metadata:
        raise ParseError('キャラ定義を読み込んでから形態を指定してください。')
    addition = interpret_document('## キャラレベル変更\n' + REQUEST, None, metadata)
    if addition['unresolved']:
        raise ParseError(addition['unresolved'][0]['reason'])
    rule = next(r for r in addition['document_rules'] if r['field'] == 'form')
    plan = copy.deepcopy(previous)
    old_labels = [display(r) for r in plan['document_rules'] if r['domain'] == 'cat' and r['field'] == 'form']
    plan['document_rules'] = [r for r in plan['document_rules']
                              if not (r['domain'] == 'cat' and r['field'] == 'form')]
    plan['document_rules'].append(rule)
    plan['labels'] = [x for x in plan['labels'] if x['key'] != 'document_third_form'
                      and not any(x['label'].startswith(label) for label in old_labels)]
    plan['labels'].append({'key': 'document_third_form', 'label': display(rule)})
    source = plan.get('_document_source', '')
    if source and not any(is_third_form_request(line) for line in source.splitlines()):
        plan['_document_source'] = source.rstrip() + '\n' + REQUEST + '\n'
        if len(plan['_document_source']) > 16000:
            raise ParseError('形態指定を追加すると16000文字を超えます。注文書を短くして全文を再送してください。')
    plan['_review_required'] = True
    plan.pop('_review_confirmed', None)
    return plan


def verify_forms(module, save, pending):
    ids = {int(key.split(':')[1]) for key in pending if key.endswith(':current_form')}
    if not ids:
        return
    if not callable(getattr(save, 'to_data', None)):
        raise ParseError('第3形態の保存・再読込を検証できません。')
    loaded = module.core.SaveFile(module.core.Data(save.to_data().data), cc=save.cc)
    if loaded.game_version.game_version != save.game_version.game_version:
        raise RuntimeError('形態変更で保存版数が一致しません。')
    if loaded.cats.serialize() != save.cats.serialize() or loaded.lineups.serialize() != save.lineups.serialize():
        raise RuntimeError('第3形態の保存・再読込または他キャラ・編成の保持検証に失敗しました。')
