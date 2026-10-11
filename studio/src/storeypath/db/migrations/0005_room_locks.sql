-- Several people on one floor, one a room (in place of one editor a floor): a person's
-- change takes the lock of each room it touches (a space; an item standing in no room,
-- by its own ID), held by them until they are done editing the floor, leave it, or make
-- no change to the room for a while (db/store.py ROOM_IDLE_S). The floors' locks go:
-- they were let go as their pages closed, nothing of them is kept.
CREATE TABLE storeypath.room_locks (
    floor text NOT NULL REFERENCES storeypath.floors (id) ON DELETE CASCADE,
    room text NOT NULL,
    project text NOT NULL REFERENCES storeypath.projects (code) ON DELETE CASCADE,
    who text REFERENCES storeypath.users (id) ON DELETE CASCADE,  -- NULL: this computer
    session text,
    since timestamptz NOT NULL DEFAULT now(),
    last_seen timestamptz NOT NULL DEFAULT now(),  -- its person's last change to it
    PRIMARY KEY (floor, room)
);
CREATE INDEX room_locks_of_project ON storeypath.room_locks (project);
DROP TABLE storeypath.floor_locks;
