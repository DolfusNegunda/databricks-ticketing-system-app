-- ===========================================================================
-- Nexus Support :: demo data
--
-- Idempotent by design: the whole block is a no-op once tickets exist, so the
-- app can call this on every boot without ever duplicating rows.
--
-- Covers 6 tickets across all 4 statuses, all 4 priorities and all 5
-- categories, with at least 2 messages on every ticket.
-- ===========================================================================

DO $seed$
DECLARE
    v_ticket BIGINT;
BEGIN
    IF EXISTS (SELECT 1 FROM __SCHEMA__.tickets) THEN
        RAISE NOTICE 'Seed skipped: __SCHEMA__.tickets already contains rows.';
        RETURN;
    END IF;

    -- ---------------------------------------------------------------- #1 open
    INSERT INTO __SCHEMA__.tickets
        (title, description, status, priority, category, created_by, assigned_to, created_at, updated_at)
    VALUES
        ('Cannot reset password from the login screen',
         'The "Forgot password" link returns a blank page on Safari and Edge. Chrome works. No reset email arrives either way.',
         'open', 'high', 'account',
         'alex.morgan@example.com', NULL,
         now() - interval '3 days', now() - interval '20 hours')
    RETURNING ticket_id INTO v_ticket;

    INSERT INTO __SCHEMA__.ticket_messages (ticket_id, message_text, author, author_role, created_at) VALUES
        (v_ticket, 'I have tried three times today. Clicking "Forgot password" just loads an empty page and I never get the email.',
         'alex.morgan@example.com', 'customer', now() - interval '3 days'),
        (v_ticket, 'Thanks for the detail. Could you confirm which browser version you are on, and whether you see the same thing in a private window?',
         'priya.raman@example.com', 'agent', now() - interval '2 days'),
        (v_ticket, 'Safari 17.4 and Edge 124. Same blank page in private windows on both.',
         'alex.morgan@example.com', 'customer', now() - interval '20 hours');

    -- --------------------------------------------------------- #2 in_progress
    INSERT INTO __SCHEMA__.tickets
        (title, description, status, priority, category, created_by, assigned_to, created_at, updated_at)
    VALUES
        ('Invoice 4821 was charged twice in July',
         'Two identical charges of $349.00 posted four minutes apart against the same subscription.',
         'in_progress', 'urgent', 'billing',
         'dana.klein@example.com', 'samir.osei@example.com',
         now() - interval '5 days', now() - interval '6 hours')
    RETURNING ticket_id INTO v_ticket;

    INSERT INTO __SCHEMA__.ticket_messages (ticket_id, message_text, author, author_role, created_at) VALUES
        (v_ticket, 'Our card statement shows two charges of $349.00 on 12 July, four minutes apart. We only have one subscription.',
         'dana.klein@example.com', 'customer', now() - interval '5 days'),
        (v_ticket, 'Confirmed on our side: the payment webhook was retried after a timeout and created a second capture. Refund raised with finance.',
         'samir.osei@example.com', 'agent', now() - interval '2 days'),
        (v_ticket, 'Refund reference RF-77120 issued. Funds typically settle in 3-5 business days. Keeping this open until you confirm receipt.',
         'samir.osei@example.com', 'agent', now() - interval '6 hours');

    INSERT INTO __SCHEMA__.ticket_status_history (ticket_id, from_status, to_status, changed_by, changed_at) VALUES
        (v_ticket, 'open', 'in_progress', 'samir.osei@example.com', now() - interval '2 days');

    -- --------------------------------------------------------- #3 in_progress
    INSERT INTO __SCHEMA__.tickets
        (title, description, status, priority, category, created_by, assigned_to, created_at, updated_at)
    VALUES
        ('Dashboard exports time out on large date ranges',
         'CSV export of the usage dashboard fails with a 504 whenever the range exceeds roughly 90 days.',
         'in_progress', 'medium', 'technical',
         'lena.fischer@example.com', 'priya.raman@example.com',
         now() - interval '8 days', now() - interval '1 day')
    RETURNING ticket_id INTO v_ticket;

    INSERT INTO __SCHEMA__.ticket_messages (ticket_id, message_text, author, author_role, created_at) VALUES
        (v_ticket, 'Anything under about 90 days exports fine. Beyond that we get a 504 after roughly 30 seconds.',
         'lena.fischer@example.com', 'customer', now() - interval '8 days'),
        (v_ticket, 'Reproduced with a 6-month range. The export runs synchronously today; we are moving it to a background job with an email link.',
         'priya.raman@example.com', 'agent', now() - interval '1 day');

    INSERT INTO __SCHEMA__.ticket_status_history (ticket_id, from_status, to_status, changed_by, changed_at) VALUES
        (v_ticket, 'open', 'in_progress', 'priya.raman@example.com', now() - interval '1 day');

    -- ---------------------------------------------------------------- #4 open
    INSERT INTO __SCHEMA__.tickets
        (title, description, status, priority, category, created_by, assigned_to, created_at, updated_at)
    VALUES
        ('Add dark mode to the mobile app',
         'Request from several field technicians who work night shifts and find the current white background hard on the eyes.',
         'open', 'low', 'feature_request',
         'tomas.reyes@example.com', NULL,
         now() - interval '12 days', now() - interval '9 days')
    RETURNING ticket_id INTO v_ticket;

    INSERT INTO __SCHEMA__.ticket_messages (ticket_id, message_text, author, author_role, created_at) VALUES
        (v_ticket, 'Six of our night-shift technicians have asked for a dark theme in the mobile app. The web console already has one.',
         'tomas.reyes@example.com', 'customer', now() - interval '12 days'),
        (v_ticket, 'Logged as a feature request and linked to the design backlog. No committed date yet, but the demand is noted.',
         'priya.raman@example.com', 'agent', now() - interval '9 days');

    -- ------------------------------------------------------------ #5 resolved
    INSERT INTO __SCHEMA__.tickets
        (title, description, status, priority, category, created_by, assigned_to, created_at, updated_at, resolved_at)
    VALUES
        ('SSO login loop after Okta migration',
         'Users bounce between the app and the identity provider without ever landing on the dashboard.',
         'resolved', 'high', 'technical',
         'nadia.haddad@example.com', 'samir.osei@example.com',
         now() - interval '20 days', now() - interval '16 days', now() - interval '16 days')
    RETURNING ticket_id INTO v_ticket;

    INSERT INTO __SCHEMA__.ticket_messages (ticket_id, message_text, author, author_role, created_at) VALUES
        (v_ticket, 'Since we moved to the new Okta tenant, signing in redirects in a loop. Roughly 40 users affected.',
         'nadia.haddad@example.com', 'customer', now() - interval '20 days'),
        (v_ticket, 'The ACS URL on the new tenant still pointed at the old callback path. Updated it and cleared the session cache.',
         'samir.osei@example.com', 'agent', now() - interval '17 days'),
        (v_ticket, 'Confirmed working for all affected users this morning. Thanks for the quick turnaround.',
         'nadia.haddad@example.com', 'customer', now() - interval '16 days');

    INSERT INTO __SCHEMA__.ticket_status_history (ticket_id, from_status, to_status, changed_by, changed_at) VALUES
        (v_ticket, 'open',        'in_progress', 'samir.osei@example.com',   now() - interval '19 days'),
        (v_ticket, 'in_progress', 'resolved',    'samir.osei@example.com',   now() - interval '16 days');

    -- -------------------------------------------------------------- #6 closed
    INSERT INTO __SCHEMA__.tickets
        (title, description, status, priority, category, created_by, assigned_to, created_at, updated_at, resolved_at)
    VALUES
        ('Typo on the billing FAQ page',
         'The FAQ says "montly" instead of "monthly" in the second paragraph.',
         'closed', 'low', 'general',
         'lena.fischer@example.com', 'priya.raman@example.com',
         now() - interval '30 days', now() - interval '28 days', now() - interval '28 days')
    RETURNING ticket_id INTO v_ticket;

    INSERT INTO __SCHEMA__.ticket_messages (ticket_id, message_text, author, author_role, created_at) VALUES
        (v_ticket, 'Small one: the billing FAQ has "montly" in the second paragraph.',
         'lena.fischer@example.com', 'customer', now() - interval '30 days'),
        (v_ticket, 'Fixed and published. Closing this out -- thanks for flagging it.',
         'priya.raman@example.com', 'agent', now() - interval '28 days');

    INSERT INTO __SCHEMA__.ticket_status_history (ticket_id, from_status, to_status, changed_by, changed_at) VALUES
        (v_ticket, 'open',     'resolved', 'priya.raman@example.com', now() - interval '28 days'),
        (v_ticket, 'resolved', 'closed',   'priya.raman@example.com', now() - interval '28 days');

    RAISE NOTICE 'Seeded 6 demo tickets into __SCHEMA__.';
END;
$seed$;
