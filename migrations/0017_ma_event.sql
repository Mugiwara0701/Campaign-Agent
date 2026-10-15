-- Events the Marketing Agent (MA) sends over the Tailscale link (docs/ca-ma-link.md). DRAFT: review before running; apply after 0016.
-- The MA never touches this table: it reaches it only through the Campaign Agent's link listener, which validates every event.
-- Events are shown, never acted on. A "blocker" stays open until a "resolved" event with the same dedupe_key arrives.

create table if not exists public.ma_event (
    event_id uuid primary key,
    received_at timestamptz not null default now(),
    occurred_at timestamptz not null,
    kind text not null check (kind in ('applied', 'rejected', 'blocker', 'resolved', 'info')),
    severity text not null check (severity in ('info', 'warning', 'error', 'critical')),
    code text not null check (code ~ '^MA_[A-Z0-9_]{1,60}$'),
    message text not null check (char_length(message) between 1 and 300),
    campaign_id uuid,
    version_no integer check (version_no is null or version_no >= 1),
    pass_id text check (pass_id is null or char_length(pass_id) <= 100),
    dedupe_key text not null check (char_length(dedupe_key) between 1 and 100),
    details jsonb not null default '{}'::jsonb check (octet_length(details::text) <= 2048),
    repeat_count integer not null default 1 check (repeat_count >= 1),
    resolved_at timestamptz
);

create index if not exists ma_event_open_blockers on public.ma_event (dedupe_key)
where kind = 'blocker' and resolved_at is null;
create index if not exists ma_event_received on public.ma_event (received_at desc);

alter table public.ma_event enable row level security;

-- The Campaign Agent stores events, closes blockers, updates a repeated blocker and prunes old rows (docs/retention.md).
grant select, insert, delete on public.ma_event to campaign_agent;
grant update (
    occurred_at, severity, message, details, repeat_count, resolved_at
) on public.ma_event to campaign_agent;
-- The dashboard only reads.
grant select on public.ma_event to campaign_dashboard;

drop policy if exists campaign_agent_all on public.ma_event;
create policy campaign_agent_all on public.ma_event
for all to campaign_agent using (true) with check (true);
drop policy if exists campaign_dashboard_read on public.ma_event;
create policy campaign_dashboard_read on public.ma_event
for select to campaign_dashboard using (true);
