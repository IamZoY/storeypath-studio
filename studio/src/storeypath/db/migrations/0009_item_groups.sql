-- Items placed or grouped together (format 0.10): a workstation's desk, chair and pedestal; a
-- meeting table and its chairs. Each piece is an item of its own, with its own ID; the items
-- of one group share this key, and no other item has it. NULL: in no group. A group is a
-- link, not an asset: it has no tag of its own.
ALTER TABLE items ADD COLUMN IF NOT EXISTS group_key text;
CREATE INDEX IF NOT EXISTS items_of_group ON items (project, group_key) WHERE group_key IS NOT NULL;
