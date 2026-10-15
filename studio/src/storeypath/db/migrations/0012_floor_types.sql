-- What level of its building a floor is (format 0.11): 'floor' (a storey), 'mezzanine'
-- (one standing in a floor of its building, its host and above_host set) or 'roof' (the
-- building's roof as a level, at its top: open air, the rooms standing on it indoor). A
-- mezzanine was told by its host alone before: those with one are mezzanines.
ALTER TABLE floors ADD COLUMN IF NOT EXISTS type text NOT NULL DEFAULT 'floor'
    CHECK (type IN ('floor', 'mezzanine', 'roof'));
UPDATE floors SET type = 'mezzanine' WHERE host IS NOT NULL AND type = 'floor';
