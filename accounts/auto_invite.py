"""Invite a partner the moment their record is created.

An invitation should not be a second thing somebody remembers to do. You add a
publisher, you type their email address into the form, and they get their login
- because that is what typing an email address into the form means.

It worked the other way round until now: creating the record did nothing, and an
Invite button on the settings page saved to the browser and called nothing, so
no invitation was ever requested from the server at all. Every partner on this
platform is still waiting for one.

Shared by buyers and publishers so the two cannot drift, and so the next partner
type gets it without anybody noticing it is missing.

Three rules:

  no email, no invite        the address is the decision. A record created
                             without one is a record somebody is still filling
                             in, and inviting them would be guessing.

  never fails the create     the record is what was asked for. If the mail
                             server is down, the account and link still exist
                             and the response says so, rather than losing a
                             publisher to an SMTP timeout.

  the link comes back either way   so an invitation is never lost to a mail
                             problem - it can be sent by hand from the response.
"""
import logging

logger = logging.getLogger(__name__)


def invite_on_create(partner, kind: str, invited_by=None) -> dict:
    """Send the welcome invitation for a newly created buyer or publisher.

    Returns what happened, for the create endpoint to include in its response.
    Never raises.
    """
    email = (getattr(partner, 'email', '') or '').strip()
    if not email:
        return {
            'invited': False,
            'reason': 'no email address was given, so nobody was invited',
        }

    try:
        from accounts.partner_invites import InviteError, invite_partner

        result = invite_partner(
            organization=partner.organization,
            partner=partner,
            kind=kind,
            email=email,
            contact_name=getattr(partner, 'name', ''),
            invited_by=invited_by,
        )
    except InviteError as e:
        # Something the person can fix - a malformed address, an account that
        # belongs to another workspace. Reported, not raised: they asked for a
        # publisher and they have one.
        logger.warning('auto-invite refused for %s %s: %s', kind, partner.id, e)
        return {'invited': False, 'reason': str(e)}
    except Exception:
        logger.exception('auto-invite failed for %s %s', kind, partner.id)
        return {
            'invited': False,
            'reason': 'the invitation could not be created; the record was saved',
        }

    logger.info(
        '%s %s created and invited: %s (email_sent=%s)',
        kind, partner.id, email, result.get('email_sent'),
    )
    return {
        'invited': True,
        'email': email,
        'email_sent': result.get('email_sent'),
        'setup_link': result.get('setup_link'),
        'expires_in_hours': result.get('expires_in_hours'),
        'reason': (
            f'invitation emailed to {email}'
            if result.get('email_sent')
            else f'account ready for {email}, but the email did not send - '
                 f'send them the link'
        ),
    }
