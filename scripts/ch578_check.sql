-- CH-578: rows per table, rows without an account, accounts seen.
-- Read-only.
select 'access_requests' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from access_requests
union all select 'activity_logs' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from activity_logs
union all select 'buyer_campaigns' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from buyer_campaigns
union all select 'buyer_caps' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from buyer_caps
union all select 'buyer_schedules' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from buyer_schedules
union all select 'buyer_telegram_reports' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from buyer_telegram_reports
union all select 'campaign_caps' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from campaign_caps
union all select 'campaign_schedules' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from campaign_schedules
union all select 'contact_messages' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from contact_messages
union all select 'conversion_events' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from conversion_events
union all select 'dni_calls' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from dni_calls
union all select 'dni_landers' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from dni_landers
union all select 'dni_numbers' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from dni_numbers
union all select 'dni_sessions' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from dni_sessions
union all select 'guest_visits' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from guest_visits
union all select 'integration_activity' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from integration_activity
union all select 'ivr_node_transitions' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from ivr_node_transitions
union all select 'ivr_nodes' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from ivr_nodes
union all select 'marketplace_favorites' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from marketplace_favorites
union all select 'notification_preferences' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from notification_preferences
union all select 'password_reset_tokens' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from password_reset_tokens
union all select 'publisher_campaigns' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from publisher_campaigns
union all select 'publisher_caps' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from publisher_caps
union all select 'referral_earnings' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from referral_earnings
union all select 'referrals' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from referrals
union all select 'reports_pin_attempts' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from reports_pin_attempts
union all select 'reports_pin_unlocks' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from reports_pin_unlocks
union all select 'rtb_rtbbid' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from rtb_rtbbid
union all select 'rule_conditions' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from rule_conditions
union all select 'rule_destinations' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from rule_destinations
union all select 'setup_tokens' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from setup_tokens
union all select 'support_messages' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from support_messages
union all select 'support_sessions' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from support_sessions
union all select 'telegram_link_codes' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from telegram_link_codes
union all select 'webhook_deliveries' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from webhook_deliveries
union all select 'white_label_domains' t, count(*) n, count(*) filter (where organization_id is null) no_account, count(distinct organization_id) accounts from white_label_domains
order by 1;
