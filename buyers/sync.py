"""Keep a buyer's phone number in step with the destination it routes to.

All 42 buyers had `phone_number=''`. That field is not decoration:
`routing/engine.py` returns `auction.winner.phone_number` as the destination for
an RTB call, so an empty one means an RTB call routes to nothing. Every campaign
is on `priority` today, so it has never fired - which is exactly why it would
have gone unnoticed until the first time somebody switched a campaign to RTB and
the calls quietly went nowhere.

The migration filled in what was already there. This is what stops it drifting
again: add a destination, change its number, and the buyer's number follows
without anyone remembering to.

Three rules, and all three matter:

  only when empty or stale   a number somebody typed in deliberately is never
                             overwritten. It is only replaced when it still
                             holds the destination's previous number, which
                             means this wrote it in the first place.

  only when unambiguous      a buyer with two enabled destinations has no single
                             "their" number. Guessing sends calls to the wrong
                             company, so nothing is written at all.

  never from the call path   this runs when a destination is saved - someone
                             editing a record - never while a call is routing.
"""
import logging

from django.db.models.signals import post_delete, post_save

logger = logging.getLogger(__name__)


def _resolve(buyer) -> str:
    """The one number that is unambiguously this buyer's, or ''."""
    from buyers.destination import Destination

    dests = list(Destination.objects.filter(buyer_id=buyer.id).exclude(tfn=''))
    enabled = [d for d in dests if d.enabled]

    if len(enabled) == 1:
        return enabled[0].tfn
    if not enabled and len(dests) == 1:
        # Still their number, just switched off at the moment.
        return dests[0].tfn
    return ''


def sync_buyer_phone_number(buyer, previous_tfn: str = '') -> None:
    from buyers.models import Buyer

    if buyer is None:
        return

    resolved = _resolve(buyer)
    if not resolved:
        return

    current = (buyer.phone_number or '').strip()
    if current and current != previous_tfn and current != resolved:
        # Somebody set this by hand. Leave it, and say so once, because a buyer
        # whose number disagrees with its destination is worth a person looking.
        logger.info(
            'buyer %s has phone_number %r set by hand; destination says %r, leaving it alone',
            buyer.id, current, resolved,
        )
        return

    if current == resolved:
        return

    Buyer.objects.filter(pk=buyer.pk).update(phone_number=resolved)
    logger.info('buyer %s phone_number set to %s from its destination', buyer.id, resolved)


def on_destination_saved(sender, instance, **kwargs):
    try:
        if instance.buyer_id:
            sync_buyer_phone_number(instance.buyer, previous_tfn=getattr(instance, '_previous_tfn', ''))
    except Exception:
        # A destination save is the thing being asked for; keeping a mirrored
        # field in step is not worth failing it.
        logger.exception('could not sync buyer phone number from destination %s', instance.pk)


def on_destination_deleted(sender, instance, **kwargs):
    try:
        if instance.buyer_id:
            from buyers.models import Buyer
            buyer = Buyer.objects.filter(pk=instance.buyer_id).first()
            if buyer and (buyer.phone_number or '') == instance.tfn:
                # The number it pointed at is gone. Fall back to whatever other
                # destination the buyer has, or clear it rather than leave a
                # number that no longer routes anywhere.
                resolved = _resolve(buyer)
                Buyer.objects.filter(pk=buyer.pk).update(phone_number=resolved)
                logger.info(
                    'buyer %s phone_number %s after its destination was deleted',
                    buyer.id, f'set to {resolved}' if resolved else 'cleared',
                )
    except Exception:
        logger.exception('could not sync buyer phone number after deleting destination %s', instance.pk)


def connect():
    """Bound to Destination alone, so nothing else enters this code."""
    from buyers.destination import Destination

    post_save.connect(
        on_destination_saved, sender=Destination,
        dispatch_uid='sync_buyer_phone_number_on_save',
    )
    post_delete.connect(
        on_destination_deleted, sender=Destination,
        dispatch_uid='sync_buyer_phone_number_on_delete',
    )
