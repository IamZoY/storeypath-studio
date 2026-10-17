"""Finding the way in a building (navigation.py): its walking network as it is now, or
the way on it from a place to a place, for whoever may see the whole building. Across a
location's site (format 0.13): its land's network with its buildings', or the way across
it, for whoever may see its land (each building in it they may see whole)."""

from __future__ import annotations

from fastapi import Request

from .calls import Calls, May, Query, TheStudio, answer

calls = Calls()


def _said(query: dict) -> tuple[str | None, str | None, bool]:
    said = {k: (query.get(k) or [None])[0] for k in ("from", "to", "accessible")}
    return said["from"], said["to"], (said["accessible"] or "").lower() in ("1", "true", "yes")


@calls.get("/api/projects/{code}/buildings/{building_id}/navigation")
def navigation(code: str, building_id: str, request: Request, may: May, studio: TheStudio, query: Query):
    may.building(code, building_id, "view")
    start, end, accessible = _said(query)
    return answer(request, studio.navigation(code, building_id, start, end, accessible))


@calls.get("/api/projects/{code}/locations/{location_id}/navigation")
def site_navigation(code: str, location_id: str, request: Request, may: May, studio: TheStudio, query: Query):
    sight = may.site(code, location_id, "view")
    start, end, accessible = _said(query)
    return answer(request, studio.site_navigation(code, location_id, start, end, accessible, sight))
