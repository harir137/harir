from fastapi import Depends, HTTPException, Request


import logging


def install_admin_inbound(panel) -> None:
    original_ensure_default_link = panel.ensure_default_link
    original_toggle_command = panel.handle_toggle_command

    async def ensure_admin_link() -> None:
        links_were_empty = not panel.LINKS
        await original_ensure_default_link()
        changed = links_were_empty
        async with panel.LINKS_LOCK:
            admin = panel.LINKS.get("admin")
            if admin is None:
                admin = {
                    "label": "admin",
                    "limit_bytes": 0,
                    "used_bytes": 0,
                    "max_connections": 0,
                    "created_at": panel.datetime.now(
                        panel.timezone.utc
                    ).isoformat(),
                    "active": True,
                    "expires_at": None,
                    "ports": [panel.DEFAULT_PORT],
                }
                panel.LINKS["admin"] = admin
                changed = True
            for key, value in (
                ("label", "admin"),
                ("active", True),
                ("expires_at", None),
                ("limit_bytes", 0),
                ("max_connections", 0),
            ):
                if admin.get(key) != value:
                    admin[key] = value
                    changed = True
        if changed:
            panel.save_db()

    async def handle_toggle_command(text: str, active_state: bool) -> str:
        parts = text.split()
        if len(parts) >= 2 and parts[1].strip() == "admin" and not active_state:
            return "The admin inbound is permanent and cannot be disabled."
        return await original_toggle_command(text, active_state)

    panel.ensure_default_link = ensure_admin_link
    panel.handle_toggle_command = handle_toggle_command

    for route in panel.app.routes:
        if getattr(route, "path", None) != "/api/links/{uid}":
            continue
        method_names = getattr(route, "methods", set())
        if "PATCH" in method_names:
            original_patch = route.endpoint

            async def protected_patch(
                uid: str,
                request: Request,
                _=Depends(panel.require_auth),
            ):
                if uid == "admin":
                    try:
                        body = await request.json()
                    except (ValueError, UnicodeDecodeError):
                        logging.exception("Unexpected error")
                        body = {}
                    if isinstance(body, dict) and (
                        ("active" in body and not bool(body["active"]))
                        or any(
                            key in body
                            for key in (
                                "limit_value",
                                "limit_unit",
                                "days_valid",
                                "expires_at",
                                "max_connections",
                                "label",
                            )
                        )
                    ):
                        raise HTTPException(
                            status_code=403,
                            detail="The admin inbound is permanent and its settings cannot be changed.",
                        )
                return await original_patch(uid, request, _)

            route.endpoint = protected_patch
            route.dependant.call = protected_patch
        elif "DELETE" in method_names:
            original_delete = route.endpoint

            async def protected_delete(
                uid: str,
                _=Depends(panel.require_auth),
            ):
                if uid == "admin":
                    raise HTTPException(
                        status_code=403,
                        detail="The admin inbound is permanent and cannot be deleted.",
                    )
                return await original_delete(uid, _)

            route.endpoint = protected_delete
            route.dependant.call = protected_delete
