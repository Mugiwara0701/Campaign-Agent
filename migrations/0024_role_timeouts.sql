-- A dead connection must not keep a lock for minutes. DRAFT: review before running; apply after 0023.
--
-- What happened: the Campaign Agent's connection to the database dropped (the office network, or the pooler) while it was
-- writing its heartbeat. The client noticed and reconnected, but the database kept the old session "idle in transaction" for
-- over three minutes and with it a lock on the one heartbeat row. Every later heartbeat write then waited for that lock until
-- the 2-minute statement timeout cancelled it, so the dashboard saw the Campaign Agent as stuck although it was running.
--
-- Two settings, on the two limited roles only (they apply to each new session of the role, also behind the pooler):
--   * idle_in_transaction_session_timeout: the database ends a session that sits inside an open transaction for a minute.
--     Nothing here keeps a transaction open that long (a model call never runs inside one).
--   * lock_timeout: a statement that waits for a lock gives up after 30 seconds instead of two minutes.

alter role campaign_agent set idle_in_transaction_session_timeout = '60s';
alter role campaign_agent set lock_timeout = '30s';
alter role campaign_dashboard set idle_in_transaction_session_timeout = '60s';
alter role campaign_dashboard set lock_timeout = '30s';
