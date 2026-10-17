-- Re-applied by every `python -m campaign_agent migrate` (see store/migrate.py), so it is safe to repeat.
-- The Campaign Agent may start and stop the Marketing Agent's pipeline (the function the dashboard's Start button calls,
-- request_pipeline() from the Marketing Agent's migration 0012) and read its one status row. Migration 0018 did the same
-- once, but only if the Marketing Agent's objects were already there: applied in the other order it granted nothing and
-- was never run again (audit 2026-10-17, F-22). Here, it is granted as soon as they exist.

do $$
begin
    if to_regclass('public.pipeline_control') is null or to_regprocedure('public.request_pipeline(text,text)') is null then
        raise notice 'the Marketing Agent''s pipeline_control / request_pipeline() are not in this database yet; run migrate again after the Marketing Agent''s migrations';
        return;
    end if;
    grant select on public.pipeline_control to campaign_agent;
    drop policy if exists campaign_agent_read on public.pipeline_control;
    create policy campaign_agent_read on public.pipeline_control for select to campaign_agent using (true);
    grant execute on function public.request_pipeline(text, text) to campaign_agent;
end $$;
