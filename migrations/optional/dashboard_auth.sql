-- OPTIONAL. DRAFT: review before running; apply by hand after 0019 (the migration runner does not read this folder), and only if the dashboard's web page talks to this database
-- DIRECTLY, signed in with Supabase Auth (no server of its own in between). A dashboard whose server writes the requests
-- with the service key, or with the `campaign_dashboard` role, does not need this: that server puts the signed-in
-- person's name in `requested_by` itself.
--
-- The problem it solves: `requested_by` is the approval record ("who approved this leadgen file"). From a browser, the
-- name would be whatever the page sends, so anyone with the page's key could approve as someone else. Here the database
-- itself fills the truth in: a request is only accepted when `requested_by` equals the email of the person the Supabase
-- login says is signed in, and only people on an allow-list may send any request at all.
--
-- Before using it: switch OFF public sign-ups in Supabase Auth (anyone who can sign up would otherwise be "signed in"),
-- and put the team's emails into `ca_dashboard_user`. The role used is `authenticated`, which Supabase gives to a
-- signed-in browser.

create table if not exists public.ca_dashboard_user (
    email text primary key check (email = lower(email) and char_length(email) between 3 and 200),
    added_at timestamptz not null default now()
);
alter table public.ca_dashboard_user enable row level security;  -- no policy: nobody reads it except through the functions below

-- The signed-in person's email, as Supabase Auth says (never what the page sends). Null when not signed in.
create or replace function public.ca_jwt_email()
returns text
language plpgsql
stable
set search_path = public, pg_temp
as $$
begin
    return lower(auth.jwt() ->> 'email');
exception when undefined_function or invalid_schema_name then
    return null;  -- not a Supabase project: nobody counts as signed in
end $$;

create or replace function public.ca_is_dashboard_user()
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select exists (select 1 from public.ca_dashboard_user u where u.email = public.ca_jwt_email());
$$;

grant usage on schema public to authenticated;
grant execute on function public.ca_jwt_email() to authenticated;
grant execute on function public.ca_is_dashboard_user() to authenticated;

-- Send requests: only an allow-listed person, and only under their own email.
grant insert (campaign_id, request_type, payload, base_revision, requested_by, idempotency_key)
on public.campaign_request to authenticated;
drop policy if exists authenticated_insert on public.campaign_request;
create policy authenticated_insert on public.campaign_request for insert to authenticated
with check (public.ca_is_dashboard_user() and requested_by = public.ca_jwt_email());

-- Read what the Campaign Agent shows: the same columns the `campaign_dashboard` role may read.
grant select on public.campaign to authenticated;
grant select (
    id, campaign_id, request_type, base_revision, requested_by, status, error_code, error_detail, result,
    created_at, finished_at
) on public.campaign_request to authenticated;
grant select (campaign_id, current_state, revision, review_view, updated_at) on public.campaign_state to authenticated;
grant select on public.campaign_version to authenticated;
grant select on public.ca_heartbeat to authenticated;
grant select on public.ca_control to authenticated;
do $$
declare t text;
begin
    foreach t in array array['campaign', 'campaign_request', 'campaign_state', 'campaign_version', 'ca_heartbeat', 'ca_control'] loop
        execute format('drop policy if exists authenticated_read on public.%I', t);
        execute format(
            'create policy authenticated_read on public.%I for select to authenticated using (public.ca_is_dashboard_user())', t
        );
    end loop;
end $$;

-- Pause and resume, under the signed-in person's own email (the second argument of ca_request_state is only a label).
create or replace function public.ca_request_state_signed_in(p_state text)
returns public.ca_control
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
    if not public.ca_is_dashboard_user() then
        raise exception 'not allowed' using errcode = '42501';
    end if;
    return public.ca_request_state(p_state, public.ca_jwt_email());
end $$;
revoke execute on function public.ca_request_state_signed_in(text) from public;
grant execute on function public.ca_request_state_signed_in(text) to authenticated;
