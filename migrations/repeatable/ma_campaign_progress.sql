-- Re-applied by every `python -m campaign_agent migrate` (see store/migrate.py), so it is safe to repeat.
-- How far the Marketing Agent got with one campaign, for the dashboard: counts of its leads by lead status and of its
-- emails by email status. Counts only: no company, person or address leaves the Marketing Agent's tables this way.
-- It needs the Marketing Agent's campaign attribution (its migration 0014: companies/emails.source_campaign_id); until
-- that is applied this does nothing and the gateway reports `execution: null`.

do $outer$
begin
    if to_regclass('public.companies') is null or to_regclass('public.emails') is null
       or not exists (
           select 1 from information_schema.columns
           where table_schema = 'public' and table_name = 'emails' and column_name = 'source_campaign_id'
       ) then
        raise notice 'the Marketing Agent''s campaign attribution (its migration 0014) is not in this database yet';
        return;
    end if;
    execute $fn$
        create or replace function public.ca_campaign_progress(p_campaign uuid) returns jsonb
        language sql stable security definer set search_path = public, pg_temp
        as $body$
            select jsonb_build_object(
                'leads', coalesce((
                    select jsonb_object_agg(x.lead_status, x.n) from (
                        select co.lead_status, count(*) as n from public.companies as co
                        where co.source_campaign_id = p_campaign group by co.lead_status
                    ) as x
                ), '{}'::jsonb),
                'emails', coalesce((
                    select jsonb_object_agg(y.status, y.n) from (
                        select e.status, count(*) as n from public.emails as e
                        where e.source_campaign_id = p_campaign group by e.status
                    ) as y
                ), '{}'::jsonb)
            )
        $body$
    $fn$;
    revoke all on function public.ca_campaign_progress(uuid) from public;
    grant execute on function public.ca_campaign_progress(uuid) to campaign_dashboard;
    grant execute on function public.ca_campaign_progress(uuid) to campaign_agent;
end $outer$;
