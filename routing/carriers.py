"""Normalise raw carrier strings from the Telnyx lookup into carrier families.

Telnyx returns the operating entity, not a brand: the same network arrives as
'Verizon Wireless:6006 - SVR/2', 'CELLCO PARTNERSHIP DBA VERIZON WIRELESS - OH'
and a dozen other spellings. Grouping a report by the raw value produces 30-odd
rows for what are really five or six carriers.

Matching is on substrings of the upper-cased string, most specific first — MVNOs
before their host network, since 'METRO PCS' would otherwise be swallowed by the
T-Mobile rule and 'CRICKET' by AT&T.
"""

# (family, [needles]) — order matters, first match wins
CARRIER_PATTERNS = [
    # MVNOs and sub-brands first, or their parent network absorbs them
    ('Metro by T-Mobile', ['METROPCS', 'METRO PCS', 'METRO BY T-MOBILE']),
    ('Cricket', ['CRICKET', 'ELISKA WIRELESS', 'LEAP WIRELESS']),
    ('Boost', ['BOOST']),
    ('Google Voice', ['GOOGLE']),
    ('TracFone', ['TRACFONE', 'STRAIGHT TALK', 'TOTAL WIRELESS']),

    ('Verizon', ['VERIZON', 'CELLCO PARTNERSHIP', 'ALLTEL']),

    # AT&T absorbed Cingular and the Bell operating companies
    ('AT&T', [
        'AT&T', 'ATT ', 'CINGULAR', 'NEW CINGULAR',
        'BELL TEL', 'BELLSOUTH', 'SOUTHWESTERN BELL', 'PACIFIC BELL',
        'AMERITECH', 'WISCONSIN BELL', 'MICHIGAN BELL', 'INDIANA BELL',
        'OHIO BELL', 'ILLINOIS BELL', 'NEVADA BELL',
    ]),

    # T-Mobile absorbed VoiceStream, Omnipoint, Powertel, Aerial, SunCom, Sprint
    ('T-Mobile', [
        'T-MOBILE', 'TMOBILE', 'VOICESTREAM', 'OMNIPOINT',
        'POWERTEL', 'AERIAL COMMUNICATIONS', 'SUNCOM',
    ]),
    ('Sprint', ['SPRINT', 'NEXTEL', 'VIRGIN MOBILE']),

    ('US Cellular', ['US CELLULAR', 'USCC', 'UNITED STATES CELLULAR']),
    ('C Spire', ['C SPIRE', 'CELLULAR SOUTH']),

    # VoIP and wholesale carriers, worth separating from real mobile networks
    ('Bandwidth', ['BANDWIDTH']),
    ('Twilio', ['TWILIO']),
    ('Onvoy', ['ONVOY', 'SINCH']),
    ('Level 3', ['LEVEL 3', 'LUMEN', 'CENTURYLINK']),
    ('Peerless', ['PEERLESS']),
    ('Inteliquent', ['INTELIQUENT', 'NEUTRAL TANDEM']),
    ('Comcast', ['COMCAST']),
    ('Charter', ['CHARTER', 'SPECTRUM']),
    ('Cox', ['COX ']),
    ('Frontier', ['FRONTIER']),
    ('Windstream', ['WINDSTREAM']),
]

UNKNOWN = 'Unknown'


def normalise_carrier(raw: str) -> str:
    """Map a raw carrier string to a family name.

    Returns 'Unknown' for an empty value or one that matches nothing, so a
    breakdown always has somewhere to put every call rather than dropping rows.
    """
    if not raw:
        return UNKNOWN

    text = raw.upper()
    for family, needles in CARRIER_PATTERNS:
        for needle in needles:
            if needle in text:
                return family

    # Nothing matched. Keep the raw value, trimmed of the routing suffixes
    # Telnyx appends ('-SVR/2', ':6006 - SVR/2'), so an unrecognised carrier is
    # still readable in a report instead of collapsing into one Unknown bucket.
    cleaned = raw.split(':')[0].split('-SVR')[0].strip().strip(',').strip()
    return cleaned[:60] or UNKNOWN
