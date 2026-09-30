import reflex as rx


def luffy_page() -> rx.Component:
    return rx.el.main(
        rx.script(
            """
            const backendHost = window.location.host
                .replace(/^8080-/, '8000-')
                .replace(/:8080$/, ':8000');
            window.location.replace(
                window.location.protocol + '//' + backendHost + '/login'
            );
            """
        ),
        rx.el.link(
            rel="stylesheet",
            href="https://fonts.googleapis.com/css2?family=Cinzel:wght@500;600&family=Inter:wght@400;500&family=Vazirmatn:wght@400;500;600&display=swap",
        ),
        rx.el.section(
            rx.el.div(
                rx.el.span(
                    "LUFFY",
                    class_name="font-['Cinzel',serif] text-2xl font-semibold tracking-[0.3em] text-[#d8b773]",
                ),
                rx.el.div(class_name="mx-auto mt-5 h-px w-12 bg-[#b89558]"),
                class_name="mb-8",
            ),
            rx.el.h1(
                "در حال انتقال به پنل…",
                class_name="text-xl font-semibold text-[#f0eadf] sm:text-2xl",
            ),
            rx.el.p(
                "اگر انتقال خودکار انجام نشد، از لینک زیر وارد شوید.",
                class_name="mt-3 text-sm font-normal text-[#aaa49a]",
            ),
            rx.el.a(
                "ورود به پنل",
                href=rx.get_upload_url("login")
                .replace("/_upload/login", "/login")
                .replace("://8080-", "://8000-"),
                class_name="mt-8 inline-flex min-h-11 items-center justify-center rounded-md border border-[#b89558] px-6 py-2 text-sm font-medium text-[#e4c383] transition-colors hover:bg-[#b89558]/15 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[#e4c383]",
            ),
            dir="rtl",
            class_name="w-full max-w-md px-6 text-center",
        ),
        class_name="flex min-h-dvh w-full items-center justify-center bg-[#101012] font-['Vazirmatn','Inter',sans-serif] text-[#f0eadf]",
    )
