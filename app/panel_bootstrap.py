import logging


def install_panel_integrations(panel: object) -> None:
    if getattr(panel, "_panel_integrations_installed", False):
        return
    setattr(panel, "_panel_integrations_installed", True)
    try:
        from app.panel_storage import install_panel_storage

        install_panel_storage(panel)
        from app.admin_inbound import install_admin_inbound

        install_admin_inbound(panel)
        from app.panel_domain import install_domain_ui

        install_domain_ui()
    except Exception:
        logging.exception("Unexpected error")
        setattr(panel, "_panel_integrations_installed", False)
        raise
