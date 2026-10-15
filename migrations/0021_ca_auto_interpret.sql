-- The Campaign Agent starts on a new campaign by itself. DRAFT: review before running; apply after 0020 (or 0019).
--
-- The dashboard's job is to save the campaign. If it does not also send the `interpret` request (or sends it late), the
-- Campaign Agent would wait forever. This function lets the service notice such a campaign and send that one request.
-- It is deliberately narrow: it can only create an `interpret` request, only for a campaign that
--   * has some text (campaign_detail),
--   * has no request at all and no state yet (nobody has touched it, so a discarded or finished campaign is never revived),
--   * is older than a short grace period (so the dashboard's own request, sent a moment after the campaign, wins), and
--   * is not old (a campaign saved long before this was switched on is not suddenly interpreted).
-- The Campaign Agent role gets this function, not a general right to insert requests. Approval stays with a person.

create or replace function public.ca_auto_interpret(p_grace_s integer, p_max_age_h integer)
returns table (started_campaign_id uuid)
language sql
security definer
set search_path = public, pg_temp
as $$
    insert into public.campaign_request (campaign_id, request_type, payload, requested_by, idempotency_key)
    select c.id, 'interpret', '{}'::jsonb, 'campaign-agent (auto-start)', 'auto-interpret'
    from public.campaign c
    where char_length(btrim(coalesce(c.campaign_detail, ''))) > 0
      and c.created_at < now() - make_interval(secs => greatest(p_grace_s, 0))
      and c.created_at > now() - make_interval(hours => greatest(p_max_age_h, 1))
      and not exists (select 1 from public.campaign_request r where r.campaign_id = c.id)
      and not exists (select 1 from public.campaign_state s where s.campaign_id = c.id)
    on conflict (campaign_id, idempotency_key) do nothing
    returning campaign_request.campaign_id;
$$;

revoke execute on function public.ca_auto_interpret(integer, integer) from public;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke execute on function public.ca_auto_interpret(integer, integer) from %I', r);
        end if;
    end loop;
end $$;
grant execute on function public.ca_auto_interpret(integer, integer) to campaign_agent;
