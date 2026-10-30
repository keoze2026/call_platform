"""Campaign Groups (CH-328; Sir, 28 Oct): one edit, every campaign in the group.

A destination (the buyer's TFN) added, removed, or given a new priority or
weight on a group is changed in each member campaign's own destination list -
the same list the campaign page shows under "Send calls to", the same rows a
person would edit by hand. Nothing in call routing reads groups: a call is
routed exactly as before, by its campaign's rule.

Which list: the campaign page edits the campaign's first active routing rule
(else its first rule) - components/campaigns/settings/forward-calls-section.tsx
- and this uses the same one, so what the group changes is what the page
shows. Destinations in a campaign's other (advanced) rules are left alone and
reported, never silently changed.

One change across several groups is one transaction: every campaign gets it,
or, if anything is refused, none does.
"""
import re
from decimal import Decimal, InvalidOperation
from django.db import transaction

from .models import Campaign, CampaignGroup

ADD, REMOVE, UPDATE = 'add', 'remove', 'update'
CONVERT_ON = ('incoming', 'connected', 'length')


def digits_key(number) -> str:
    """The last ten digits: '+1 (800) 555-0100' and '18005550100' are one line."""
    return re.sub(r'\D', '', str(number or ''))[-10:]


def to_e164(number) -> str:
    d = re.sub(r'\D', '', str(number or ''))
    if len(d) == 10:
        return '+1' + d
    if len(d) == 11 and d.startswith('1'):
        return '+' + d
    return '+' + d if d else ''


def campaign_rule(campaign, create=False, user=None):
    """The rule the campaign page shows: first active by priority, else first."""
    from routing.models import RoutingRule
    rules = list(RoutingRule.objects.filter(campaign=campaign).order_by('priority', 'created_at'))
    rule = next((r for r in rules if r.status == RoutingRule.Status.ACTIVE), None) or (rules[0] if rules else None)
    if rule or not create:
        return rule
    # As the campaign page's "Add destination" does for a campaign with none.
    return RoutingRule.objects.create(
        organization=campaign.organization, campaign=campaign, created_by=user,
        name=f'{campaign.name} routing', rule_type=RoutingRule.RuleType.PRIORITY, priority=1,
        status=RoutingRule.Status.ACTIVE)


def _members(groups):
    return list(Campaign.objects.filter(group__in=groups).exclude(status=Campaign.Status.ARCHIVED)
                .select_related('group').order_by('group__name', 'name'))


def group_destinations(group):
    """Every destination across the group's campaigns, with where it is and
    its priority and weight in each - so differences show at a glance."""
    campaigns = _members([group])
    lines = {}
    for c in campaigns:
        rule = campaign_rule(c)
        if not rule:
            continue
        for d in rule.destinations.select_related('buyer').all():
            k = digits_key(d.destination)
            line = lines.setdefault(k, {'number': to_e164(d.destination) or d.destination,
                                        'buyer_id': str(d.buyer_id) if d.buyer_id else None,
                                        'buyer_name': d.buyer.name if d.buyer_id else '', 'campaigns': []})
            line['campaigns'].append({'campaign_id': str(c.id), 'campaign_name': c.name, 'id': str(d.id),
                                      'priority': d.priority, 'weight': d.weight,
                                      'revenue': str(d.revenue) if d.revenue is not None else None,
                                      'convert_on': d.convert_on, 'convert_seconds': d.convert_seconds})
    out = []
    member_ids = [str(c.id) for c in campaigns]
    for line in lines.values():
        present = {x['campaign_id'] for x in line['campaigns']}
        line['missing_in'] = [{'campaign_id': str(c.id), 'campaign_name': c.name}
                              for c in campaigns if str(c.id) not in present]
        line['in_count'] = len(present)
        line['of_count'] = len(member_ids)
        line['priorities'] = sorted({x['priority'] for x in line['campaigns']})
        line['weights'] = sorted({x['weight'] for x in line['campaigns']})
        out.append(line)
    out.sort(key=lambda x: (min(x['priorities']), x['buyer_name'], x['number']))
    return out


