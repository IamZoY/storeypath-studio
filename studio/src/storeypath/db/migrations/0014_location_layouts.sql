-- Location layouts (format 0.12): a location's land, read from a site plan: its plot, its
-- boundary walls and gates, its outdoor areas, what is placed on it and the footprints of
-- its buildings. The land is kept as a building is, its code LAND (no building may have
-- it), its one level of type 'layout' (G00); a footprint is an object of its own kind.
ALTER TABLE floors DROP CONSTRAINT IF EXISTS floors_type_check;
ALTER TABLE floors ADD CONSTRAINT floors_type_check CHECK (type IN ('floor', 'mezzanine', 'roof', 'layout'));
-- a layout's level: what its site plan's north arrow was read as (workspace.NorthRead)
ALTER TABLE floors ADD COLUMN IF NOT EXISTS north jsonb;
ALTER TABLE objects DROP CONSTRAINT IF EXISTS objects_kind_check;
ALTER TABLE objects ADD CONSTRAINT objects_kind_check CHECK (kind IN ('space', 'zone', 'opening', 'footprint'));
-- a footprint's building as a person chose it: a building's ID, '' for none
ALTER TABLE overrides ADD COLUMN IF NOT EXISTS building text CHECK (length(building) <= 100);
-- which way north is on the location's site plan (workspace.North)
ALTER TABLE locations ADD COLUMN IF NOT EXISTS north jsonb;
