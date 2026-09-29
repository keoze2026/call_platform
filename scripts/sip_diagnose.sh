#!/usr/bin/env bash
# Why is SIP not arriving? Checks every layer between the carrier and Asterisk.
#
# Written after calls stopped with no obvious cause. The firewall was restricted
# to two carrier addresses on 25 September, so the first question is whether we
# are dropping traffic that used to get through.
echo "════════════════════════════════════════════════════════════"
echo " SIP DIAGNOSTIC  $(date)"
echo "════════════════════════════════════════════════════════════"

echo
echo "── 1. SERVER IDENTITY ──────────────────────────────────────"
echo "public IP : $(curl -s --max-time 5 ifconfig.me)"
echo "hostname  : $(hostname)"

echo
echo "── 2. IS ASTERISK LISTENING ON 5060 ────────────────────────"
ss -ulnp 2>/dev/null | grep -E ':5060|asterisk' || echo "  NOTHING LISTENING ON UDP 5060  <<< that alone would stop every call"
systemctl is-active asterisk 2>/dev/null | sed 's/^/asterisk service: /'
asterisk -rx "core show channels count" 2>/dev/null | sed 's/^/  /'

echo
echo "── 3. FIREWALL RULES FOR 5060, IN KERNEL ORDER ─────────────"
echo "   (an ACCEPT must appear ABOVE the DROP to take effect)"
iptables -S ufw-user-input 2>/dev/null | grep -n 5060 || echo "  no 5060 rules found"

echo
echo "── 4. HOW MUCH HAS BEEN ACCEPTED vs DROPPED ────────────────"
iptables -L ufw-user-input -n -v 2>/dev/null | grep -E '5060' | \
    awk '{printf "  %-10s pkts=%-10s bytes=%-12s src=%s\n", $3, $1, $2, $8}'

echo
echo "── 5. WHO IS ACTUALLY SENDING US SIP (60s, unfiltered) ─────"
echo "   tcpdump sees packets BEFORE the firewall drops them, so"
echo "   anything we are refusing shows up here."
timeout 60 tcpdump -nn -i any 'udp port 5060' 2>/dev/null \
  | awk '{print $3}' | cut -d. -f1-4 | sort | uniq -c | sort -rn | head -15
echo "   (empty above = nothing reached the network card at all)"

echo
echo "── 6. WHAT ASTERISK WILL ACCEPT A CALL FROM ────────────────"
asterisk -rx "pjsip show endpoint carrier" 2>/dev/null | grep -iE "Match|Identify|context" | sed 's/^/  /'
grep -hiE "^match|^contact|^aors" /etc/asterisk/pjsip.conf 2>/dev/null | sed 's/^/  /'

echo
echo "── 7. IS THE DIALPLAN LOADED ───────────────────────────────"
asterisk -rx "dialplan show from-carrier" 2>/dev/null | head -8 | sed 's/^/  /'

echo
echo "── 8. CAN ASTERISK REACH THE PLATFORM API ──────────────────"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 -X POST http://127.0.0.1:8000/api/twilio/asterisk/route/)
echo "  POST /api/twilio/asterisk/route/ -> $code   (403 is correct: it wants the shared secret)"

echo
echo "── 9. LAST CALL RECORDED ───────────────────────────────────"
cd /opt/call_platform 2>/dev/null && docker compose exec -T web python manage.py shell -c "
from routing.models import CallLog
from django.utils import timezone
print('  today      :', CallLog.objects.filter(created_at__date=timezone.now().date()).count())
c = CallLog.objects.order_by('-created_at').first()
print('  last call  :', c.created_at if c else 'none')
" 2>/dev/null | grep -v "objects imported"

echo
echo "════════════════════════════════════════════════════════════"
echo " READING IT"
echo "════════════════════════════════════════════════════════════"
echo " Section 5 empty              -> nothing is arriving. Upstream."
echo " Section 5 shows an IP that"
echo "   is not in section 3        -> WE are dropping their calls."
echo " Section 2 not listening      -> Asterisk is the problem."
echo " Section 8 not 403            -> the API is not answering Asterisk."
