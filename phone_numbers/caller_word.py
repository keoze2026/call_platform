"""A caller ID that is a word, not a number (CH-344).

A TFN test call lands in the reports with the caller ID "TFN Test" (CH-312;
Sir, 28 Oct: "under Caller ID can we put TFN Test"). Every place that shows a
caller ID used to keep only its digits, so the word came out empty. This keeps
a word as it is. Rows written before 28 Oct say "Test"; they read "TFN Test".
No imports: analytics, reports and Telegram code all use it.
"""
TEST_CALLER = 'TFN Test'
LEGACY_TEST_CALLERS = ('Test',)


def caller_word(value):
    """The caller ID as a word, or None when it is a phone number."""
    raw = str(value or '').strip()
    if not raw or any(c.isdigit() for c in raw):
        return None
    return TEST_CALLER if raw in LEGACY_TEST_CALLERS else raw
