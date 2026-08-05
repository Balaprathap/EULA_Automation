-- 0010: Provision an organization and profile when a user signs up.
--
-- Runs inside the auth.users insert transaction, so a signed-in user always has
-- exactly one profile and one organization - there is no window in which a
-- session exists without a tenant.

CREATE OR REPLACE FUNCTION handle_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    new_org_id   UUID;
    base_slug    TEXT;
    final_slug   TEXT;
    suffix       INTEGER := 0;
    display_name TEXT;
BEGIN
    display_name := COALESCE(
        NEW.raw_user_meta_data->>'full_name',
        NEW.raw_user_meta_data->>'name',
        split_part(NEW.email, '@', 1)
    );

    base_slug := regexp_replace(lower(split_part(NEW.email, '@', 1)), '[^a-z0-9]+', '-', 'g');
    base_slug := trim(both '-' from base_slug);
    IF length(base_slug) < 2 THEN
        base_slug := 'org';
    END IF;
    final_slug := base_slug;

    WHILE EXISTS (SELECT 1 FROM organizations WHERE slug = final_slug) LOOP
        suffix := suffix + 1;
        final_slug := base_slug || '-' || suffix::text;
    END LOOP;

    INSERT INTO organizations (name, slug)
    VALUES (display_name || '''s workspace', final_slug)
    RETURNING id INTO new_org_id;

    -- The first user of a new organization owns it.
    INSERT INTO profiles (id, org_id, email, full_name, role)
    VALUES (NEW.id, new_org_id, NEW.email, display_name, 'owner');

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION handle_new_user();
