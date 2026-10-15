-- The automatic start must not touch a campaign the dashboard has deleted. DRAFT: review before running; apply after 0022.
--
-- The dashboard deletes a campaign softly: it sets campaign.deleted_at and leaves the row. The function from 0021 did not look
-- at that column, so a campaign deleted before the Campaign Agent had started on it could still be started. Same function, one
-- more condition. (A campaign that is already live is switched off by the dashboard asking for `deactivate`, see
-- docs/dashboard-integration.md.)

create or replace function public.ca_auto_interpret(p_grace_s integer, p_max_age_h integer)
returns table (started_campaign_id uuid)
language sql
security definer
set search_path = public, pg_temp
as $$
    insert into public.campaign_request (campaign_id, request_type, payload, requested_by, idempotency_key)
    select c.id, 'interpret', '{}'::jsonb, 'campaign-agent (auto-start)', 'auto-interpret'
    from public.campaign c
    where c.deleted_at is null
      and char_length(btrim(coalesce(c.campaign_detail, ''))) > 0
      and c.created_at < now() - make_interval(secs => greatest(p_grace_s, 0))
      and c.created_at > now() - make_interval(hours => greatest(p_max_age_h, 1))
      and not exists (select 1 from public.campaign_request r where r.campaign_id = c.id)
      and not exists (select 1 from public.campaign_state s where s.campaign_id = c.id)
    on conflict (campaign_id, idempotency_key) do nothing
    returning campaign_request.campaign_id;
$$;
