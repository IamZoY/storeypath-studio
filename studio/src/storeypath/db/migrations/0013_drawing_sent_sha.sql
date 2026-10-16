-- A drawing's file as it was sent: the sha256 of its bytes before anything was done to
-- them (its private information taken out, a DWG converted), so that the same file sent
-- again is known and not kept twice. Null for a drawing kept before this was kept, or
-- one that came in a project file (only its kept sha256 is known then).
ALTER TABLE drawings ADD COLUMN IF NOT EXISTS sent_sha256 text;
