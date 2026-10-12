-- Campaign Agent worker support. DRAFT: review before running; apply after 0013.
-- Additive only, so it is safe whether or not 0013 has been applied already.

-- Request bookkeeping for the inbox worker: a lease lets a crashed worker's request be retried.
alter table public.campaign_request add column if not exists attempts integer not null default 0;
alter table public.campaign_request add column if not exists claimed_by text;
alter table public.campaign_request add column if not exists lease_until timestamptz;

-- What the conversation remembers between turns (questions asked and open, review state flags).
alter table public.campaign_state add column if not exists conversation jsonb not null default '{}'::jsonb;

grant update (attempts, claimed_by, lease_until) on public.campaign_request to campaign_agent;

-- Limited role for the dashboard. The dashboard server may use it instead of the service key for the
-- campaign agent tables: it can ask (insert requests) and read what the agent published, nothing else.
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'campaign_dashboard') then
        create role campaign_dashboard nologin;
    end if;
end
$$;

grant usage on schema public to campaign_dashboard;
grant select on public.campaign to campaign_dashboard;
grant insert (campaign_id, request_type, payload, base_revision, requested_by, idempotency_key)
on public.campaign_request to campaign_dashboard;
grant select (
    id, campaign_id, request_type, base_revision, requested_by, status, error_code, error_detail,
    result, created_at, finished_at
) on public.campaign_request to campaign_dashboard;
-- Not the working IR or the conversation internals: only what is meant for display.
grant select (campaign_id, current_state, revision, review_view, updated_at)
on public.campaign_state to campaign_dashboard;
grant select on public.campaign_version to campaign_dashboard;
grant select on public.ca_heartbeat to campaign_dashboard;

drop policy if exists campaign_dashboard_read on public.campaign;
create policy campaign_dashboard_read on public.campaign
for select to campaign_dashboard using (true);
drop policy if exists campaign_dashboard_insert on public.campaign_request;
create policy campaign_dashboard_insert on public.campaign_request
for insert to campaign_dashboard with check (true);
drop policy if exists campaign_dashboard_read on public.campaign_request;
create policy campaign_dashboard_read on public.campaign_request
for select to campaign_dashboard using (true);
drop policy if exists campaign_dashboard_read on public.campaign_state;
create policy campaign_dashboard_read on public.campaign_state
for select to campaign_dashboard using (true);
drop policy if exists campaign_dashboard_read on public.campaign_version;
create policy campaign_dashboard_read on public.campaign_version
for select to campaign_dashboard using (true);
drop policy if exists campaign_dashboard_read on public.ca_heartbeat;
create policy campaign_dashboard_read on public.ca_heartbeat
for select to campaign_dashboard using (true);
