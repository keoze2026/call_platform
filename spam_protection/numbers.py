"""One phone number, however a carrier or a person writes it.

A blocked number was matched on the exact text. Calls arrive from the carrier
as 16283585660, and a number added by hand is just as likely to be
+16283585660, 6283585660 or (628) 358-5660. Any of those differs by a
character, so the block was saved, showed on the Blocked Numbers page, and
never matched the call. Every check now looks for all the ways of writing the
same US number.
"""
import re


def digits_of(raw) -> str:
    return re.sub(r'\D', '', str(raw or ''))


def variants(raw) -> list:
    """Every spelling of this number worth matching: as given, bare digits,
    ten digits, with 1 and with +1 (US numbers). A value with no digits
    ('anonymous', '') is returned as it is."""
    text = str(raw or '').strip()
    digits = digits_of(text)
    if not digits:
        return [text]
    out = {text, digits, '+' + digits}
    if len(digits) == 10 or (len(digits) == 11 and digits.startswith('1')):
        ten = digits[-10:]
        out |= {ten, '1' + ten, '+1' + ten}
    return list(out)


def canonical(raw) -> str:
    """How a number is stored when it is added: digits only, keeping a leading
    plus, no spaces, brackets or dashes."""
    text = str(raw or '').strip()
    digits = digits_of(text)
    if not digits:
        return text
    return ('+' if text.startswith('+') else '') + digits
