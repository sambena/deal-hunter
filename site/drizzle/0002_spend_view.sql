-- What the owner console sees of Deal Hunter's AI (Den #924, #927): each month's totals of the calls run on the admin's
-- AI (the console's key), and nothing else: never a person, a model or a row. People's own keys are not counted.
-- The grant to cave-db's cave_spend role is made on every start (src/app.ts), so a role made later still gets it.
CREATE OR REPLACE VIEW "deals"."cave_ai_month" AS
  SELECT 'deals'::text AS app, to_char(to_timestamp(u.at) AT TIME ZONE 'UTC', 'YYYY-MM') AS month,
         SUM(round(u.cost * 1000000))::bigint AS billed_micros, COUNT(*)::bigint AS calls
    FROM "deals"."ai_usage" u JOIN "deals"."members" m ON m.id = COALESCE(u.paid_by, u.member_id)
   WHERE m.admin
   GROUP BY 2;