def _billing(data, required):
    """Rate and Convert ON: mandatory when a destination is added (as on the
    campaign page, CH-248); optional on a change."""
    out = {}
    rate = data.get('revenue')
    if rate is not None and rate != '':
        try:
            v = Decimal(str(rate)).quantize(Decimal('0.01'))
        except (InvalidOperation, ValueError):
            raise ValueError('Rate must be a number')
        if v < 0:
            raise ValueError('Rate cannot be negative')
        out['revenue'] = v
    elif required:
        raise ValueError('Rate is required when adding a destination')
    conv = (data.get('convert_on') or '').strip().lower()
    if conv:
        if conv not in CONVERT_ON:
            raise ValueError('Convert ON must be Incoming Call, Call Connected or Call Length')
        out['convert_on'] = conv
        secs = int(data.get('convert_seconds') or 0)
        if conv == 'length' and secs <= 0:
            raise ValueError('Call Length needs a number of seconds above 0')
        out['convert_seconds'] = secs if conv == 'length' else 0
    elif required:
        raise ValueError('Convert ON is required when adding a destination')
    return out


def _int(value, name, low, high):
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise ValueError(f'{name} must be a whole number')
    if not low <= v <= high:
        raise ValueError(f'{name} must be between {low} and {high}')
    return v


def apply(user, group_ids, action, number, buyer_id=None, priority=None, weight=None, **billing):
    """Add, remove or update one destination in every campaign of the groups.

    Returns {'action', 'number', 'groups': [...], 'results': [{campaign, group,
    result, detail}], 'changed': n}. Raises ValueError (nothing changed) when
    the request is not valid."""
    from routing.models import RuleDestination
    if action not in (ADD, REMOVE, UPDATE):
        raise ValueError('action must be add, remove or update')
    key = digits_key(number)
    if len(key) != 10:
        raise ValueError('A destination number needs 10 digits')
    groups = list(CampaignGroup.objects.filter(organization=user.organization, id__in=list(group_ids or [])))
    if not groups or len(groups) != len(set(map(str, group_ids))):
        raise ValueError('Group not found')
    campaigns = _members(groups)
    if not campaigns:
        raise ValueError('These groups have no campaigns yet')

    fields = {}
    if priority is not None and priority != '':
        fields['priority'] = _int(priority, 'Priority', 1, 999)
    if weight is not None and weight != '':
        fields['weight'] = _int(weight, 'Weight', 0, 1000)
    fields.update(_billing(billing, required=action == ADD))
    if action == UPDATE and not fields:
        raise ValueError('Nothing to change: give a priority, weight, rate or Convert ON')

    buyer = None
    if action == ADD:
        from buyers.destination import Destination
        from buyers.models import Buyer
        if buyer_id:
            buyer = Buyer.objects.filter(id=buyer_id, organization=user.organization).first()
            if buyer is None:
                raise ValueError('Buyer not found')
        else:
            # The buyer that owns this number on the Destinations page. One
            # number can be on several buyers (CH-392); the portal sends the
            # buyer it means, and without one a live destination wins.
            d = (Destination.objects.filter(organization=user.organization).exclude(buyer=None)
                 .select_related('buyer').order_by('-enabled', 'created_at'))
            d = next((x for x in d if digits_key(x.tfn) == key), None)
            buyer = d.buyer if d else None
        fields.setdefault('priority', 1)
        fields.setdefault('weight', 100)

    results = []
    _via_group([g.name for g in groups])
    with transaction.atomic():
        for c in campaigns:
            rule = campaign_rule(c, create=action == ADD, user=user)
            rows = [d for d in (rule.destinations.all() if rule else []) if digits_key(d.destination) == key]
            others = _in_other_rules(c, rule, key)
            note = ' · also in an advanced routing plan (left as is)' if others else ''
            row = {'campaign_id': str(c.id), 'campaign_name': c.name, 'group': c.group.name}
            if action == ADD:
                if rows:
                    results.append({**row, 'result': 'unchanged', 'detail': 'already there' + note})
                    continue
                new = RuleDestination(rule=rule, destination_type=RuleDestination.DestinationType.PHONE,
                                      destination=to_e164(number), buyer=buyer)
                for f, v in fields.items():
                    setattr(new, f, v)
                new.save()
                results.append({**row, 'result': 'added', 'detail': f'priority {new.priority} · weight {new.weight}' + note})
            elif action == REMOVE:
                if not rows:
                    results.append({**row, 'result': 'unchanged', 'detail': 'not in this campaign' + note})
                    continue
                for d in rows:
                    d.delete()
                results.append({**row, 'result': 'removed', 'detail': note.lstrip(' ·')})
            else:
                if not rows:
                    results.append({**row, 'result': 'unchanged', 'detail': 'not in this campaign' + note})
                    continue
                changes = []
                for d in rows:
                    for f, v in fields.items():
                        old = getattr(d, f)
                        if old != v:
                            changes.append(f'{f.replace("_", " ")} {old}→{v}')
                            setattr(d, f, v)
                    d.save()
                results.append({**row, 'result': 'changed' if changes else 'unchanged',
                                'detail': (', '.join(dict.fromkeys(changes)) or 'already set') + note})
    changed = sum(1 for r in results if r['result'] != 'unchanged')
    _log(user, groups, action, to_e164(number), fields, changed, len(campaigns))
    return {'action': action, 'number': to_e164(number), 'groups': [g.name for g in groups],
            'results': results, 'changed': changed, 'campaigns': len(campaigns)}


