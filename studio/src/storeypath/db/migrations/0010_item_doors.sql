-- The door an item serves (format 0.10): a card reader, a door controller, an exit button,
-- linked to the door it opens, by the door's ID (an opening of the item's floor). NULL: none.
ALTER TABLE items ADD COLUMN IF NOT EXISTS door text;
