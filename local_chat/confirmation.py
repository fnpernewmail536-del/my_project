"""Recognize a complete approval reply without treating edited orders as approval."""
import unicodedata


REPLIES = frozenset({
    '確認して実行', 'はい', 'うん', 'ok', 'okay', 'おけ', '了解', '大丈夫',
    'それでお願いします', '合ってる', '合っています', 'この内容で実行',
    '確認した', '確定', '実行してください', 'お願いします',
})
QUOTES = {'「': '」', '『': '』', '"': '"', "'": "'", '“': '”', '‘': '’'}


def is_confirmation_reply(text):
    if not isinstance(text, str) or len(text) > 16000:
        return False
    value = unicodedata.normalize('NFKC', text).casefold().strip()
    while value:
        value = value.rstrip('。.!').strip()
        if len(value) >= 2 and QUOTES.get(value[0]) == value[-1]:
            value = value[1:-1].strip()
        else:
            break
    return value in REPLIES
