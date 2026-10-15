-- Mezzanines (format 0.11): a floor standing in another floor of its building, over part
-- of it. host: the code of that floor (one of the building's that is not a mezzanine);
-- above_host: how far above that floor's level its own is, metres (its elevation is the
-- host's and that). Both NULL: not a mezzanine. A mezzanine has its host's ordinal: a
-- building has one floor per ordinal that is not a mezzanine, and any number on it.
ALTER TABLE floors ADD COLUMN IF NOT EXISTS host text;
ALTER TABLE floors ADD COLUMN IF NOT EXISTS above_host double precision
    CHECK (above_host IS NULL OR above_host > 0);