def apply_many(user, group_ids, action, items, priority=None, weight=None, **billing):
    """CH-353 (Sir, 29 Oct: "under Destination give check box to add bulk"):
    several destinations in one change. Each item is {number, buyer_id?,
    revenue?, convert_on?, convert_seconds?}; an item's own Rate and Convert
    ON win over the shared ones (each buyer has its own rate).

    One transaction for all of them: if any destination is refused in any
    campaign, nothing is changed. Results carry their number."""
    items = [i for i in (items or []) if digits_key((i or {}).get('number'))]
    if not items:
        raise ValueError('Pick at least one destination')
    keys = [digits_key(i['number']) for i in items]
    if len(set(keys)) != len(keys):
        raise ValueError('The same destination is picked twice')
    out = {'action': action, 'numbers': [], 'groups': [], 'results': [], 'changed': 0, 'campaigns': 0}
    with transaction.atomic():
        for item in items:
            own = {k: item.get(k) for k in ('revenue', 'convert_on', 'convert_seconds')
                   if item.get(k) not in (None, '')}
            try:
                one = apply(user, group_ids, action, item['number'], buyer_id=item.get('buyer_id'),
                            priority=priority, weight=weight, **{**billing, **own})
            except ValueError as e:
                raise ValueError(f'{to_e164(item["number"])}: {e}')
            out['numbers'].append(one['number'])
            out['groups'] = one['groups']
            out['campaigns'] = one['campaigns']
            out['changed'] += one['changed']
            out['results'] += [{**r, 'number': one['number']} for r in one['results']]
    out['number'] = out['numbers'][0]
    return out


