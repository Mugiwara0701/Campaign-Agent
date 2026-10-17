-- A version can only be approved through ca_publish(). DRAFT: review before running; apply after 0024. (Audit 2026-10-17, F-14.)
--
-- 0013 granted the campaign_agent role INSERT on every column of campaign_version (the column list in that grant applied
-- to UPDATE only), and 0015 revoked only the UPDATE of approved_by / approved_at. So the role could insert a row that was
-- already "approved" and then make it live with ca_activate(), without ca_publish() and without a person. The Campaign
-- Agent's code never does this; the database now makes it impossible:
--   * the role may insert only the content columns, and
--   * a trigger refuses any new row that arrives approved (this also holds for the owner and any future role).

revoke insert on public.campaign_version from campaign_agent;
grant insert (
    campaign_id, version_no, overlay_yaml, sha256, ir_hash, contract_version, validation_report, not_applied, ir_snapshot
) on public.campaign_version to campaign_agent;

create or replace function public.campaign_version_insert_guard() returns trigger
language plpgsql as $$
begin
    if new.approved_at is not null or new.approved_by is not null then
        raise exception 'a campaign version is approved only through ca_publish(), never when it is inserted';
    end if;
    return new;
end;
$$;

drop trigger if exists campaign_version_insert_guard on public.campaign_version;
create trigger campaign_version_insert_guard
before insert on public.campaign_version
for each row execute function public.campaign_version_insert_guard();
