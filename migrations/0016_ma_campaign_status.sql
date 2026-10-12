-- What the Marketing Agent (MA) reports about campaigns. DRAFT: review before running; apply after 0015.
-- One row, written by the MA with its heartbeat; the Campaign Agent and the dashboard only read it.
--   supported_contract_versions  the config_version values the MA's loader accepts, e.g. {1.0}
--   current_campaign             {id, version, sha256, applied_at, error?}: what the MA runs now, or why it refused
-- The MA's database role needs insert and update on this table: grant it in the MA's own migration
-- (see docs/marketing-agent-m8-work.md), because that role is owned by the MA repository.

create table if not exists public.ma_campaign_status (
    id boolean primary key default true check (id),
    supported_contract_versions text[] not null default '{}',
    current_campaign jsonb,
    reported_at timestamptz not null default now()
);

alter table public.ma_campaign_status enable row level security;

grant select on public.ma_campaign_status to campaign_agent;
grant select on public.ma_campaign_status to campaign_dashboard;

drop policy if exists campaign_agent_read on public.ma_campaign_status;
create policy campaign_agent_read on public.ma_campaign_status
for select to campaign_agent using (true);
drop policy if exists campaign_dashboard_read on public.ma_campaign_status;
create policy campaign_dashboard_read on public.ma_campaign_status
for select to campaign_dashboard using (true);
