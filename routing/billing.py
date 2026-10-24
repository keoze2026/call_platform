"""How a call is billed: the campaign's rule, or its destination's own (CH-247).

Sir, 24 Oct: "every buyer and their destination have different rules of
billing calls: some pay for incoming, some pay for connected only, some pay for
call length" - Revo's "Convert ON" per destination. A campaign destination row
(RuleDestination) may carry:

    revenue          what this buyer pays per converted call (None: the
                     campaign's revenue_amount)
    convert_on       ''         the campaign's rule, as before: answered and
                                at least min_call_duration seconds
                     incoming   every call forwarded to it converts, answered
                                or not ("Not Connected - With Conversion")
                     connected  answered
                     length     answered and at least convert_seconds long

A converted call is counted Converted and Paid, earns that revenue and pays the
publisher its payout, as Revo shows it. Only calls actually forwarded carry a
destination row: a call refused before dialling (blocked, duplicate, no
destination) never converts by it.
"""
from decimal import Decimal


def rule_of(call):
    """The destination row whose rule bills this call, or None for the
    campaign's rule."""
    rd = getattr(call, 'rule_destination', None) if getattr(call, 'rule_destination_id', None) else None
    if rd is None:
        return None
    if rd.convert_on or rd.revenue is not None:
        return rd
    return None


def label(call) -> str:
    """What CallRecord.billing_rule stores: '' for the campaign's rule."""
    rd = rule_of(call)
    if rd is None:
        return ''
    return rd.convert_on or 'rate'


def converts(call, answered: bool, duration: int) -> bool:
    campaign = getattr(call, 'campaign', None)
    min_dur = (getattr(campaign, 'min_call_duration', 0) or 0) if campaign else 0
    rd = rule_of(call)
    on = rd.convert_on if rd is not None else ''
    if on == 'incoming':
        return True
    if on == 'connected':
        return bool(answered)
    if on == 'length':
        return bool(answered) and (duration or 0) >= (rd.convert_seconds or 0)
    return bool(answered) and (duration or 0) >= min_dur


def revenue(call) -> Decimal:
    rd = rule_of(call)
    if rd is not None and rd.revenue is not None:
        return Decimal(rd.revenue)
    campaign = getattr(call, 'campaign', None)
    return Decimal((getattr(campaign, 'revenue_amount', 0) or 0) if campaign else 0)
