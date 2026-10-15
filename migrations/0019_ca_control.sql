-- Pause and resume of the Campaign Agent from the dashboard, with no backend between them. DRAFT: review before running;
-- apply after 0018. Same pattern as the Marketing Agent's pipeline_control: the dashboard writes what it WANTS
-- (desired_state) through one function; the Campaign Agent service polls the row, does it, and writes what IS
-- (actual_state). The dashboard shows both.
--
-- "paused" means: the worker takes no new request. A request already running finishes (the model call cannot be cut
-- cleanly); requests that arrive meanwhile stay "pending" and run after "running" is asked for again. The link to the
-- Marketing Agent keeps serving the live campaign, and the Marketing Agent's own pipeline is a separate switch.

create table if not exists public.ca_control (
    id integer primary key default 1 check (id = 1),
    desired_state text not null default 'running' check (desired_state in ('running', 'paused')),
    requested_by text check (requested_by is null or char_length(requested_by) <= 200),
    requested_at timestamptz,
    actual_state text not null default 'running' check (actual_state in ('running', 'paused')),
    state_since timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
insert into public.ca_control (id) values (1) on conflict (id) do nothing;
alter table public.ca_control enable row level security;

-- The Campaign Agent reads the wish and reports what it did; it never changes the wish.
grant select on public.ca_control to campaign_agent;
grant update (actual_state, state_since, updated_at) on public.ca_control to campaign_agent;
drop policy if exists campaign_agent_all on public.ca_control;
create policy campaign_agent_all on public.ca_control for all to campaign_agent using (true) with check (true);
-- The dashboard reads both, and asks only through the function below (no table write grant).
grant select on public.ca_control to campaign_dashboard;
drop policy if exists campaign_dashboard_read on public.ca_control;
create policy campaign_dashboard_read on public.ca_control for select to campaign_dashboard using (true);

create or replace function public.ca_request_state(p_state text, p_by text)
returns public.ca_control
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    r public.ca_control;
begin
    if p_state not in ('running', 'paused') then
        raise exception 'state must be running or paused, not %', p_state;
    end if;
    update public.ca_control
       set desired_state = p_state, requested_by = left(coalesce(p_by, 'unknown'), 200), requested_at = now(),
           updated_at = now()
     where id = 1
    returning * into r;
    return r;
end $$;

-- Nobody but the dashboard role (and the owner) may ask.
revoke execute on function public.ca_request_state(text, text) from public;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke execute on function public.ca_request_state(text, text) from %I', r);
            execute format('revoke all on public.ca_control from %I', r);
        end if;
    end loop;
end $$;
grant execute on function public.ca_request_state(text, text) to campaign_dashboard;
