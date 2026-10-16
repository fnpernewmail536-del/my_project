"""Recognize explicit requests to unlock named characters without guessing names."""
import re
import unicodedata

PREFIX=r'(?:指定(?:する)?キャラ(?:1体)?(?:の)?(?:解放|開放)|キャラ(?:の)?(?:解放|開放)(?:対象|指定)?|(?:解放|開放)(?:する|してほしい)?キャラ(?:指定)?)'
TAIL=r'(?:を)?(?:して(?:ください|ほしい(?:です)?|もらいたい)?|してください|お願いします|お願い|希望(?:です)?|する)?'
SCOPES={'全キャラ','キャラ','全てのキャラ','すべてのキャラ','キャラ全員','レアキャラ','激レアキャラ','超激レアキャラ','伝説レア','exキャラ','基本キャラ','コラボキャラ','コラボ系','レア','激レア','超激レア','伝説','ex','基本'}

def target_text(text):
    value=text.strip()
    value=re.sub(r'^(?:[-・•]\s*)','',value)
    return value.strip()

def unlock_sentence(text):
    value=unicodedata.normalize('NFKC',text).strip().rstrip('。!！')
    if re.search(r'円|¥|￥|引き継ぎ|引継ぎ|認証',value):return None
    if re.fullmatch(PREFIX+r'\s*[:=]?\s*'+TAIL,value):return 'メニュー27'
    replacement=re.fullmatch(r'('+PREFIX+r')(?:は|を|:|=)?\s*(.+?)\s*に(?:変更|訂正|修正)'+TAIL,value)
    if replacement:return '指定キャラ変更='+replacement[2]
    prefix=re.fullmatch(r'('+PREFIX+r')(?:は|を|:|=)\s*(.+)',value)
    if prefix:return 'メニュー27='+target_text(prefix[2])
    before=re.fullmatch(r'(?:解放|開放)してほしいキャラ(?:は|:|=)\s*(.+)',value)
    if before:return 'メニュー27='+target_text(before[1])
    after=re.fullmatch(r'(?:(?:追加で|指定キャラとして)\s*)?(.+?)(?:を|の)?(?:解放|開放)'+TAIL,value)
    if after and after[1].casefold() not in SCOPES and not re.search(r'(?:全|全部|全て|全員)(?:解放)?$',after[1]):
        return 'メニュー27='+target_text(after[1])
    return None
