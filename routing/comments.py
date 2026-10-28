"""How a call's comment (block_reason) reads in reports and exports (CH-349).

Sir, 28 Oct, on rows reading "Caller Is On A Do-Not-Call Register": "Comment
should only be CallerId Blocked". Every caller ID refused because it is on a
block list - do-not-call, spam, fraud, blacklist, or blocked by the team -
reads "CallerId Blocked", in new rows (routing/engine.py) and in rows saved
before, wherever the comment is shown or exported. The frontend has the same
rule in lib/fail-reason.ts.
"""
import re

CALLER_ID_BLOCKED = 'CallerId Blocked'

_CALLER_BLOCK = re.compile(
    r'^caller\s*-?\s*id\s*blocked$'
    r'|do[\s-]*not[\s-]*call'
    r'|caller\s+flagged\s+as\s+(spam|fraud)'
    r'|caller\s+is\s+blacklisted|blacklisted\s+number',
    re.I,
)


def is_caller_block(reason) -> bool:
    return bool(_CALLER_BLOCK.search((reason or '').strip()))


def comment(reason) -> str:
    """The comment as shown: "CallerId Blocked" for a blocked caller ID,
    otherwise unchanged."""
    raw = (reason or '').strip()
    return CALLER_ID_BLOCKED if raw and is_caller_block(raw) else raw
