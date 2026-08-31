from .models import RoutingRule, RuleCondition, RuleDestination, CallLog
from .schemas import CreateRoutingRuleSchema, UpdateRoutingRuleSchema
from accounts.models import User


class RoutingService:

    @staticmethod
    def create_rule(data: CreateRoutingRuleSchema, user: User) -> RoutingRule:
        from campaigns.models import Campaign

        try:
            campaign = Campaign.objects.get(
                id=data.campaign_id,
                organization=user.organization
            )
        except Campaign.DoesNotExist:
            raise ValueError("Campaign not found")

        rule = RoutingRule.objects.create(
            organization=user.organization,
            campaign=campaign,
            created_by=user,
            name=data.name,
            rule_type=data.rule_type,
            priority=data.priority,
            status=RoutingRule.Status.ACTIVE
        )

        return rule

    @staticmethod
    def get_rule(rule_id: str, user: User) -> RoutingRule:
        try:
            return RoutingRule.objects.select_related(
                'campaign', 'created_by'
            ).prefetch_related(
                'conditions', 'destinations__buyer'
            ).get(
                id=rule_id,
                organization=user.organization
            )
        except RoutingRule.DoesNotExist:
            raise ValueError("Routing rule not found")

    @staticmethod
    def list_rules(user: User, campaign_id: str = None):
        from django.db.models import Count

        qs = RoutingRule.objects.select_related('campaign').filter(
            organization=user.organization
        ).annotate(
            destination_count=Count('destinations', distinct=True),
            condition_count=Count('conditions', distinct=True),
        )
        if campaign_id:
            qs = qs.filter(campaign_id=campaign_id)
        return qs.order_by('priority')

    @staticmethod
    def update_rule(rule_id: str, data: UpdateRoutingRuleSchema, user: User) -> RoutingRule:
        rule = RoutingService.get_rule(rule_id, user)

        ALLOWED_FIELDS = {'name', 'rule_type', 'priority', 'status'}

        for field, value in data.model_dump(exclude_none=True).items():
            if field in ALLOWED_FIELDS:
                setattr(rule, field, value)

        rule.save()
        return rule

    @staticmethod
    def delete_rule(rule_id: str, user: User):
        rule = RoutingService.get_rule(rule_id, user)
        rule.delete()

    @staticmethod
    def add_condition(rule_id: str, data, user: User) -> RuleCondition:
        rule = RoutingService.get_rule(rule_id, user)

        condition = RuleCondition.objects.create(
            rule=rule,
            conditions=data.conditions
        )

        return condition

    @staticmethod
    def add_destination(rule_id: str, data, user: User) -> RuleDestination:
        rule = RoutingService.get_rule(rule_id, user)

        buyer = None
        if data.buyer_id:
            from buyers.models import Buyer
            try:
                buyer = Buyer.objects.get(
                    id=data.buyer_id,
                    organization=user.organization
                )
            except Buyer.DoesNotExist:
                raise ValueError("Buyer not found")

        destination_value = data.destination or data.phone_number
        if not destination_value:
            raise ValueError("destination or phone_number is required")

        destination = RuleDestination.objects.create(
            rule=rule,
            destination_type=data.destination_type,
            destination=destination_value,
            buyer=buyer,
            priority=data.priority,
            weight=data.weight
        )

        return destination

    @staticmethod
    def list_destinations(rule_id: str, user: User):
        rule = RoutingService.get_rule(rule_id, user)
        return rule.destinations.select_related('buyer').all()

    @staticmethod
    def get_destination(rule_id: str, destination_id: str, user: User) -> RuleDestination:
        rule = RoutingService.get_rule(rule_id, user)
        try:
            return RuleDestination.objects.select_related('buyer').get(
                id=destination_id,
                rule=rule
            )
        except RuleDestination.DoesNotExist:
            raise ValueError("Destination not found")

    @staticmethod
    def update_destination(rule_id: str, destination_id: str, data, user: User) -> RuleDestination:
        destination = RoutingService.get_destination(rule_id, destination_id, user)

        if data.destination_type is not None:
            valid_types = [c[0] for c in RuleDestination.DestinationType.choices]
            if data.destination_type not in valid_types:
                raise ValueError(f"destination_type must be one of: {', '.join(valid_types)}")
            destination.destination_type = data.destination_type

        # 'destination' and 'phone_number' are aliases, matching add_destination
        destination_value = data.destination or data.phone_number
        if destination_value is not None:
            destination.destination = destination_value

        if getattr(data, '_detach_buyer', False):
            destination.buyer = None
        elif data.buyer_id is not None:
            from buyers.models import Buyer
            try:
                destination.buyer = Buyer.objects.get(
                    id=data.buyer_id,
                    organization=user.organization
                )
            except Buyer.DoesNotExist:
                raise ValueError("Buyer not found")

        if data.priority is not None:
            destination.priority = data.priority

        if data.weight is not None:
            destination.weight = data.weight

        destination.save()
        return destination

    @staticmethod
    def delete_destination(rule_id: str, destination_id: str, user: User):
        destination = RoutingService.get_destination(rule_id, destination_id, user)
        destination.delete()

    @staticmethod
    def format_destination(destination: RuleDestination) -> dict:
        return {
            'id': str(destination.id),
            'destination_type': destination.destination_type,
            'destination': destination.destination,
            'priority': destination.priority,
            'weight': destination.weight,
            'buyer_id': str(destination.buyer_id) if destination.buyer_id else None,
            'buyer_name': destination.buyer.name if destination.buyer else None,
        }

    @staticmethod
    def list_calls(user: User, campaign_id: str = None):
        qs = CallLog.objects.select_related(
            'campaign', 'buyer', 'publisher'
        ).filter(organization=user.organization)

        if campaign_id:
            qs = qs.filter(campaign_id=campaign_id)

        return qs.order_by('-created_at')[:100]

    @staticmethod
    def get_call(call_id: str, user: User) -> CallLog:
        try:
            return CallLog.objects.select_related(
                'campaign', 'buyer', 'publisher', 'routing_rule'
            ).get(
                id=call_id,
                organization=user.organization
            )
        except CallLog.DoesNotExist:
            raise ValueError("Call log not found")

    @staticmethod
    def format_rule(rule: RoutingRule) -> dict:
        conditions = [
            {
                'id': str(c.id),
                'conditions': c.conditions,
            }
            for c in rule.conditions.all()
        ]

        destinations = [
            {
                'id': str(d.id),
                'destination_type': d.destination_type,
                'destination': d.destination,
                'priority': d.priority,
                'weight': d.weight,
                'buyer_id': str(d.buyer_id) if d.buyer_id else None,
                'buyer_name': d.buyer.name if d.buyer else None,
            }
            for d in rule.destinations.all()
        ]

        return {
            'id': str(rule.id),
            'name': rule.name,
            'rule_type': rule.rule_type,
            'priority': rule.priority,
            'status': rule.status,
            'campaign_id': str(rule.campaign_id),
            'campaign_name': rule.campaign.name,
            'conditions': conditions,
            'destinations': destinations,
            'created_at': rule.created_at.isoformat(),
            'updated_at': rule.updated_at.isoformat(),
        }

    @staticmethod
    def format_call(call: CallLog) -> dict:
        return {
            'id': str(call.id),
            'caller_number': call.caller_number,
            'called_number': call.called_number,
            'destination_number': call.destination_number,
            'status': call.status,
            'duration': call.duration,
            'caller_area_code': call.caller_area_code,
            'caller_state': call.caller_state,
            'caller_country': call.caller_country,
            'campaign_id': str(call.campaign_id) if call.campaign_id else None,
            'campaign_name': call.campaign.name if call.campaign else None,
            'buyer_id': str(call.buyer_id) if call.buyer_id else None,
            'buyer_name': call.buyer.name if call.buyer else None,
            'publisher_id': str(call.publisher_id) if call.publisher_id else None,
            'publisher_name': call.publisher.name if call.publisher else None,
            'revenue': str(call.revenue),
            'buyer_payout': str(call.buyer_payout),
            'publisher_payout': str(call.publisher_payout),
            'recording_url': call.recording_url,
            'tags': call.tags,
            'notes': call.notes,
            'created_at': call.created_at.isoformat(),
            'updated_at': call.updated_at.isoformat(),
            'transcription_text': call.transcription_text,
            'transcription_status': call.transcription_status,
            'sentiment': call.sentiment,
            'sentiment_score': call.sentiment_score,
        }
