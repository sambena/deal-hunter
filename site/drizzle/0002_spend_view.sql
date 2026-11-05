-- What the owner console sees of Deal Hunter's AI (Den #924, #927): each month's totals of the calls run on the admin's
-- AI (the console's key), and nothing else: never a person, a model or a row. People's own keys are not counted.
-- cave_spend is the console's read-only role in cave-db; without it (local development, PGlite) nothing is granted.
CREATE OR REPLACE VIEW "deals"."cave_ai_month" AS
  SELECT 'deals'::text AS app, to_char(to_timestamp(u.at) AT TIME ZONE 'UTC', 'YYYY-MM') AS month,
         SUM(round(u.cost * 1000000))::bigint AS billed_micros, COUNT(*)::bigint AS calls
    FROM "deals"."ai_usage" u JOIN "deals"."members" m ON m.id = COALESCE(u.paid_by, u.member_id)
   WHERE m.admin
   GROUP BY 2;--> statement-breakpoint
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cave_spend') THEN
    GRANT USAGE ON SCHEMA "deals" TO cave_spend;
    GRANT SELECT ON "deals"."cave_ai_month" TO cave_spend;
  END IF;
END $$;
