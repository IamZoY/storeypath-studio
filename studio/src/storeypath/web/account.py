"""The calls of the person's own account, the item types, the users (admins), the
audit log, backups and the GPU helpers (admins). (Every POST takes a JSON object, used
or not, as it always has: anything else is answered 400.)"""

from __future__ import annotations

from fastapi import Request

from .calls import Body, Calls, May, Query, Sent, TheStudio, answer

calls = Calls()


# ---- logging in and out, and the person's own -------------------------------------------

@calls.post("/api/login")
def login(request: Request, may: May, body: Body):
    may.anyone()
    return answer(request, may.login(body))


@calls.post("/api/logout")
def logout(request: Request, may: May, body: Body):
    may.anyone()
    return answer(request, may.logout())


@calls.get("/api/me")
def me(request: Request, may: May):
    may.me()
    return answer(request, may.whoami())


@calls.post("/api/me/password")
def own_password(request: Request, may: May, body: Body):
    may.me()
    return answer(request, may.change_password(body))


@calls.get("/api/status")
def status(request: Request, may: May, studio: TheStudio):
    user = may.logged_in()
    return answer(request, studio.status(paths=user.role == "admin"))


@calls.get("/api/catalogue")
def catalogue(request: Request, may: May, studio: TheStudio):
    may.logged_in()
    return answer(request, studio.catalogue().model_dump())


@calls.post("/api/catalogue")
def save_catalogue(request: Request, may: May, studio: TheStudio, body: Body):
    may.capability("catalogue")
    before = {t.code: t.model_dump() for t in studio.catalogue().types}
    saved = studio.save_catalogue(body)
    added = [t["code"] for t in saved["types"] if t["code"] not in before]
    changed = [t["code"] for t in saved["types"] if t["code"] in before and before[t["code"]] != t]
    if added or changed:
        may.audit("item types changed", None, added=added, changed=changed)
    return answer(request, saved)


@calls.get("/api/catalogue/models")
def models(request: Request, may: May, studio: TheStudio):
    """What each item type's 3D model is, by its code (models.py)."""
    may.logged_in()
    return answer(request, studio.models())


@calls.get("/api/catalogue/models/{code}.glb")
def model_file(code: str, request: Request, may: May, studio: TheStudio):
    may.logged_in()
    return answer(request, studio.model_file(code))


@calls.get("/api/catalogue/models/{code}.svg")
def model_picture(code: str, request: Request, may: May, studio: TheStudio, query: Query):
    may.logged_in()
    return answer(request, studio.model_picture(code, (query.get("view") or ["front"])[0]))


@calls.put("/api/catalogue/types/{code}/model")
def add_model(code: str, request: Request, may: May, studio: TheStudio, sent: Sent, query: Query):
    """A model file sent as a type's 3D model, in any format read (model_files.py): a .glb,
    an FBX, an OBJ… or a .zip of one with its textures (?name= the file's name, ?turn=
    degrees)."""
    may.capability("catalogue")
    turn = (query.get("turn") or ["0"])[0]
    if not turn.lstrip("-").isdigit():
        raise ValueError("turn: 0, 90, 180 or 270 (degrees)")
    made = studio.add_model(code, sent.read(), (query.get("name") or [""])[0], int(turn), by=may.user)
    may.audit("item type model added", None, code=code, name=made["name"], triangles=made["triangles"])
    return answer(request, made)


@calls.post("/api/catalogue/types/{code}/model/turn")
def turn_model(code: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.capability("catalogue")
    made = studio.turn_model(code, body.get("turn"), by=may.user)
    may.audit("item type model turned", None, code=code, turn=made["turn"])
    return answer(request, made)


@calls.post("/api/catalogue/types/{code}/model/fit")
def fit_model(code: str, request: Request, may: May, studio: TheStudio, body: Body):
    """A type's model made again from its file: any of {turn, units, up}."""
    may.capability("catalogue")
    made = studio.fit_model(code, body, by=may.user)
    may.audit("item type model refitted", None, code=code, turn=made["turn"], units=made["units"], up=made["up"])
    return answer(request, made)


@calls.post("/api/catalogue/types/{code}/model/delete")
def drop_model(code: str, request: Request, may: May, studio: TheStudio):
    may.capability("catalogue")
    gone = studio.drop_model(code)
    may.audit("item type model removed", None, code=code)
    return answer(request, gone)


@calls.put("/api/catalogue/types-in")
def types_in(request: Request, may: May, studio: TheStudio, sent: Sent):
    """The item types a file brings (a catalogue .json, a package, a project file), read
    for who may change the item types to choose which to take: nothing is changed."""
    may.capability("catalogue")
    return answer(request, studio.types_in(sent.read()))


@calls.get("/api/users")
def users_to_share(request: Request, may: May):
    may.picker()
    return answer(request, may.users_to_share())


# ---- users (admins), the audit log and backups -------------------------------------------

@calls.get("/api/admin/users")
def all_users(request: Request, may: May):
    may.admin()
    return answer(request, may.all_users())


@calls.post("/api/admin/users")
def add_user(request: Request, may: May, body: Body):
    may.admin()
    return answer(request, may.add_user(body))


@calls.post("/api/admin/users/{user_id}")
def change_user(user_id: str, request: Request, may: May, body: Body):
    may.admin()
    return answer(request, may.change_user(user_id, body))


@calls.post("/api/admin/users/{user_id}/password")
def reset_password(user_id: str, request: Request, may: May, body: Body):
    may.admin()
    return answer(request, may.reset_password(user_id))


@calls.get("/api/admin/audit")
def audit(request: Request, may: May, query: Query):
    may.admin()
    return answer(request, may.audit_log(query))


@calls.get("/api/backup")
def backup(request: Request, may: May):
    may.capability("backup")
    return answer(request, may.backup())


# ---- the GPU helpers (admins): the vision model's servers ---------------------------------

@calls.get("/api/admin/helpers")
def helpers(request: Request, may: May, studio: TheStudio):
    may.admin()
    return answer(request, studio.helpers())


@calls.post("/api/admin/helpers")
def save_helpers(request: Request, may: May, studio: TheStudio, body: Body):
    may.admin()
    saved = studio.save_helpers(body)
    may.audit("gpu helpers changed", None, helpers=[h["url"] for h in saved["helpers"]])
    return answer(request, saved)


@calls.post("/api/admin/helpers/test")
def test_helper(request: Request, may: May, studio: TheStudio, body: Body):
    may.admin()
    return answer(request, studio.test_helper(body))
