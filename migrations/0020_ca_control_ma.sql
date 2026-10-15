-- Pausing the Campaign Agent also pauses the Marketing Agent. DRAFT: review before running; apply after 0019. One column: it remembers that the Marketing Agent's pipeline was RUNNING when the pause came, so
-- that resuming the Campaign Agent starts it again (and a pipeline that was already stopped stays stopped).
-- Needs migration 0018 (the Campaign Agent may call request_pipeline). Without this column the service still pauses
-- itself as in 0019 and leaves the Marketing Agent alone.

alter table public.ca_control add column if not exists ma_resume boolean not null default false;
grant update (ma_resume) on public.ca_control to campaign_agent;
