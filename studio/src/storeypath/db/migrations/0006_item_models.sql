-- Item types' 3D models (models.py; format 0.9.2): a .glb a person added for a type, kept
-- as it came (``source``, to make the model again turned another way) and as the small
-- file packages carry (``glb``): one mesh in the type's own frame, simplified. A type
-- keeps its code for good, so its model is kept by it; the type's ``model`` in the
-- catalogue follows from which types have one here.
CREATE TABLE storeypath.item_models (
    code text PRIMARY KEY,
    name text NOT NULL,                  -- the file's name as it came
    source bytea NOT NULL,               -- the file as it came
    source_sha256 text NOT NULL,
    source_triangles integer NOT NULL,
    units text NOT NULL,                 -- m, cm or mm: as its size said it was drawn in
    turn integer NOT NULL DEFAULT 0,     -- degrees, counter-clockwise seen from above
    glb bytea NOT NULL,                  -- the model packages carry
    sha256 text NOT NULL,
    triangles integer NOT NULL,
    size jsonb NOT NULL,                 -- its width, depth and height, metres
    added_by jsonb,                      -- who added it (history.who_of)
    added_at timestamptz NOT NULL DEFAULT now()
);
