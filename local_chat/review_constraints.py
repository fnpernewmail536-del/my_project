"""Keep incomplete document intent visible throughout the review lifecycle."""
import re

OWNERSHIP_INPUT='コラボ取得対象の除外'
OWNERSHIP_REASON='【定義確認】解放割合の除外対象が未確定です。除外するコラボを指定した注文書を全文再送してください。開催年の条件が不要なら、条件を外して元の解放割合を再解析してください。'


def enforce_review_constraints(plan):
    if not plan or not plan.get('_pending_document_ownership'):return plan
    issues=plan.setdefault('unresolved',[])
    existing=next((x for x in issues if x.get('input')==OWNERSHIP_INPUT),None)
    if existing is None:
        existing={'input':OWNERSHIP_INPUT,'reason':OWNERSHIP_REASON,'category':'metadata'}
        issues.append(existing)
    existing['scope']='document_ownership'
    # An old draft has no recoverable ratio expressions. It must be resubmitted
    # instead of proceeding with only the other edits left in the draft.
    existing['dismissible']=bool(plan.get('_document_source') and plan.get('_year_exclusion_cancellable'))
    if existing['dismissible']:existing['omit_label']='開催年の条件を外して再解析'
    else:existing.pop('omit_label',None)
    plan['blockers']=list(plan.get('menu_pending',{}).values())+[f'未解釈項目{i+1}：{x["input"]} — {x["reason"]}' for i,x in enumerate(issues)]
    plan['ready']=False
    return plan


def without_year_exclusion(plan):
    """Only invoked by an explicit omission of the pending exclusion issue."""
    from .parser import ParseError
    from .structured_requests import parse_document,document_lines
    source=plan.get('_document_source')
    if not source:
        raise ParseError('この下書きには元の解放割合の本文が残っていません。注文書を全文再送してください。開催年の条件が不要なら、その条件と優先条件の2行を外して再送してください。')
    doc=parse_document(source)
    if not doc['collab_cutoffs'] or doc['excluded_collab_names']:
        raise ParseError('除外条件を外す場合は、修正した注文書を全文再送してください。')
    removed={x['source'] for x in doc['collab_cutoffs']}
    return '\n'.join(line for line in document_lines(source) if re.sub(r'^[-•・]\s*','',line).strip() not in removed)
