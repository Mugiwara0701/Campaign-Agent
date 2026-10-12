-- Publishing for the Campaign Agent. DRAFT: review before running; apply after 0013 and 0014.
--
-- The Marketing Agent runs the campaign whose version is pointed to by campaign_state.active_version_id.
-- That pointer and the approval fields are changed ONLY through the three functions below. The agent's
-- role has no direct write access to them, so the rules are enforced here, not only in the application:
--   * only the latest candidate can be approved, and only with the sha256 the user reviewed;
--   * an approval records who and when, once;
--   * at most one campaign is live at a time: publishing one replaces the previous one.
-- Failures raise SQLSTATE CA001 (not the latest candidate or hash differs), CA002 (no such version or
-- campaign), CA003 (the version was never approved), CA004 (no approver).

-- A frozen copy of the campaign as it was when the candidate was made (audit, and a way back).
alter table public.campaign_version add column if not exists ir_snapshot jsonb;

create or replace function public.campaign_version_guard() returns trigger
language plpgsql as $$
begin
    if new.overlay_yaml is distinct from old.overlay_yaml
        or new.sha256 is distinct from old.sha256
        or new.ir_hash is distinct from old.ir_hash
        or new.ir_snapshot is distinct from old.ir_snapshot
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

-- One more kind of request: stop the live campaign.
alter table public.campaign_request drop constraint if exists campaign_request_request_type_check;
alter table public.campaign_request add constraint campaign_request_request_type_check check (
    request_type in (
        'interpret', 'answer', 'edit', 'suggestion', 'approve', 'discard', 'rollback', 'deactivate'
    )
);

-- At most one campaign has a live version.
create unique index if not exists campaign_state_one_live
on public.campaign_state ((true)) where active_version_id is not null;

-- Approve the reviewed candidate and make it live.
create or replace function public.ca_publish(
    p_campaign uuid, p_version integer, p_sha256 text, p_approver text
) returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $$
declare
    v public.campaign_version%rowtype;
    latest integer;
    superseded jsonb;
begin
    if p_approver is null or char_length(p_approver) not between 1 and 100 then
        raise exception 'an approver is required' using errcode = 'CA004';
    end if;
    select * into v from public.campaign_version
    where campaign_id = p_campaign and version_no = p_version for update;
    if not found then
        raise exception 'no such version' using errcode = 'CA002';
    end if;
    select max(version_no) into latest from public.campaign_version where campaign_id = p_campaign;
    if v.sha256 <> p_sha256 or (v.approved_at is null and v.version_no <> latest) then
        raise exception 'not the latest candidate, or the text changed' using errcode = 'CA001', detail = latest::text;
    end if;
    if v.approved_at is null then
        update public.campaign_version set approved_by = p_approver, approved_at = now() where id = v.id;
    end if;
    select coalesce(jsonb_agg(campaign_id), '[]'::jsonb) into superseded
    from public.campaign_state where active_version_id is not null and campaign_id <> p_campaign;
    update public.campaign_state set active_version_id = null, updated_at = now()
    where active_version_id is not null and campaign_id <> p_campaign;
    update public.campaign_state set active_version_id = v.id, updated_at = now() where campaign_id = p_campaign;
    if not found then
        raise exception 'no such campaign' using errcode = 'CA002';
    end if;
    return jsonb_build_object('version', v.version_no, 'sha256', v.sha256, 'superseded', superseded);
end;
$$;

-- Make a version that was approved before live again (a way back).
create or replace function public.ca_activate(p_campaign uuid, p_version integer) returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $$
declare
    v public.campaign_version%rowtype;
    superseded jsonb;
begin
    select * into v from public.campaign_version
    where campaign_id = p_campaign and version_no = p_version for update;
    if not found then
        raise exception 'no such version' using errcode = 'CA002';
    end if;
    if v.approved_at is null then
        raise exception 'that version was never approved' using errcode = 'CA003';
    end if;
    select coalesce(jsonb_agg(campaign_id), '[]'::jsonb) into superseded
    from public.campaign_state where active_version_id is not null and campaign_id <> p_campaign;
    update public.campaign_state set active_version_id = null, updated_at = now()
    where active_version_id is not null and campaign_id <> p_campaign;
    update public.campaign_state set active_version_id = v.id, updated_at = now() where campaign_id = p_campaign;
    if not found then
        raise exception 'no such campaign' using errcode = 'CA002';
    end if;
    return jsonb_build_object('version', v.version_no, 'sha256', v.sha256, 'superseded', superseded);
end;
$$;

-- Stop: no campaign is live for this one any more. Its versions stay.
create or replace function public.ca_deactivate(p_campaign uuid) returns jsonb
language plpgsql security definer set search_path = public, pg_temp as $$
begin
    update public.campaign_state set active_version_id = null, updated_at = now() where campaign_id = p_campaign;
    if not found then
        raise exception 'no such campaign' using errcode = 'CA002';
    end if;
    return jsonb_build_object('stopped', true);
end;
$$;

revoke all on function public.ca_publish(uuid, integer, text, text) from public;
revoke all on function public.ca_activate(uuid, integer) from public;
revoke all on function public.ca_deactivate(uuid) from public;
grant execute on function public.ca_publish(uuid, integer, text, text) to campaign_agent;
grant execute on function public.ca_activate(uuid, integer) to campaign_agent;
grant execute on function public.ca_deactivate(uuid) to campaign_agent;

-- The agent's role can no longer move the live pointer or approve anything directly.
revoke insert, update on public.campaign_state from campaign_agent;
grant insert (campaign_id, current_state, revision, ir, conversation, review_view, candidate_version_id)
on public.campaign_state to campaign_agent;
grant update (current_state, revision, ir, conversation, review_view, candidate_version_id, updated_at)
on public.campaign_state to campaign_agent;
revoke update (approved_by, approved_at) on public.campaign_version from campaign_agent;
