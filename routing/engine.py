import logging
import random
from datetime import datetime
from decimal import Decimal
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.core.cache import cache
from .models import RoutingRule, RuleCondition, RuleDestination, CallLog

logger = logging.getLogger(__name__)



# How far back Original looks for the destination that took a caller's first call.
ORIGINAL_MEMORY_HOURS = 24 * 365



def _d10(number) -> str:
    """A phone number by its last ten digits, so "+18667404516", "18667404516"
    and "8667404516" are the same TFN when duplicates are compared (CH-241)."""
    return ''.join(c for c in str(number or '') if c.isdigit())[-10:]

class RoutingEngine:
    @staticmethod
    def get_caller_state(area_code: str) -> str:
        area_code_map = {
            '201': 'NJ', '202': 'DC', '203': 'CT', '204': 'MB', '205': 'AL',
            '206': 'WA', '207': 'ME', '208': 'ID', '209': 'CA', '210': 'TX',
            '212': 'NY', '213': 'CA', '214': 'TX', '215': 'PA', '216': 'OH',
            '217': 'IL', '218': 'MN', '219': 'IN', '220': 'OH', '223': 'PA',
            '224': 'IL', '225': 'LA', '228': 'MS', '229': 'GA', '231': 'MI',
            '234': 'OH', '239': 'FL', '240': 'MD', '248': 'MI', '251': 'AL',
            '252': 'NC', '253': 'WA', '254': 'TX', '256': 'AL', '260': 'IN',
            '262': 'WI', '267': 'PA', '269': 'MI', '270': 'KY', '272': 'PA',
            '276': 'VA', '281': 'TX', '301': 'MD', '302': 'DE', '303': 'CO',
            '304': 'WV', '305': 'FL', '306': 'SK', '307': 'WY', '308': 'NE',
            '309': 'IL', '310': 'CA', '312': 'IL', '313': 'MI', '314': 'MO',
            '315': 'NY', '316': 'KS', '317': 'IN', '318': 'LA', '319': 'IA',
            '320': 'MN', '321': 'FL', '323': 'CA', '325': 'TX', '330': 'OH',
            '331': 'IL', '334': 'AL', '336': 'NC', '337': 'LA', '339': 'MA',
            '340': 'VI', '347': 'NY', '351': 'MA', '352': 'FL', '360': 'WA',
            '361': 'TX', '364': 'KY', '380': 'OH', '385': 'UT', '386': 'FL',
            '401': 'RI', '402': 'NE', '404': 'GA', '405': 'OK', '406': 'MT',
            '407': 'FL', '408': 'CA', '409': 'TX', '410': 'MD', '412': 'PA',
            '413': 'MA', '414': 'WI', '415': 'CA', '417': 'MO', '419': 'OH',
            '423': 'TN', '424': 'CA', '425': 'WA', '430': 'TX', '432': 'TX',
            '434': 'VA', '435': 'UT', '440': 'OH', '442': 'CA', '443': 'MD',
            '458': 'OR', '469': 'TX', '470': 'GA', '475': 'CT', '478': 'GA',
            '479': 'AR', '480': 'AZ', '484': 'PA', '501': 'AR', '502': 'KY',
            '503': 'OR', '504': 'LA', '505': 'NM', '507': 'MN', '508': 'MA',
            '509': 'WA', '510': 'CA', '512': 'TX', '513': 'OH', '515': 'IA',
            '516': 'NY', '517': 'MI', '518': 'NY', '520': 'AZ', '530': 'CA',
            '531': 'NE', '534': 'WI', '539': 'OK', '540': 'VA', '541': 'OR',
            '551': 'NJ', '559': 'CA', '561': 'FL', '562': 'CA', '563': 'IA',
            '564': 'WA', '567': 'OH', '570': 'PA', '571': 'VA', '573': 'MO',
            '574': 'IN', '575': 'NM', '580': 'OK', '585': 'NY', '586': 'MI',
            '601': 'MS', '602': 'AZ', '603': 'NH', '605': 'SD', '606': 'KY',
            '607': 'NY', '608': 'WI', '609': 'NJ', '610': 'PA', '612': 'MN',
            '614': 'OH', '615': 'TN', '616': 'MI', '617': 'MA', '618': 'IL',
            '619': 'CA', '620': 'KS', '623': 'AZ', '626': 'CA', '628': 'CA',
            '629': 'TN', '630': 'IL', '631': 'NY', '636': 'MO', '641': 'IA',
            '646': 'NY', '650': 'CA', '651': 'MN', '657': 'CA', '660': 'MO',
            '661': 'CA', '662': 'MS', '669': 'CA', '678': 'GA', '681': 'WV',
            '682': 'TX', '701': 'ND', '702': 'NV', '703': 'VA', '704': 'NC',
            '706': 'GA', '707': 'CA', '708': 'IL', '712': 'IA', '713': 'TX',
            '714': 'CA', '715': 'WI', '716': 'NY', '717': 'PA', '718': 'NY',
            '719': 'CO', '720': 'CO', '724': 'PA', '725': 'NV', '727': 'FL',
            '731': 'TN', '732': 'NJ', '734': 'MI', '737': 'TX', '740': 'OH',
            '743': 'NC', '747': 'CA', '754': 'FL', '757': 'VA', '760': 'CA',
            '762': 'GA', '763': 'MN', '765': 'IN', '769': 'MS', '770': 'GA',
            '772': 'FL', '773': 'IL', '774': 'MA', '775': 'NV', '779': 'IL',
            '781': 'MA', '785': 'KS', '786': 'FL', '787': 'PR', '800': 'TF',
            '801': 'UT', '802': 'VT', '803': 'SC', '804': 'VA', '805': 'CA',
            '806': 'TX', '808': 'HI', '810': 'MI', '812': 'IN', '813': 'FL',
            '814': 'PA', '815': 'IL', '816': 'MO', '817': 'TX', '818': 'CA',
            '828': 'NC', '830': 'TX', '831': 'CA', '832': 'TX', '843': 'SC',
            '845': 'NY', '847': 'IL', '848': 'NJ', '850': 'FL', '854': 'SC',
            '856': 'NJ', '857': 'MA', '858': 'CA', '859': 'KY', '860': 'CT',
            '862': 'NJ', '863': 'FL', '864': 'SC', '865': 'TN', '870': 'AR',
            '872': 'IL', '878': 'PA', '901': 'TN', '903': 'TX', '904': 'FL',
            '906': 'MI', '907': 'AK', '908': 'NJ', '909': 'CA', '910': 'NC',
            '912': 'GA', '913': 'KS', '914': 'NY', '915': 'TX', '916': 'CA',
            '917': 'NY', '918': 'OK', '919': 'NC', '920': 'WI', '925': 'CA',
            '928': 'AZ', '929': 'NY', '930': 'IN', '931': 'TN', '936': 'TX',
            '937': 'OH', '938': 'AL', '940': 'TX', '941': 'FL', '947': 'MI',
            '949': 'CA', '951': 'CA', '952': 'MN', '954': 'FL', '956': 'TX',
            '959': 'CT', '970': 'CO', '971': 'OR', '972': 'TX', '973': 'NJ',
            '975': 'MO', '978': 'MA', '979': 'TX', '980': 'NC', '984': 'NC',
            '985': 'LA', '989': 'MI',
        }
        return area_code_map.get(area_code, 'Unknown')

    @staticmethod
    def is_blacklisted(caller_number: str, organization_id: str, campaign_id: str = None) -> bool:
        from spam_protection.services import SpamProtectionService
        result = SpamProtectionService.check(
            phone_number=caller_number,
            organization_id=organization_id,
            campaign_id=campaign_id,
            log=False
        )
        return not result['allowed']

    @staticmethod
    def is_publisher_blocked(caller_number: str, organization_id: str, publisher_id) -> bool:
        """True when this caller is blocked on this publisher's numbers.

        Set from the publisher's own settings page (or by a publisher login
        with Block Numbers on). Only that publisher's calls are refused; the
        same caller on any other publisher routes as usual.
        """
        if not caller_number or not publisher_id:
            return False
        from spam_protection.models import Blacklist
        from spam_protection.numbers import variants
        now = timezone.now()
        return Blacklist.objects.filter(
            organization_id=organization_id,
            publisher_id=publisher_id,
            phone_number__in=variants(caller_number),
            is_active=True,
        ).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
        ).exists()

    # Reasons the platform writes by itself (do-not-call hit, spam or fraud
    # check). Those callers are still refused in route_call with a row the team
    # can see, so an automatic block never makes a call vanish unnoticed.
    AUTO_BLOCK_REASONS = ('auto_detected', 'spam', 'fraud')

    @staticmethod
    def is_blocked_caller(caller_number: str, organization_id: str, publisher_id=None) -> bool:
        """True when the team blocked this caller ID (CH-206).

        Sir: once a caller ID is blocked it never shows on the portal. Asked
        before a call row exists, so the call is dropped with nothing written,
        however many times the number dials. Covers the organization's Blocked
        Numbers (active, not expired, whitelist still wins) and a block set on
        this TFN's publisher. Not covered: a buyer-only block (the caller still
        routes to every other buyer) and the automatic reasons above.
        """
        if not caller_number:
            return False
        if RoutingEngine.is_publisher_blocked(caller_number, organization_id, publisher_id):
            return True
        from spam_protection.models import Blacklist
        from spam_protection.numbers import variants
        from spam_protection.services import SpamProtectionService
        now = timezone.now()
        blocked = Blacklist.objects.filter(
            organization_id=organization_id,
            phone_number__in=variants(caller_number),
            is_active=True,
            campaign__isnull=True,
            buyer__isnull=True,
            publisher__isnull=True,
        ).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
        ).exclude(reason__in=RoutingEngine.AUTO_BLOCK_REASONS).exists()
        return blocked and not SpamProtectionService.is_whitelisted(caller_number, organization_id)

    # Every refusal from the spam checks was reported as "Caller is
    # blacklisted", whatever had actually put the number there. Most of these
    # are the TCPA check adding a do-not-call hit automatically after a
    # previous call (spam_protection/dnc.py), which reads nothing like a
    # blacklist to whoever is looking at the export - the boss read them as
    # duplicates. One query, and only for a call that is already refused.
    # CH-349 (Sir, 28 Oct): "Comment should only be CallerId Blocked" - for
    # every blocked caller ID, whatever put it on the list (do-not-call, spam,
    # fraud, the team). The reason stays in the routing trace for the team.
    BLOCKED_CALLER_LABEL = 'CallerId Blocked'
    # A user workspace out of balance (CH-255): the call is missed with this
    # comment, in Sir's words.
    INSUFFICIENT_FUNDS_LABEL = 'Insufficient Funds'

    BLOCK_LABELS = {
        'auto_detected': 'Caller is on a do-not-call register',
        'spam':          'Caller flagged as spam',
        'fraud':         'Caller flagged as fraud',
        # A number the team blocked on purpose (Block button / Blocked
        # Numbers). The boss's wording: the call is MISSED and the comment
        # reads "CallerId Blocked" (asterisk_handler marks it missed).
        'manual':        BLOCKED_CALLER_LABEL,
    }

    # What a blocked caller ID's entry says, for the trace; all of them read
    # "CallerId Blocked" in the comment (CH-349).
    CALLER_BLOCK_DETAILS = frozenset(BLOCK_LABELS.values())

    @staticmethod
    def block_label(caller_number: str, organization_id: str, campaign_id: str = None) -> str:
        from spam_protection.models import Blacklist
        from spam_protection.numbers import variants
        entry = Blacklist.objects.filter(
            organization_id=organization_id,
            phone_number__in=variants(caller_number),
            is_active=True,
            # Only the organization's own blocks refuse a call; a buyer's or a
            # publisher's entry for the same number must not lend its reason.
            buyer__isnull=True,
            publisher__isnull=True,
        )
        if campaign_id:
            # This campaign's block or a workspace-wide one, never another
            # campaign's (CH-339).
            entry = entry.filter(models.Q(campaign__isnull=True) | models.Q(campaign_id=campaign_id))
        entry = entry.order_by('campaign_id').first()
        if not entry:
            # Refused by a check that is not the blacklist - today that is the
            # anonymous-caller block.
            return 'Anonymous caller blocked'
        # The Blocked Numbers page saves its own reason text ("scope=number"),
        # which is none of the keys above, so every number the team blocked
        # there read "Caller is blacklisted". Sir: a blocked caller ID is
        # "CallerId Blocked", whatever the entry's reason says (CH-206, CH-349).
        return RoutingEngine.BLOCK_LABELS.get(entry.reason, RoutingEngine.BLOCKED_CALLER_LABEL)

    @staticmethod
    def is_duplicate(caller_number: str, campaign_id: str, block_hours: int) -> bool:
        from datetime import timedelta
        if block_hours <= 0:
            return False
        cutoff = timezone.now() - timedelta(hours=block_hours)
        return CallLog.objects.filter(
            campaign_id=campaign_id,
            caller_number=caller_number,
            created_at__gte=cutoff,
            status__in=['completed', 'in_progress', 'ringing', 'initiated']
        ).exists()
    
    @staticmethod
    def is_repeat_caller(caller_number: str, campaign_id: str, window_hours: int) -> bool:
        """Did this caller already call this campaign inside its window? (CH-230)

        For the Repeated column, as Revo's Dupe: ANY earlier call counts,
        answered or not. 15047156000, 23 Oct: missed at 3:02 PM, called again at
        3:03 PM, and both rows read Repeated No because is_duplicate() above
        only counts an earlier call that was answered or still live.

        is_duplicate() is unchanged and still decides duplicate HANDLING in
        routing, so a caller who was missed is never held back as a duplicate.
        A sender's retry of the same call is not a repeat; the caller of
        this function takes care of that (routing.retry_group).
        """
        from datetime import timedelta
        if window_hours <= 0:
            return False
        cutoff = timezone.now() - timedelta(hours=window_hours)
        return CallLog.objects.filter(
            campaign_id=campaign_id,
            caller_number=caller_number,
            created_at__gte=cutoff,
            retry_hidden=False,
        ).exists()

    # @staticmethod
    # def mark_as_called(caller_number: str, campaign_id: str, block_hours: int):
    #     cache_key = f"duplicate_{campaign_id}_{caller_number}"
    #     cache.set(cache_key, True, timeout=block_hours * 3600)


    @staticmethod
    def mark_as_called(caller_number: str, campaign_id: str, block_hours: int):
        # No-op — dup detection now reads CallLog directly
        pass
    
    @staticmethod
    def check_campaign_caps(campaign, current_sid=None):
        """The reason this campaign cannot take another call, or None.

        `current_sid` is the call being routed, excluded from the live count -
        its row already exists by the time this check runs.

        'Campaign cap reached' is reserved for a FULL concurrency limit - the
        only refusal the portal may show as Overflow Missed. The owner's rule,
        verbatim: "Overflow drop only possible when camp given cc is full."
        The volume limits answer with their own words.

        The volume counts take only calls that were actually dispatched to a
        destination. Every refusal of ours - no rule, duplicate, blacklist,
        destination missed - carries a block_reason and must not eat the
        campaign's quota: counting them meant a day of refused traffic locked
        the campaign below its concurrency and the label blamed the cap.
        """
        try:
            cap = campaign.cap
        except Exception:
            return None

        today = timezone.now().date()
        month_start = today.replace(day=1)
        dispatched = (
            CallLog.objects.filter(campaign=campaign, block_reason='')
            .exclude(destination_number='')
            .exclude(destination_number__isnull=True)
        )

        if cap.max_calls_daily > 0:
            if dispatched.filter(created_at__date=today).count() >= cap.max_calls_daily:
                return 'Campaign daily limit reached'

        if cap.max_calls_monthly > 0:
            if dispatched.filter(created_at__date__gte=month_start).count() >= cap.max_calls_monthly:
                return 'Campaign monthly limit reached'

        if cap.max_calls_global > 0:
            if dispatched.count() >= cap.max_calls_global:
                return 'Campaign total limit reached'

        # Concurrency. This was the one cap on the campaign that nothing read:
        # daily, monthly and global were all enforced here and max_concurrency
        # was skipped, so the Campaigns page showed "8 / 6" on a campaign whose
        # limit was 6 and kept dispatching.
        #
        # Counted the same way as the buyer and destination checks: only rows
        # still open, and only from the last four hours so a row left behind by
        # a missing end-of-call webhook cannot block a campaign for ever.
        if cap.max_concurrency > 0:
            from datetime import timedelta
            live_qs = CallLog.objects.filter(
                campaign=campaign,
                status__in=[CallLog.Status.IN_PROGRESS, CallLog.Status.RINGING],
                ended_at__isnull=True,
                created_at__gte=timezone.now() - timedelta(hours=4),
            )
            # The call being routed already has its own row, written on arrival
            # with status RINGING - counting it made every cap off by one: at 4
            # real calls the newcomer's own row read as the 5th and CC=5 could
            # never fill. The boss's team, verbatim: "not coming above 4 cc".
            if current_sid:
                live_qs = live_qs.exclude(twilio_call_sid=current_sid)
            if live_qs.count() >= cap.max_concurrency:
                return 'Campaign cap reached'

        return None

    @staticmethod
    def check_buyer_caps(buyer) -> bool:
        try:
            cap = buyer.cap
        except Exception:
            return True

        today = timezone.now().date()
        month_start = today.replace(day=1)

        if cap.max_calls_daily > 0:
            daily_count = CallLog.objects.filter(
                buyer=buyer,
                created_at__date=today
            ).count()
            if daily_count >= cap.max_calls_daily:
                return False

        if cap.max_calls_monthly > 0:
            monthly_count = CallLog.objects.filter(
                buyer=buyer,
                created_at__date__gte=month_start
            ).count()
            if monthly_count >= cap.max_calls_monthly:
                return False

        return True

    @staticmethod
    def pick_live_destination(buyer, exclude=(), only=None):
        """The buyer's live TFN for THIS call - rotated, cap-aware.

        The hangup-side resolution used to take .first() of the buyer's live
        destinations, which is why a buyer could only ever have one: a second
        live TFN sat in the interface looking active and never received a
        call, and the API enforced the limit to make that honest. The boss's
        decision is the opposite - multiple live TFNs per buyer - so this is
        the distribution that makes it true: walk the buyer's live
        destinations round-robin, skip any at its own concurrency cap, and
        when every one is capped rotate anyway so the load still spreads.

        Returns a Destination row or None when the buyer has none live.
        """
        from datetime import timedelta

        from buyers.destination import Destination

        rows = list(
            Destination.objects.filter(buyer=buyer, enabled=True)
            .only('id', 'tfn', 'concurrency_cap')
            .order_by('created_at')
        )
        # Duplicate handling (CH-241). The rotation below chose among ALL the
        # buyer's live TFNs, after routing had applied the campaign's duplicate
        # rule to the routed rows - so a repeat caller on "Different" could be
        # rotated straight back onto the TFN that already had him, and one on
        # "Original" away from his original TFN. `only`: the TFN he must go
        # back to; `exclude`: TFNs he must not reach again. None left: the
        # caller keeps the number routing chose, which already obeys them.
        if only:
            pinned = [r for r in rows if _d10(r.tfn) == only]
            return pinned[0] if pinned else None
        if exclude:
            rows = [r for r in rows if _d10(r.tfn) not in exclude]
        if not rows:
            return None
        if len(rows) == 1:
            return rows[0]

        key = f'buyer_live_rr_{buyer.id}'
        start = cache.get(key, 0)
        n = len(rows)
        for step in range(n):
            d = rows[(start + step) % n]
            if d.concurrency_cap and d.concurrency_cap > 0:
                active = CallLog.objects.filter(
                    buyer=buyer,
                    destination_number=d.tfn,
                    status__in=[CallLog.Status.IN_PROGRESS, CallLog.Status.RINGING],
                    ended_at__isnull=True,
                    created_at__gte=timezone.now() - timedelta(hours=4),
                ).count()
                if active >= d.concurrency_cap:
                    continue
            cache.set(key, (start + step + 1) % n, timeout=86400)
            return d

        # Every TFN at its cap: plain rotation, same as a single saturated
        # destination would behave - the concurrency guard upstream decides
        # whether the call proceeds at all.
        cache.set(key, (start + 1) % n, timeout=86400)
        return rows[start % n]

    @staticmethod
    def check_buyer_concurrency(buyer, destination_number: str = None) -> bool:
        from datetime import timedelta
        
        # 1. Check destination-specific cap first if provided
        if destination_number:
            from buyers.destination import Destination
            try:
                buyer_dest = Destination.objects.filter(
                    buyer=buyer,
                    tfn=destination_number
                ).first()
                if buyer_dest and buyer_dest.concurrency_cap > 0:
                    dest_active = CallLog.objects.filter(
                        buyer=buyer,
                        destination_number=destination_number,
                        status__in=[CallLog.Status.IN_PROGRESS, CallLog.Status.RINGING],
                        ended_at__isnull=True,
                        created_at__gte=timezone.now() - timedelta(hours=4)
                    ).count()
                    if dest_active >= buyer_dest.concurrency_cap:
                        return False
            except Exception:
                # Failing open is deliberate: a broken cap check must not stop
                # calls. But it means the cap is not being applied, so it is
                # logged rather than hidden.
                logger.exception('destination concurrency cap check failed for buyer=%s', buyer.id)

        if buyer.max_concurrency == 0:
            return True
            
        active_calls = CallLog.objects.filter(
            buyer=buyer,
            status__in=[CallLog.Status.IN_PROGRESS, CallLog.Status.RINGING],
            ended_at__isnull=True,
            created_at__gte=timezone.now() - timedelta(hours=4)
        ).count()
        return active_calls < buyer.max_concurrency

    @staticmethod
    def evaluate_time_based(rule: RoutingRule, call_time: datetime) -> bool:
        try:
            condition = rule.conditions.first()
            if not condition:
                return False
            data = condition.conditions
            days = data.get('days', [])
            start_time = data.get('start_time', '00:00')
            end_time = data.get('end_time', '23:59')
            current_day = call_time.weekday()
            current_time = call_time.strftime('%H:%M')
            return current_day in days and start_time <= current_time <= end_time
        except Exception:
            return False

    @staticmethod
    def evaluate_geo_based(rule: RoutingRule, caller_state: str, caller_area_code: str) -> bool:
        try:
            condition = rule.conditions.first()
            if not condition:
                return False
            data = condition.conditions
            allowed_states = data.get('states', [])
            allowed_area_codes = data.get('area_codes', [])
            if allowed_states and caller_state in allowed_states:
                return True
            if allowed_area_codes and caller_area_code in allowed_area_codes:
                return True
            return False
        except Exception:
            return False

    @staticmethod
    def evaluate_caller_specific(rule: RoutingRule, caller_number: str) -> bool:
        try:
            condition = rule.conditions.first()
            if not condition:
                return False
            data = condition.conditions
            numbers = data.get('numbers', [])
            return caller_number in numbers
        except Exception:
            return False

    @staticmethod
    def evaluate_tag_based(rule: RoutingRule, call_tags: list) -> bool:
        try:
            condition = rule.conditions.first()
            if not condition:
                return False
            data = condition.conditions
            required_tags = data.get('tags', [])
            return any(tag in call_tags for tag in required_tags)
        except Exception:
            return False

    @staticmethod
    def _apply_duplicate_filter(destinations, call_data):
        """The same duplicate rules the priority path enforces, for the
        pickers that choose from a pool instead of walking it in order.
        Without this, Round Robin and Weighted handed a repeat caller back
        to a destination the campaign said they must not reach."""
        dup_only = call_data.get('dup_only_destination')
        excl_d = call_data.get('dup_exclude_destinations', ())
        excl_b = call_data.get('dup_exclude_buyers', ())
        blocked_b = call_data.get('blocked_buyers', ())
        if not dup_only and not excl_d and not excl_b and not blocked_b:
            return destinations
        kept = []
        for d in destinations:
            if dup_only and _d10(d.destination) != dup_only:
                continue
            if _d10(d.destination) in excl_d:
                continue
            if d.buyer_id and str(d.buyer_id) in excl_b:
                continue
            if d.buyer_id and str(d.buyer_id) in blocked_b:
                continue
            kept.append(d)
        return kept

    @staticmethod
    def get_round_robin_destination(rule: RoutingRule, call_data: dict = None) -> RuleDestination:
        destinations = RoutingEngine._apply_duplicate_filter(
            list(rule.destinations.all().order_by('priority')), call_data or {},
        )
        if not destinations:
            return None
        cache_key = f"round_robin_{rule.id}"
        current_index = cache.get(cache_key, 0)
        destination = destinations[current_index % len(destinations)]
        # A TFN test (CH-175) looks at where a call would go without taking its turn.
        if not (call_data or {}).get('is_test'):
            cache.set(cache_key, (current_index + 1) % len(destinations), timeout=86400)
        return destination

    @staticmethod
    def get_weighted_destination(rule: RoutingRule, call_data: dict = None) -> RuleDestination:
        destinations = RoutingEngine._apply_duplicate_filter(
            list(rule.destinations.all()), call_data or {},
        )
        if not destinations:
            return None
        total_weight = sum(d.weight for d in destinations)
        rand = random.uniform(0, total_weight)
        cumulative = 0
        for destination in destinations:
            cumulative += destination.weight
            if rand <= cumulative:
                return destination
        return destinations[-1]

    @staticmethod
    def get_priority_destination(rule: RoutingRule) -> RuleDestination:
        return rule.destinations.order_by('priority').first()

    @staticmethod
    def is_destination_enabled(buyer, destination_number: str) -> bool:
        """Is the operator's Destination row for this number switched on?

        The `enabled` toggle on buyers.Destination decided only whether a row
        appeared on the dashboard. Routing never read it, so a destination
        switched off kept taking calls and simply vanished from the operator's
        view - on 5 October one took 155 calls while showing as paused. Pausing
        a destination to stop traffic did nothing but hide it.

        Absence is not a pause. Calls route through RoutingRule/RuleDestination,
        and not every one of those has a matching buyers.Destination row; when
        there is no row there is nothing to honour, so the call proceeds. Only
        a row that exists and says `enabled=False` stops it.
        """
        if not destination_number:
            return True
        from buyers.destination import Destination
        try:
            row = Destination.objects.filter(
                buyer=buyer, tfn=destination_number
            ).only('enabled').first()
        except Exception:
            # A lookup failure must never cost a call. Worst case we route as
            # before, which is what happened for every call until now.
            logger.exception(
                'could not read the enabled flag for %s', destination_number
            )
            return True
        if row is None:
            return True
        return bool(row.enabled)

    @staticmethod
    def get_valid_destination(rule: RoutingRule, call_data: dict):
        """Standard routing: priority first, then weight (CH-249), as on Revo.

        Destinations are tried by priority, 1 before 2. Inside one priority the
        call goes to one of the destinations that can take it, chosen by weight:
        three lines at priority 1 / weight 100 share the calls evenly, 75 and 25
        split them three to one, weight 0 takes a call only when nothing else
        at that priority can. Only when no destination of a priority can take
        the call does the next priority get it.

        It used to take the first destination of the top priority every time,
        so weight did nothing and one line at priority 1 took every call until
        it was switched off or full (Sir, 24 Oct: "priority and weight should
        work perfect").

        Records why each one was rejected into call_data['trace'] when the caller
        supplies it, so a call can later explain its own routing. Costs nothing
        when no trace is passed.
        """
        trace = call_data.get('trace')
        destinations = list(rule.destinations.all().order_by('priority', 'created_at'))

        tiers = []
        for destination in destinations:
            if tiers and tiers[-1][0] == destination.priority:
                tiers[-1][1].append(destination)
            else:
                tiers.append((destination.priority, [destination]))

        for _priority, tier in tiers:
            eligible = []
            for destination in tier:
                reason = RoutingEngine._destination_refusal(destination, call_data)
                if reason is None:
                    eligible.append(destination)
                    if trace is not None:
                        trace.record_destination(rule, destination, eligible=True)
                elif trace is not None:
                    trace.record_destination(rule, destination, eligible=False, reason=reason)
            if eligible:
                return RoutingEngine._by_weight(eligible)

        return None

    @staticmethod
    def _by_weight(eligible):
        """One destination, chosen in proportion to weight. Weight 0 only when
        every candidate is 0."""
        if len(eligible) == 1:
            return eligible[0]
        weights = [max(0, d.weight or 0) for d in eligible]
        if sum(weights) <= 0:
            return eligible[0]
        return random.choices(eligible, weights=weights, k=1)[0]

    @staticmethod
    def _destination_refusal(destination, call_data):
        """Why this destination cannot take the call, or None."""
        reason = None

        # Duplicate handling, set up by route_call for a repeat caller.
        # Original: only the destination that took their first call.
        # Different: never one they have already reached.
        dup_only = call_data.get('dup_only_destination')
        if dup_only and _d10(destination.destination) != dup_only:
            reason = 'duplicate routes to its original destination'
        elif _d10(destination.destination) in call_data.get('dup_exclude_destinations', ()):
            reason = 'already took this caller (duplicates: different)'
        elif destination.buyer_id and str(destination.buyer_id) in call_data.get('dup_exclude_buyers', ()):
            reason = f'buyer already took this caller (duplicates: different)'
        elif destination.buyer_id and str(destination.buyer_id) in call_data.get('blocked_buyers', ()):
            reason = 'this buyer has blocked the caller'

        if reason is None and destination.buyer:
            buyer = destination.buyer
            dest_fault = None
            if buyer.status != 'active':
                reason = f'buyer {buyer.name} is {buyer.status}'
                dest_fault = 'Destination missed (buyer paused)'
            elif not RoutingEngine.is_destination_enabled(
                buyer, destination.destination
            ):
                reason = f'destination {destination.destination} is switched off'
                dest_fault = 'Destination missed (TFN off)'
            elif not RoutingEngine.check_buyer_caps(buyer):
                reason = f'buyer {buyer.name} has reached its cap'
                dest_fault = 'Destination missed (cap full)'
            elif not RoutingEngine.check_buyer_concurrency(
                buyer, destination_number=destination.destination
            ):
                reason = f'buyer {buyer.name} is at concurrency limit'
                dest_fault = 'Destination missed (CC full)'
            # The owner's rule: a call the buyer's line could not take is
            # missed ON THAT LINE, never an anonymous campaign drop. The
            # first destination-side refusal in priority order is the line
            # that should have answered, so the call is recorded against
            # it if no later destination takes the call.
            if dest_fault and 'refused_destination' not in call_data:
                call_data['refused_destination'] = {
                    'tfn': destination.destination,
                    'buyer': buyer,
                    'label': dest_fault,
                }
        return reason

    @staticmethod
    def evaluate_rule(rule: RoutingRule, call_data: dict) -> RuleDestination:
        caller_number = call_data.get('caller_number', '')
        caller_area_code = call_data.get('caller_area_code', '')
        caller_state = call_data.get('caller_state', '')
        call_tags = call_data.get('tags', [])
        call_time = call_data.get('call_time', timezone.now())

        matched = False

        if rule.rule_type == RoutingRule.RuleType.TIME_BASED:
            matched = RoutingEngine.evaluate_time_based(rule, call_time)

        elif rule.rule_type == RoutingRule.RuleType.GEO_BASED:
            matched = RoutingEngine.evaluate_geo_based(rule, caller_state, caller_area_code)

        elif rule.rule_type == RoutingRule.RuleType.ROUND_ROBIN:
            return RoutingEngine.get_round_robin_destination(rule, call_data)

        elif rule.rule_type == RoutingRule.RuleType.WEIGHTED:
            return RoutingEngine.get_weighted_destination(rule, call_data)

        elif rule.rule_type == RoutingRule.RuleType.PRIORITY:
            return RoutingEngine.get_valid_destination(rule, call_data)

        elif rule.rule_type == RoutingRule.RuleType.CALLER_SPECIFIC:
            matched = RoutingEngine.evaluate_caller_specific(rule, caller_number)

        elif rule.rule_type == RoutingRule.RuleType.OVERFLOW:
            return RoutingEngine.get_valid_destination(rule, call_data)

        elif rule.rule_type == RoutingRule.RuleType.SCHEDULE_BASED:
            matched = RoutingEngine.evaluate_time_based(rule, call_time)

        elif rule.rule_type == RoutingRule.RuleType.TAG_BASED:
            matched = RoutingEngine.evaluate_tag_based(rule, call_tags)

        if matched:
            return RoutingEngine.get_valid_destination(rule, call_data)

        return None

    @staticmethod
    def required_call_balance(campaign, phone_number=None, organization=None) -> Decimal:
        """Credit needed to dispatch one call, read from configured pricing.

        Nothing here is a fixed rate. The number comes from what the operator set
        in the UI, following the same precedence the numbers page displays:

          1. the tracking number's own payout_per_call
          2. the parent campaign's payout_amount

        Zero means the campaign has not been priced, so no minimum is enforced.
        MINIMUM_CALL_BALANCE is an optional floor, off (0) unless configured.
        """
        # A call cannot be routed unless the client can afford its first minute,
        # which is what they are actually billed. Falls through to the payout
        # figures only when no billing account rate is available.
        if organization is not None:
            try:
                from billing.services import BillingService
                one_minute = BillingService.call_cost(organization, 60)
                if one_minute > 0:
                    return one_minute
            except Exception:
                logger.exception("call_cost_failed: org=%s", getattr(organization, 'id', None))

        if phone_number is not None:
            per_number = getattr(phone_number, 'payout_per_call', None) or Decimal('0')
            if per_number > 0:
                return Decimal(per_number)

        campaign_payout = getattr(campaign, 'payout_amount', None) or Decimal('0')
        if campaign_payout > 0:
            return Decimal(campaign_payout)

        return Decimal(getattr(settings, 'MINIMUM_CALL_BALANCE', Decimal('0')))

    @staticmethod
    def check_balance(campaign, phone_number=None) -> bool:
        """False when the organization cannot fund one more call.

        Disabled wholesale by ENFORCE_CALL_BALANCE=False, which restores the
        previous always-route behaviour without a deploy.
        """
        if not getattr(settings, 'ENFORCE_CALL_BALANCE', True):
            return True

        from billing.services import BillingService

        # Our own portal - the workspace the owner logs into - is never
        # refused for balance; only user workspaces are (CH-255).
        if BillingService.is_our_portal(campaign.organization):
            return True

        required = RoutingEngine.required_call_balance(
            campaign, phone_number, campaign.organization
        )
        if required <= 0:
            # Unpriced campaign — nothing configured to charge against.
            return True

        from billing.services import BillingService

        if BillingService.has_sufficient_balance(campaign.organization, required):
            return True

        logger.warning(
            "insufficient_balance: campaign=%s org=%s required=%s balance=%s",
            campaign.id,
            campaign.organization_id,
            required,
            BillingService.get_balance(campaign.organization),
        )
        return False

    @staticmethod
    def route_call(campaign_id: str, call_data: dict) -> dict:
        from campaigns.models import Campaign

        try:
            campaign = Campaign.objects.prefetch_related(
                'routing_rules__conditions',
                'routing_rules__destinations__buyer__cap'
            ).get(id=campaign_id)
        except Campaign.DoesNotExist:
            return {'destination': None, 'rule': None, 'error': 'Campaign not found'}

        caller_number = call_data.get('caller_number', '')

        trace = call_data.get('trace')

        # CH-339: with the campaign, so a block on this campaign only (the
        # Blocked Numbers page's campaign scope, or the automatic do-not-call
        # / fraud entry the checks add for one campaign) refuses the call too.
        # It was asked without it, so only workspace-wide blocks counted: on
        # 28 Oct a caller with a C-03 do-not-call block went live on C-03.
        if RoutingEngine.is_blacklisted(caller_number, str(campaign.organization_id), str(campaign.id)):
            label = RoutingEngine.block_label(caller_number, str(campaign.organization_id), str(campaign.id))
            if trace: trace.step('blacklist', False, label.lower())
            # CH-349: the comment is "CallerId Blocked" for every blocked
            # caller ID; why it was blocked stays in the trace above. An
            # automatic block (do-not-call, spam, fraud) stays a failed row,
            # as before; a block the team made stays missed (CH-206).
            if label in RoutingEngine.CALLER_BLOCK_DETAILS:
                return {'destination': None, 'rule': None, 'error': RoutingEngine.BLOCKED_CALLER_LABEL,
                        'auto_block': label != RoutingEngine.BLOCKED_CALLER_LABEL, 'block_detail': label}
            return {'destination': None, 'rule': None, 'error': label}
        if trace: trace.step('blacklist', True)

        # A caller the team blocked on this publisher only: refused here as a
        # missed call with the same "CallerId Blocked" comment, while the same
        # caller on another publisher's numbers goes through.
        if RoutingEngine.is_publisher_blocked(
            caller_number, str(campaign.organization_id), call_data.get('publisher_id')
        ):
            if trace: trace.step('publisher_block', False, 'caller blocked on this publisher')
            return {'destination': None, 'rule': None, 'error': RoutingEngine.BLOCKED_CALLER_LABEL}

        if campaign.duplicate_call_block:
            if RoutingEngine.is_duplicate(caller_number, str(campaign.id), campaign.duplicate_call_block_hours):
                if trace: trace.step('duplicate', False, f'called within {campaign.duplicate_call_block_hours}h')
                return {'destination': None, 'rule': None, 'error': 'Duplicate call blocked'}
            if trace: trace.step('duplicate', True)

        cap_reason = RoutingEngine.check_campaign_caps(
            campaign, current_sid=call_data.get('twilio_call_sid'))
        if cap_reason:
            if trace: trace.step('campaign_cap', False, cap_reason.lower())
            return {'destination': None, 'rule': None, 'error': cap_reason}
        if trace: trace.step('campaign_cap', True)

        # Funds check runs last of the guardrails — it is the most expensive, and
        # there is no point pricing a call the other rules would have dropped.
        if not RoutingEngine.check_balance(campaign, call_data.get('phone_number')):
            if trace: trace.step('balance', False, 'insufficient balance for one call')
            return {'destination': None, 'rule': None, 'error': RoutingEngine.INSUFFICIENT_FUNDS_LABEL}
        if trace: trace.step('balance', True)

        rules = campaign.routing_rules.filter(
            status=RoutingRule.Status.ACTIVE
        ).order_by('priority')

        # RTB routing
        if campaign.routing_type == 'rtb':
            from rtb.engine import RTBEngine
            auction = RTBEngine.start_auction(
                campaign=campaign,
                caller_number=caller_number,
                caller_state=call_data.get('caller_state', ''),
                twilio_call_sid=call_data.get('twilio_call_sid', '')
            )
            if auction.winner and auction.status == 'closed':
                if campaign.duplicate_call_block:
                    RoutingEngine.mark_as_called(
                        caller_number,
                        str(campaign.id),
                        campaign.duplicate_call_block_hours
                    )
                return {
                    'destination': auction.winner.phone_number,
                    'destination_type': 'phone',
                    'rule': None,
                    'buyer': auction.winner,
                    'error': None
                }
            return {'destination': None, 'rule': None, 'error': 'RTB no winner'}

        if trace:
            for rule in rules:
                trace.count_considered(rule.destinations.count())

        # ── Duplicate handling: Original / Different ────────────────────────
        # Four controls sat in the interface since the beginning - Normal /
        # Original / Different, the Destination/Buyer scope, and Strict - wired
        # to nothing: component state, hardcoded defaults, never saved, never
        # read here. The boss selected Different and duplicates kept forwarding
        # because the selection never left his browser tab.
        dup_mode = getattr(campaign, 'duplicate_handling', 'normal') or 'normal'
        dup_prior = []
        if dup_mode in ('original', 'different') and caller_number:
            from datetime import timedelta
            # Original means the same destination every time this caller
            # rings, however long ago the first call was (Sir, 20 Oct).
            # Different means never the same one again, "no matter what"
            # (Sir, 24 Oct, CH-242): the same long memory, no longer the
            # campaign's duplicate window - after 24 hours a caller could be
            # sent back to a TFN that already had him. The window still limits
            # blocking and the Repeated column.
            cutoff = timezone.now() - timedelta(hours=ORIGINAL_MEMORY_HOURS)
            if dup_mode == 'original':
                prior = CallLog.objects.filter(campaign_id=campaign.id)
            else:
                # Different looks across every campaign of the workspace: the
                # same buyer TFN sits in several campaigns, and a caller who
                # reached it through C-02 must not reach it again through C-03
                # (Sir, 24 Oct, CH-242).
                prior = CallLog.objects.filter(organization_id=campaign.organization_id)
            prior = prior.filter(
                caller_number=caller_number,
                created_at__gte=cutoff,
            ).exclude(destination_number='')
            if dup_mode == 'original':
                # Original pins the caller to the destination that ANSWERED
                # them; a first call nobody answered does not pin them.
                prior = prior.filter(status__in=['completed', 'in_progress'])
            else:
                # Different (Sir, 24 Oct): "in any case not a single call
                # should be forwarded on the same TFN". Every call that was
                # forwarded to a TFN counts - answered, unanswered, refused by
                # the buyer's line, still ringing. Only "No Destination" rows
                # are left out: we refused those ourselves and never dialled
                # the TFN stamped on them. Counting answered calls only sent a
                # caller whose first call rang out straight back to that TFN.
                prior = prior.exclude(block_reason='No Destination')
            dup_prior = list(
                prior.order_by('-created_at').values_list('destination_number', 'buyer_id')
            )
        if dup_prior:
            if dup_mode == 'original':
                call_data['dup_only_destination'] = _d10(dup_prior[0][0])
                if trace: trace.step('duplicate', True, f'repeat caller; held to their original destination {dup_prior[0][0]}')
            elif getattr(campaign, 'duplicate_direction', 'destination') == 'buyer':
                call_data['dup_exclude_buyers'] = {str(b) for _, b in dup_prior if b}
                # And the TFNs he reached (CH-392). One TFN can now be on
                # several buyers; excluding only the buyer would send him to
                # the same line through the other buyer - Sir: "not a single
                # call should be forwarded on the same TFN". With no TFN
                # shared this changes nothing: that buyer is excluded anyway.
                call_data['dup_exclude_destinations'] = {_d10(d) for d, _ in dup_prior if d}
                if trace: trace.step('duplicate', True, f'repeat caller; {len(call_data["dup_exclude_buyers"])} buyer(s) and their TFNs excluded')
            else:
                call_data['dup_exclude_destinations'] = {_d10(d) for d, _ in dup_prior if d}
                if trace: trace.step('duplicate', True, f'repeat caller; {len(call_data["dup_exclude_destinations"])} destination(s) excluded')

        # Buyers who have personally blocked this caller: taken out of the
        # running for this call only - the call still routes to anyone else.
        # Organization-wide blocks refused the call back at the blacklist
        # step, exactly as they always did.
        if caller_number:
            from spam_protection.models import Blacklist as _BL
            from spam_protection.numbers import variants as _variants
            _now = timezone.now()
            _blocked = set(
                str(b) for b in _BL.objects.filter(
                    organization_id=campaign.organization_id,
                    phone_number__in=_variants(caller_number),
                    is_active=True,
                    buyer__isnull=False,
                ).filter(
                    models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=_now)
                ).values_list('buyer_id', flat=True)
            )
            if _blocked:
                call_data['blocked_buyers'] = _blocked
                if trace: trace.step('buyer_block', True, f'{len(_blocked)} buyer(s) skip this caller')

        def _run_rules():
            for rule in rules:
                destination = RoutingEngine.evaluate_rule(rule, call_data)
                if destination:
                    return rule, destination
            return None, None

        matched_rule, destination = _run_rules()

        if not destination and dup_prior:
            if dup_mode == 'different':
                # The boss's spec, verbatim: on Different a duplicate call
                # appears in the portal as a missed call and is NOT forwarded
                # to a TFN that already took this caller - whether or not
                # Strict is on. The caller is not blocked: this call lands in
                # Incoming and in the duplicate report, and their next call
                # gets the same treatment, not a blacklist entry.
                if trace: trace.step('duplicate', False, 'no destination this caller has not already reached; shown as missed, not forwarded')
                return {'destination': None, 'rule': None, 'error': 'Duplicate call'}
            # Original whose destination is gone: forward like a regular call
            # rather than lose it - Normal/Original always forward.
            for key in ('dup_only_destination', 'dup_exclude_destinations', 'dup_exclude_buyers'):
                call_data.pop(key, None)
            if trace: trace.step('duplicate', True, 'original destination unavailable; routed normally')
            matched_rule, destination = _run_rules()

        if destination:
            # CH-358: a test or a TFN Check readiness look is not a call - it
            # must not mark its caller as called, or the next look is refused
            # as a duplicate.
            if campaign.duplicate_call_block and not call_data.get('is_test'):
                RoutingEngine.mark_as_called(
                    caller_number,
                    str(campaign.id),
                    campaign.duplicate_call_block_hours
                )
            return {
                'destination': destination.destination,
                'destination_type': destination.destination_type,
                'rule': matched_rule,
                'buyer': destination.buyer,
                'error': None,
                # The buyer's own TFN is chosen after this (pick_live_destination),
                # and it must keep to the same duplicate rules (CH-241).
                'rule_destination_id': destination.id,
                'dup_exclude_tfns': set(call_data.get('dup_exclude_destinations', ())),
                'dup_only_tfn': call_data.get('dup_only_destination'),
            }

        refused = call_data.get('refused_destination')
        if refused:
            if trace: trace.step('rule_match', False,
                                 f"{refused['label'].lower()} on {refused['tfn']}")
            return {'destination': None, 'rule': None, 'error': refused['label'],
                    'refused_destination': refused['tfn'],
                    'refused_buyer': refused['buyer']}

        if trace: trace.step('rule_match', False, 'no rule produced a destination')
        return {'destination': None, 'rule': None, 'error': 'No matching rule found'}