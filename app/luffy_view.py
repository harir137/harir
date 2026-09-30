import reflex as rx


def luffy_page() -> rx.Component:
    return rx.el.main(
        rx.el.iframe(
            src=f"{rx.get_upload_url('panel').split('/_upload/')[0]}/login",
            title="Luffy Panel",
            style={
                "display": "block",
                "width": "100%",
                "height": "100%",
                "border": "0",
                "background_color": "#060608",
            },
        ),
        style={
            "width": "100vw",
            "height": "100dvh",
            "margin": "0",
            "padding": "0",
            "overflow": "hidden",
            "background_color": "#060608",
        },
    )
