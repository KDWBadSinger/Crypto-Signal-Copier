"""Conservative recognition of the explicitly agreed blogger vocabulary."""
import re
from .parser import normalize_signal_text


def parse_management(text: str) -> dict | None:
    text = normalize_signal_text(text)
    actions = []
    if re.search(r'带\s*成本损', text):
        actions.append('breakeven')
    if re.search(r'直接\s*手动\s*TP\s*1\b', text, re.I):
        actions.append('tp1')
    if re.search(r'留\s*小仓.*格局', text):
        actions.append('runner')
    if not actions:
        return None
    symbols = set(re.findall(r'#([A-Za-z0-9]{2,20})\b', text))
    symbols = {s.upper() if s.upper().endswith('USDT') else s.upper()+'USDT' for s in symbols}
    return {'actions': actions, 'symbol': next(iter(symbols)) if len(symbols) == 1 else None,
            'ambiguous': len(symbols) > 1 or bool(re.search(r'不要|不[带帶]|暂不|暫不|取消|如果|假如|[?？]', text))}
