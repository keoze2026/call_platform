from django.conf import settings
from .models import IVRFlow, IVRNode, IVRNodeTransition
from .twiml_builder import TwiMLBuilder
from accounts.models import User
import logging

logger = logging.getLogger(__name__)


class IVRService:

    @staticmethod
    def create_flow(data, user: User) -> IVRFlow:
        if not user.organization:
            raise ValueError("User has no organization")

        campaign = None
        if data.campaign_id:
            from campaigns.models import Campaign
            try:
                campaign = Campaign.objects.get(
                    id=data.campaign_id,
                    organization=user.organization
                )
            except Campaign.DoesNotExist:
                raise ValueError("Campaign not found")

        flow = IVRFlow.objects.create(
            organization=user.organization,
            created_by=user,
            campaign=campaign,
            name=data.name,
            description=data.description or '',
            welcome_message=data.welcome_message or '',
            language=data.language or 'en-US',
            voice=data.voice or 'alice',
            status=IVRFlow.Status.DRAFT
        )

        return flow

    @staticmethod
    def get_flow(flow_id: str, user: User) -> IVRFlow:
        try:
            return IVRFlow.objects.prefetch_related(
                'nodes__transitions'
            ).get(
                id=flow_id,
                organization=user.organization
            )
        except IVRFlow.DoesNotExist:
            raise ValueError("IVR flow not found")

    @staticmethod
    def list_flows(user: User):
        return IVRFlow.objects.filter(
            organization=user.organization
        ).select_related('campaign').order_by('-created_at')

    @staticmethod
    def update_flow(flow_id: str, data, user: User) -> IVRFlow:
        flow = IVRService.get_flow(flow_id, user)

        if data.name is not None:
            flow.name = data.name
        if data.description is not None:
            flow.description = data.description
        if data.welcome_message is not None:
            flow.welcome_message = data.welcome_message
        if data.language is not None:
            flow.language = data.language
        if data.voice is not None:
            flow.voice = data.voice
        if data.status is not None:
            flow.status = data.status
        if data.campaign_id is not None:
            from campaigns.models import Campaign
            try:
                campaign = Campaign.objects.get(
                    id=data.campaign_id,
                    organization=user.organization
                )
                flow.campaign = campaign
            except Campaign.DoesNotExist:
                raise ValueError("Campaign not found")

        flow.save()
        return flow

    @staticmethod
    def delete_flow(flow_id: str, user: User):
        flow = IVRService.get_flow(flow_id, user)
        flow.delete()

    @staticmethod
    def add_node(flow_id: str, data, user: User) -> IVRNode:
        flow = IVRService.get_flow(flow_id, user)

        if data.is_entry_point:
            IVRNode.objects.filter(flow=flow, is_entry_point=True).update(is_entry_point=False)

        node = IVRNode.objects.create(
            flow=flow,
            node_type=data.node_type,
            name=data.name,
            position=data.position,
            is_entry_point=data.is_entry_point,
            config=data.config.dict()
        )

        return node

    @staticmethod
    def add_transition(flow_id: str, data, user: User) -> IVRNodeTransition:
        flow = IVRService.get_flow(flow_id, user)

        try:
            from_node = IVRNode.objects.get(id=data.from_node_id, flow=flow)
        except IVRNode.DoesNotExist:
            raise ValueError("From node not found")

        to_node = None
        if data.to_node_id:
            try:
                to_node = IVRNode.objects.get(id=data.to_node_id, flow=flow)
            except IVRNode.DoesNotExist:
                raise ValueError("To node not found")

        transition = IVRNodeTransition.objects.create(
            from_node=from_node,
            to_node=to_node,
            trigger=data.trigger
        )

        return transition

    @staticmethod
    def execute_flow(flow_id: str, digit: str = None, node_id: str = None) -> str:
        base_url = getattr(settings, 'BASE_URL', 'http://127.0.0.1:8000')

        try:
            flow = IVRFlow.objects.prefetch_related(
                'nodes__transitions'
            ).get(id=flow_id, status=IVRFlow.Status.ACTIVE)
        except IVRFlow.DoesNotExist:
            return '<?xml version="1.0" encoding="UTF-8"?><Response><Say>Flow not found</Say><Hangup/></Response>'

        if node_id and digit:
            try:
                current_node = IVRNode.objects.get(id=node_id, flow=flow)
                transition = current_node.transitions.filter(trigger=digit).first()
                if not transition:
                    transition = current_node.transitions.filter(trigger='default').first()
                if transition and transition.to_node:
                    return TwiMLBuilder.build_flow(flow, transition.to_node, base_url)
            except IVRNode.DoesNotExist:
                # Falls through to the flow's default handling below. Logged
                # because it means a live menu is pointing at a deleted node.
                logger.warning('ivr node missing mid-flow: node=%s flow=%s', node_id, flow.id)

        entry_node = flow.nodes.filter(is_entry_point=True).first()
        if not entry_node:
            entry_node = flow.nodes.first()

        if not entry_node:
            return '<?xml version="1.0" encoding="UTF-8"?><Response><Say>No nodes configured</Say><Hangup/></Response>'

        return TwiMLBuilder.build_flow(flow, entry_node, base_url)

    @staticmethod
    def format_flow(flow: IVRFlow) -> dict:
        nodes = []
        for node in flow.nodes.all():
            transitions = [
                {
                    'id': str(t.id),
                    'trigger': t.trigger,
                    'to_node_id': str(t.to_node_id) if t.to_node_id else None,
                    'to_node_name': t.to_node.name if t.to_node else None,
                }
                for t in node.transitions.all()
            ]
            nodes.append({
                'id': str(node.id),
                'node_type': node.node_type,
                'name': node.name,
                'position': node.position,
                'is_entry_point': node.is_entry_point,
                'config': node.config,
                'transitions': transitions,
            })

        return {
            'id': str(flow.id),
            'name': flow.name,
            'description': flow.description,
            'status': flow.status,
            'welcome_message': flow.welcome_message,
            'language': flow.language,
            'voice': flow.voice,
            'campaign_id': str(flow.campaign_id) if flow.campaign_id else None,
            'campaign_name': flow.campaign.name if flow.campaign else None,
            'organization_id': str(flow.organization_id),
            'nodes': nodes,
            'created_at': flow.created_at.isoformat(),
            'updated_at': flow.updated_at.isoformat(),
        }