"""CH-656: the AI's one-line comment on a call, in the reader's language.

The AI Audit page is translated in full (Russian, Japanese, Chinese), but the
comment the LLM writes for each call (`ai_summary`) is English. The page asks
for its comments in the reader's language; each is translated once by the same
LLM the AI audit uses (AI_LLM_MODEL, ANTHROPIC_API_KEY from .env) and kept 90
days, so a comment is never paid for twice. Any failure (no key, a timeout, an
answer that does not fit) gives back nothing, and the page shows the English.
"""
import hashlib
import json
import logging
import os

from django.core.cache import cache

logger = logging.getLogger(__name__)

LANGS = {'ru': 'Russian', 'ja': 'Japanese', 'zh': 'Simplified Chinese'}
KEEP_SECONDS = 90 * 24 * 3600
MAX_ITEMS = 200
URL = 'https://api.anthropic.com/v1/messages'


def _key(lang: str, text: str) -> str:
    return f'ai_tr:{lang}:{hashlib.sha1(text.encode()).hexdigest()}'


def _ask_llm(lang: str, texts: list) -> list | None:
    import requests
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        return None
    model = os.environ.get('AI_LLM_MODEL', '') or 'claude-haiku-5-5'
    if not model.startswith('claude'):
        model = 'claude-haiku-5-5'
    prompt = (f'Translate each of these call-audit notes into {LANGS[lang]}. Keep phone numbers, '
              'numbers and names as they are. Answer with only a JSON array of strings, the same '
              'length and order as the input.\n\n' + json.dumps(texts, ensure_ascii=False))
    try:
        r = requests.post(URL, timeout=40, headers={
            'x-api-key': api_key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json',
        }, json={'model': model, 'max_tokens': 4000, 'messages': [{'role': 'user', 'content': prompt}]})
        if not r.ok:
            logger.warning('ai translate: %s %s', r.status_code, r.text[:200])
            return None
        text = ''.join(b.get('text', '') for b in r.json().get('content', []) if b.get('type') == 'text').strip()
        start, end = text.find('['), text.rfind(']')
        out = json.loads(text[start:end + 1]) if start >= 0 and end > start else None
    except Exception:
        logger.exception('ai translate failed')
        return None
    if not isinstance(out, list) or len(out) != len(texts) or not all(isinstance(x, str) for x in out):
        logger.warning('ai translate: answer did not fit (%s for %s)', len(out) if isinstance(out, list) else '-', len(texts))
        return None
    return out


def translate(lang: str, texts: list) -> dict:
    """{english text: translation} for the texts that could be translated."""
    if lang not in LANGS:
        return {}
    wanted = list(dict.fromkeys(t for t in texts if t and t.strip()))[:MAX_ITEMS]
    done, todo = {}, []
    for t in wanted:
        hit = cache.get(_key(lang, t))
        if hit:
            done[t] = hit
        else:
            todo.append(t)
    for i in range(0, len(todo), 40):                 # 40 notes per question
        part = todo[i:i + 40]
        answer = _ask_llm(lang, part)
        if answer is None:
            break
        for src, dst in zip(part, answer):
            dst = dst.strip()
            if dst:
                cache.set(_key(lang, src), dst, KEEP_SECONDS)
                done[src] = dst
    return done
