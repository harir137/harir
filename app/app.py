import reflex as rx

from app.states.welcome_state import WelcomeState


def index() -> rx.Component:
    return rx.el.main(
        rx.el.div(
            class_name="welcome-orbit welcome-orbit--top-outer",
            aria_hidden="true",
        ),
        rx.el.div(
            class_name="welcome-orbit welcome-orbit--top-inner",
            aria_hidden="true",
        ),
        rx.el.div(
            class_name="welcome-orbit welcome-orbit--bottom-outer",
            aria_hidden="true",
        ),
        rx.el.div(
            class_name="welcome-orbit welcome-orbit--bottom-inner",
            aria_hidden="true",
        ),
        rx.el.section(
            rx.el.div(
                rx.el.span(class_name="welcome-eyebrow-line"),
                rx.el.span(
                    "مساحةٌ من الهدوء",
                    class_name="welcome-eyebrow-text",
                ),
                rx.el.span(class_name="h-px w-8 bg-[#87977D]"),
                class_name="welcome-eyebrow",
            ),
            rx.el.div(
                rx.el.div(
                    class_name="welcome-emblem-ring welcome-emblem-ring--outer",
                    aria_hidden="true",
                ),
                rx.el.div(
                    class_name="welcome-emblem-ring welcome-emblem-ring--inner",
                    aria_hidden="true",
                ),
                rx.el.h1(
                    "سلام",
                    class_name="welcome-title",
                ),
                class_name="welcome-emblem",
            ),
            rx.el.div(
                rx.el.span(class_name="welcome-divider-line"),
                rx.el.span(class_name="welcome-divider-dot"),
                rx.el.span(class_name="h-px w-12 bg-[#87977D]/50"),
                class_name="welcome-divider",
                aria_hidden="true",
            ),
            rx.el.p(
                WelcomeState.greeting,
                class_name="welcome-greeting",
                aria_live="polite",
            ),
            rx.el.button(
                "ابدأ",
                rx.icon("arrow-left", class_name="welcome-button-icon"),
                on_click=WelcomeState.begin,
                type="button",
                class_name="welcome-button",
            ),
            class_name="welcome-content",
        ),
        class_name="welcome-page",
        dir="rtl",
        lang="ar",
    )


app = rx.App(
    theme=rx.theme(appearance="light"),
    stylesheets=["/welcome.css"],
    head_components=[
        rx.el.link(rel="preconnect", href="https://fonts.googleapis.com"),
        rx.el.link(
            rel="preconnect", href="https://fonts.gstatic.com", cross_origin=""
        ),
        rx.el.link(
            rel="stylesheet",
            href="https://fonts.googleapis.com/css2?family=Amiri:wght@400;700&family=Tajawal:wght@400;500;700&display=swap",
        ),
    ],
)
app.add_page(
    index,
    route="/",
    title="سلام | أهلًا بك",
    description="مساحة هادئة لترحيب دافئ بك.",
)
