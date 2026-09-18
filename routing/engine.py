import logging
import random
from datetime import datetime
from decimal import Decimal
from django.conf import settings
from django.utils import timezone
from django.core.cache import cache
from .models import RoutingRule, RuleCondition, RuleDestination, CallLog

logger = logging.getLogger(__name__)



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
    def is_duplicate(caller_number: str, campaign_id: str, block_hours: int) -> bool:
        from datetime import timedelta
        if block_hours <= 0:
            return False
        cutoff = timezone.now() - timedelta(hours=block_hours)
        return CallLog.objects.filter(
            campaign_id=campaign_id,
            caller_number=caller_number,
            created_at__gte=cutoff,
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
    def check_campaign_caps(campaign) -> bool:
        try:
            cap = campaign.cap
        except Exception:
            return True

        today = timezone.now().date()
        month_start = today.replace(day=1)

        if cap.max_calls_daily > 0:
            daily_count = CallLog.objects.filter(
                campaign=campaign,
                created_at__date=today
            ).count()
            if daily_count >= cap.max_calls_daily:
                return False

        if cap.max_calls_monthly > 0:
            monthly_count = CallLog.objects.filter(
                campaign=campaign,
                created_at__date__gte=month_start
            ).count()
            if monthly_count >= cap.max_calls_monthly:
                return False

        if cap.max_calls_global > 0:
            global_count = CallLog.objects.filter(campaign=campaign).count()
            if global_count >= cap.max_calls_global:
                return False

        return True

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
                pass

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
    def get_round_robin_destination(rule: RoutingRule) -> RuleDestination:
        destinations = list(rule.destinations.all().order_by('priority'))
        if not destinations:
            return None
        cache_key = f"round_robin_{rule.id}"
        current_index = cache.get(cache_key, 0)
        destination = destinations[current_index % len(destinations)]
        cache.set(cache_key, (current_index + 1) % len(destinations), timeout=86400)
        return destination

    @staticmethod
    def get_weighted_destination(rule: RoutingRule) -> RuleDestination:
        destinations = list(rule.destinations.all())
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
    def get_valid_destination(rule: RoutingRule, call_data: dict):
        destinations = list(rule.destinations.all().order_by('priority'))
        for destination in destinations:
            if destination.buyer:
                buyer = destination.buyer
                if buyer.status != 'active':
                    continue
                if not RoutingEngine.check_buyer_caps(buyer):
                    continue
                if not RoutingEngine.check_buyer_concurrency(buyer, destination_number=destination.destination):
                    continue
            return destination
        return None

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
            return RoutingEngine.get_round_robin_destination(rule)

        elif rule.rule_type == RoutingRule.RuleType.WEIGHTED:
            return RoutingEngine.get_weighted_destination(rule)

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

        if RoutingEngine.is_blacklisted(caller_number, str(campaign.organization_id)):
            return {'destination': None, 'rule': None, 'error': 'Caller is blacklisted'}

        if campaign.duplicate_call_block:
            if RoutingEngine.is_duplicate(caller_number, str(campaign.id), campaign.duplicate_call_block_hours):
                return {'destination': None, 'rule': None, 'error': 'Duplicate call blocked'}

        if not RoutingEngine.check_campaign_caps(campaign):
            return {'destination': None, 'rule': None, 'error': 'Campaign cap reached'}

        # Funds check runs last of the guardrails — it is the most expensive, and
        # there is no point pricing a call the other rules would have dropped.
        if not RoutingEngine.check_balance(campaign, call_data.get('phone_number')):
            return {'destination': None, 'rule': None, 'error': 'insufficient_balance'}

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

        for rule in rules:
            destination = RoutingEngine.evaluate_rule(rule, call_data)
            if destination:
                if campaign.duplicate_call_block:
                    RoutingEngine.mark_as_called(
                        caller_number,
                        str(campaign.id),
                        campaign.duplicate_call_block_hours
                    )
                return {
                    'destination': destination.destination,
                    'destination_type': destination.destination_type,
                    'rule': rule,
                    'buyer': destination.buyer,
                    'error': None
                }

        return {'destination': None, 'rule': None, 'error': 'No matching rule found'}