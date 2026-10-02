"""Immutable, user-facing evidence snapshots; never reconstruct old executions."""
from datetime import UTC, datetime


def message_evidence(message):
    return {key: message.get(key) for key in
            ('chat_id', 'message_id', 'source_name', 'text', 'sent_at')}


def signal_evidence(signal):
    data = signal.model_dump(mode='json') if hasattr(signal, 'model_dump') else signal
    return data.get('source_messages') or [{
        'chat_id': data.get('source_chat_id'), 'message_id': data.get('source_message_id'),
        'source_name': data.get('source_name'), 'text': data.get('raw_text'), 'sent_at': None,
    }]


def event(action, detail, *, sources=None, actor='system', at=None, values=None):
    return {'at': at or datetime.now(UTC).isoformat(), 'action': action,
            'detail': detail, 'actor': actor, 'sources': sources or [], 'values': values or {}}
