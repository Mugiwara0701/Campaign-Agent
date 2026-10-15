-- The dashboard gateway (docs/dashboard-api.md) needs one more column to be readable by the `campaign_dashboard` role.
-- DRAFT: review before running; apply after 0021.
--
-- A repeated request (same campaign, same idempotency_key: a retry, a double click) must return the FIRST request instead of
-- failing or making a second one. To find it the gateway looks the key up, so the role must be allowed to read that column.
-- It is only the caller's own key; the payload, the lease and the worker's bookkeeping stay unreadable.

grant select (idempotency_key) on public.campaign_request to campaign_dashboard;
