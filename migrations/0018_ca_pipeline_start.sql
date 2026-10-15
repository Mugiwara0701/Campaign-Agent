-- Lets the Campaign Agent start and stop the Marketing Agent's pipeline: the same function the dashboard's Start button
-- calls (request_pipeline, defined in the Marketing Agent's migration 0012), and read the one status row. DRAFT: review
-- before running; apply after 0017. It changes nothing else: the Campaign Agent still has no access to leads, emails,
-- contacts, Slack or Gmail, and sending email stays off its hands (every email needs a person's Slack approval).
-- Skipped when the Marketing Agent's tables are not in this database.

do $$
begin
    if to_regclass('public.pipeline_control') is null or to_regprocedure('public.request_pipeline(text,text)') is null then
        raise notice 'the Marketing Agent''s pipeline_control / request_pipeline() are not in this database; nothing granted';
        return;
    end if;
    grant select on public.pipeline_control to campaign_agent;
    drop policy if exists campaign_agent_read on public.pipeline_control;
    create policy campaign_agent_read on public.pipeline_control for select to campaign_agent using (true);
    grant execute on function public.request_pipeline(text, text) to campaign_agent;
end $$;
