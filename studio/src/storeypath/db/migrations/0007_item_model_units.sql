-- Item types' 3D models in any format (model_files.py): how a model was read, so that it
-- is made again the same way (turned, or in other units). ``units`` stays what it was read
-- in; ``units_from`` says how that was known: the file said so ("file"), its size did
-- ("size"), or a person chose them (``units_chosen``, null when not). ``up`` the way up
-- it was drawn: "y" (glTF's), or "z" (stood up: as CAD draws). ``size_before`` the type's
-- size before it had a model (kept while it has one), what a model's size is held to.
ALTER TABLE storeypath.item_models
    ADD COLUMN units_from text NOT NULL DEFAULT 'size',
    ADD COLUMN units_chosen text,
    ADD COLUMN up text NOT NULL DEFAULT 'y',
    ADD COLUMN size_before jsonb;
