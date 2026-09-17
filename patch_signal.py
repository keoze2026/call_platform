path = 'routing/signals.py'
with open(path, 'r') as f:
    content = f.read()

# Replace the revenue, payout, and profit dictionary mapping block completely
old_target_snippet = """    'revenue': call.revenue or 0,
    'payout': call.buyer_payout or 0,
    'profit': (call.revenue or 0) - (call.buyer_payout or 0),"""

new_replacement_snippet = """    'revenue': (call.campaign.revenue_amount if call.campaign_id and call.campaign else 0) or call.revenue or 0,
    'payout': (call.campaign.payout_amount if call.campaign_id and call.campaign else 0) or call.buyer_payout or 0,
    'profit': (
        ((call.campaign.revenue_amount if call.campaign_id and call.campaign else 0) or call.revenue or 0) -
        ((call.campaign.payout_amount if call.campaign_id and call.campaign else 0) or call.buyer_payout or 0)
    ),"""

if old_target_snippet in content:
    content = content.replace(old_target_snippet, new_replacement_snippet)
    with open(path, 'w') as f:
        f.write(content)
    print("SUCCESS: routing/signals.py patched permanently!")
else:
    print("WARNING: Exact snippet not found, using regex fallback...")
    import re
    content, count = re.subn(
        r"['\"]revenue['\"]\s*:\s*call\.revenue\s+or\s+0\s*,\s*['\"]payout['\"]\s*:\s*call\.buyer_payout\s+or\s+0\s*,\s*['\"]profit['\"]\s*:\s*\(call\.revenue\s+or\s+0\)\s*-\s*\(call\.buyer_payout\s+or\s+0\)\s*,",
        new_replacement_snippet,
        content,
        flags=re.DOTALL
    )
    if count > 0:
        with open(path, 'w') as f:
            f.write(content)
        print("SUCCESS: Patched via regex fallback!")
    else:
        print("ERROR: Could not match blocks automatically.")
