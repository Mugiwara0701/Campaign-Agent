-- Campaign Agent tables. DRAFT: review with the dashboard developer before running.
-- Run by hand in the Supabase SQL editor, like dashboardbackend/migrations/003_campaign.sql.
-- Parent table: public.campaign (owned by the dashboard, not modified here).

-- 1. Inbox: one row per dashboard action. The dashboard server inserts, the CA fills in the result.
create table if not exists public.campaign_request (
    id uuid primary key default gen_random_uuid(),
    campaign_id uuid not null references public.campaign (id) on delete cascade,
    request_type text not null check (
        request_type in ('interpret', 'answer', 'edit', 'suggestion', 'approve', 'discard', 'rollback')
    ),
    payload jsonb not null default '{}'::jsonb,
    base_revision integer,
    requested_by text not null check (char_length(requested_by) between 1 and 100),
    idempotency_key text not null,
    status text not null default 'pending' check (
        status in ('pending', 'processing', 'done', 'failed')
    ),
    error_code text,
    error_detail text,
    result jsonb,
    created_at timestamptz not null default now(),
    claimed_at timestamptz,
    finished_at timestamptz,
    unique (campaign_id, idempotency_key)
);

create index if not exists campaign_request_pending_idx
on public.campaign_request (created_at) where status = 'pending';

-- 2. Frozen versions. Content never changes after insert; only the approval fields are set, once.
create table if not exists public.campaign_version (
    id uuid primary key default gen_random_uuid(),
    campaign_id uuid not null references public.campaign (id) on delete cascade,
    version_no integer not null check (version_no > 0),
    overlay_yaml text not null,
    sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
    ir_hash text not null,
    contract_version text not null,
    validation_report jsonb not null default '{}'::jsonb,
    not_applied jsonb not null default '[]'::jsonb,
    approved_by text,
    approved_at timestamptz,
    created_at timestamptz not null default now(),
    unique (campaign_id, version_no)
);

create or replace function public.campaign_version_guard() returns trigger
language plpgsql as $$
begin
    if new.overlay_yaml is distinct from old.overlay_yaml
        or new.sha256 is distinct from old.sha256
        or new.ir_hash is distinct from old.ir_hash
        or new.contract_version is distinct from old.contract_version
        or new.version_no is distinct from old.version_no
        or new.campaign_id is distinct from old.campaign_id then
        raise exception 'campaign_version content is immutable';
    end if;
    if old.approved_at is not null then
        raise exception 'campaign_version is already approved';
    end if;
    return new;
end;
$$;

drop trigger if exists campaign_version_guard on public.campaign_version;
create trigger campaign_version_guard
before update on public.campaign_version
for each row execute function public.campaign_version_guard();

-- 3. Working state, one row per campaign. active_version_id is the pointer the Marketing Agent reads.
create table if not exists public.campaign_state (
    campaign_id uuid primary key references public.campaign (id) on delete cascade,
    current_state text not null default 'DRAFT' check (
        current_state in (
            'DRAFT', 'INTERPRETING', 'RESEARCHING', 'CLARIFICATION_REQUIRED',
            'READY_FOR_VALIDATION', 'VALIDATING', 'VALIDATED', 'VALIDATION_FAILED',
            'AWAITING_REVIEW', 'APPROVED', 'PUBLISHED', 'PUBLISH_FAILED',
            'MODEL_UNAVAILABLE', 'DISCARDED'
        )
    ),
    revision integer not null default 0,
    ir jsonb not null default '{}'::jsonb,
    review_view jsonb not null default '{}'::jsonb,
    candidate_version_id uuid references public.campaign_version (id),
    active_version_id uuid references public.campaign_version (id),
    updated_at timestamptz not null default now()
);

-- 4. One row: lets the dashboard show that the CA machine is alive.
create table if not exists public.ca_heartbeat (
    id integer primary key default 1 check (id = 1),
    last_seen timestamptz not null default now(),
    agent_status text not null default 'idle',
    agent_version text,
    model_name text,
    current_request_id uuid
);

-- Same convention as public.campaign: RLS on. The dashboard server uses the service key.
alter table public.campaign_request enable row level security;
alter table public.campaign_version enable row level security;
alter table public.campaign_state enable row level security;
alter table public.ca_heartbeat enable row level security;

-- Limited role for the Campaign Agent. Set a password and LOGIN by hand; never use the service key.
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'campaign_agent') then
        create role campaign_agent nologin;
    end if;
end
$$;

grant usage on schema public to campaign_agent;
grant select on public.campaign to campaign_agent;
grant select, update (status, error_code, error_detail, result, claimed_at, finished_at)
on public.campaign_request to campaign_agent;
grant select, insert, update on public.campaign_state to campaign_agent;
grant select, insert, update (approved_by, approved_at) on public.campaign_version to campaign_agent;
grant select, insert, update on public.ca_heartbeat to campaign_agent;

-- RLS is on, so the role needs explicit policies to see rows.
drop policy if exists campaign_agent_all on public.campaign_request;
create policy campaign_agent_all on public.campaign_request
for all to campaign_agent using (true) with check (true);
drop policy if exists campaign_agent_all on public.campaign_version;
create policy campaign_agent_all on public.campaign_version
for all to campaign_agent using (true) with check (true);
drop policy if exists campaign_agent_all on public.campaign_state;
create policy campaign_agent_all on public.campaign_state
for all to campaign_agent using (true) with check (true);
drop policy if exists campaign_agent_all on public.ca_heartbeat;
create policy campaign_agent_all on public.ca_heartbeat
for all to campaign_agent using (true) with check (true);
drop policy if exists campaign_agent_read on public.campaign;
create policy campaign_agent_read on public.campaign
for select to campaign_agent using (true);

-- TODO (open): read-only grants and policies for the Marketing Agent's database role on
-- campaign, campaign_state (active_version_id) and campaign_version. Role name not yet known.