def set_line(user, group, number, campaigns, buyer_id=None, **billing):
    """CH-415 (Sir's Campaign Group design): one destination in a group, in
    exactly the campaigns marked included, each with its own priority and
    weight. Campaigns not listed are left as they are.

    `campaigns` is [{campaign_id, included, priority?, weight?}]. Included and
    missing -> added (Rate and Convert ON given, else copied from the line's
    row in another campaign of the group); included and there -> its priority
    / weight set; not included and there -> removed. One transaction: all of
    it, or (on any refusal) none of it."""
    from routing.models import RuleDestination
    key = digits_key(number)
    if len(key) != 10:
        raise ValueError('A destination number needs 10 digits')
    members = {str(c.id): c for c in _members([group])}
    wanted, seen = [], set()
    for item in campaigns or []:
        cid = str((item or {}).get('campaign_id') or '')
        c = members.get(cid)
        if c is None:
            raise ValueError('Campaign not in this group')
        if cid in seen:
            raise ValueError(f'{c.name} is listed twice')
        seen.add(cid)
        fields = {}
        if item.get('priority') not in (None, ''):
            fields['priority'] = _int(item['priority'], 'Priority', 1, 999)
        if item.get('weight') not in (None, ''):
            fields['weight'] = _int(item['weight'], 'Weight', 0, 1000)
        wanted.append((c, bool(item.get('included')), fields))
    if not wanted:
        raise ValueError('Pick at least one campaign')

    # The line as it is now in the group: its buyer and its billing, for the
    # campaigns it is added to.
    existing = []
    for c in members.values():
        rule = campaign_rule(c)
        if rule:
            existing += [d for d in rule.destinations.select_related('buyer').all() if digits_key(d.destination) == key]
    given = _billing(billing, required=False)
    buyer = None
    if buyer_id:
        from buyers.models import Buyer
        buyer = Buyer.objects.filter(id=buyer_id, organization=user.organization).first()
        if buyer is None:
            raise ValueError('Buyer not found')
    elif existing:
        buyer = next((d.buyer for d in existing if d.buyer_id), None)
    else:
        from buyers.destination import Destination
        d = (Destination.objects.filter(organization=user.organization).exclude(buyer=None)
             .select_related('buyer').order_by('-enabled', 'created_at'))
        d = next((x for x in d if digits_key(x.tfn) == key), None)
        buyer = d.buyer if d else None
    model = existing[0] if existing else None
    adds = {}
    if any(inc for _, inc, _ in wanted):
        if model is not None:
            adds_billing = {'revenue': model.revenue, 'convert_on': model.convert_on,
                            'convert_seconds': model.convert_seconds}
            adds_billing.update(given)
        else:
            adds_billing = _billing(billing, required=True)
        adds = adds_billing

    results = []
    _via_group([group.name])
    with transaction.atomic():
        for c, included, fields in wanted:
            rule = campaign_rule(c, create=included, user=user)
            rows = [d for d in (rule.destinations.all() if rule else []) if digits_key(d.destination) == key]
            note = ' · also in an advanced routing plan (left as is)' if _in_other_rules(c, rule, key) else ''
            row = {'campaign_id': str(c.id), 'campaign_name': c.name, 'group': group.name}
            if included and not rows:
                new = RuleDestination(rule=rule, destination_type=RuleDestination.DestinationType.PHONE,
                                      destination=to_e164(number), buyer=buyer,
                                      priority=fields.get('priority', 1), weight=fields.get('weight', 100))
                for f, v in adds.items():
                    setattr(new, f, v)
                new.save()
                results.append({**row, 'result': 'added', 'detail': f'priority {new.priority} · weight {new.weight}' + note})
            elif included:
                changes = []
                for d in rows:
                    for f, v in fields.items():
                        old = getattr(d, f)
                        if old != v:
                            changes.append(f'{f} {old}→{v}')
                            setattr(d, f, v)
                    for f, v in given.items():
                        old = getattr(d, f)
                        if old != v:
                            changes.append(f'{f.replace("_", " ")} {old}→{v}')
                            setattr(d, f, v)
                    d.save()
                results.append({**row, 'result': 'changed' if changes else 'unchanged',
                                'detail': (', '.join(dict.fromkeys(changes)) or 'already set') + note})
            elif rows:
                for d in rows:
                    d.delete()
                results.append({**row, 'result': 'removed', 'detail': note.lstrip(' ·')})
            else:
                results.append({**row, 'result': 'unchanged', 'detail': 'not in this campaign' + note})
    changed = sum(1 for r in results if r['result'] != 'unchanged')
    added = [r['campaign_name'] for r in results if r['result'] == 'added']
    removed = [r['campaign_name'] for r in results if r['result'] == 'removed']
    edited = [r['campaign_name'] for r in results if r['result'] == 'changed']
    parts = ([f'added in {", ".join(added)}'] if added else []) + \
            ([f'removed from {", ".join(removed)}'] if removed else []) + \
            ([f'changed in {", ".join(edited)}'] if edited else [])
    if parts:
        _log_text(user, group, f'destination {to_e164(number)} ' + '; '.join(parts))
    return {'action': 'set', 'number': to_e164(number), 'groups': [group.name],
            'results': results, 'changed': changed, 'campaigns': len(wanted)}


def history(group, limit=100):
    """CH-415: the group's Change history tab - the activity lines written for
    it (destinations added, removed or changed through it, campaigns moved in
    or out), newest first."""
    from accounts.models import ActivityLog
    qs = (ActivityLog.objects.filter(metadata__target_type='campaign_group', metadata__target_id=str(group.id))
          .select_related('user').order_by('-created_at')[:limit])
    out = []
    for a in qs:
        m = a.metadata or {}
        text = m.get('group_action') or ''
        changes = m.get('changes') or {}
        if not text and 'campaigns' in changes:
            ch = changes['campaigns'] or {}
            text = f'campaigns: {ch.get("old", "")} → {ch.get("new", "")}'
        elif changes and 'campaigns' not in changes:
            shown = ', '.join(f'{k.replace("_", " ")} {v.get("new", "")}' for k, v in changes.items()
                              if isinstance(v, dict))
            if shown:
                text = f'{text} ({shown})' if text else shown
        u = a.user
        out.append({'id': str(a.id), 'at': a.created_at.isoformat(),
                    'by': (u.get_full_name() or u.email or u.username) if u else '',
                    'text': text})
    return out


