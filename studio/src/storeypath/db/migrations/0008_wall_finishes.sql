-- One wall of a room finished otherwise than the rest (format 0.9.3): an accent wall, set
-- by a person in review, kept with the room's other corrections: [{"line": [[x, y], [x, y]],
-- "finish": "WALL-…"}, …], each line along the wall's face towards the room, in metres of the
-- building's own frame (as an item's place); NULL, none. The rest of its walls are its
-- wall_finish (else its type's).
ALTER TABLE overrides ADD COLUMN IF NOT EXISTS wall_finishes jsonb;