def _log_text(user, group, text):
    from accounts.models import ActivityLog
    try:
        ActivityLog.objects.create(
            user=user, action=ActivityLog.Action.RECORD_UPDATED,
            metadata={'target_type': 'campaign_group', 'target_id': str(group.id), 'target_name': group.name,
                      'group_action': text, 'changes': {}})
    except Exception:  # noqa: BLE001 - the log line never costs the change
        pass


def _in_other_rules(campaign, rule, key) -> bool:
    from routing.models import RuleDestination
    qs = RuleDestination.objects.filter(rule__campaign=campaign)
    if rule is not None:
        qs = qs.exclude(rule=rule)
    return any(digits_key(d) == key for d in qs.values_list('destination', flat=True))


def _log(user, groups, action, number, fields, changed, total):
    """One line per group in the activity log (and the Telegram alert), in
    place of one per campaign."""
    from accounts.models import ActivityLog
    verb = {ADD: 'added', REMOVE: 'removed', UPDATE: 'changed'}[action]
    shown = {k: str(v) for k, v in fields.items()}
    for g in groups:
        try:
            ActivityLog.objects.create(
                user=user, action=ActivityLog.Action.RECORD_UPDATED,
                metadata={'target_type': 'campaign_group', 'target_id': str(g.id), 'target_name': g.name,
                          'group_action': f'destination {number} {verb} in {changed} of {total} campaign(s)',
                          'changes': {k: {'old': '', 'new': v} for k, v in shown.items()}})
        except Exception:  # noqa: BLE001 - the log line never costs the change
            pass


def set_members(group, campaign_ids):
    """Exactly these campaigns in the group. A campaign already in another
    group is refused, naming it: one group per campaign."""
    org = group.organization
    wanted = list(Campaign.objects.filter(organization=org, id__in=list(campaign_ids or [])).select_related('group'))
    if len(wanted) != len(set(map(str, campaign_ids or []))):
        raise ValueError('Campaign not found')
    taken = [c for c in wanted if c.group_id and c.group_id != group.id]
    if taken:
        raise ValueError('Already in another group: ' + ', '.join(f'{c.name} ({c.group.name})' for c in taken))
    before = sorted(Campaign.objects.filter(group=group).values_list('name', flat=True))
    after = sorted(c.name for c in wanted)
    _via_group([group.name])
    with transaction.atomic():
        for c in Campaign.objects.filter(group=group).exclude(id__in=[c.id for c in wanted]):
            c.group = None
            c.save(update_fields=['group', 'updated_at'])
        for c in wanted:
            if c.group_id != group.id:
                c.group = group
                c.save(update_fields=['group', 'updated_at'])
    if before != after:
        from accounts.models import ActivityLog
        from accounts.current_request import get_current_user
        user = get_current_user()
        if user is not None:
            ActivityLog.objects.create(
                user=user, action=ActivityLog.Action.RECORD_UPDATED,
                metadata={'target_type': 'campaign_group', 'target_id': str(group.id), 'target_name': group.name,
                          'changes': {'campaigns': {'old': ', '.join(before) or 'none',
                                                    'new': ', '.join(after) or 'none'}}})


def format_group(group) -> dict:
    members = list(group.campaigns.order_by('name'))
    return {'id': str(group.id), 'name': group.name, 'description': group.description,
            'campaigns': [{'id': str(c.id), 'name': c.name, 'status': c.status} for c in members],
            'created_at': group.created_at.isoformat(), 'updated_at': group.updated_at.isoformat()}


def _via_group(names):
    """Mark this request's per-campaign activity lines as made through a
    group: the feed keeps them, the Telegram alert sends the one group line."""
    from accounts.current_request import get_current_request
    req = get_current_request()
    if req is not None:
        req._activity_via_group = ', '.join(names)
