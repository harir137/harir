import reflex as rx


class WelcomeState(rx.State):
    greeting: str = "أهلًا بك، نتمنى لك يومًا جميلًا"

    @rx.event
    def begin(self):
        self.greeting = (
            "يا مرحبًا بك! يسعدنا حضورك، ونتمنى أن يملأ يومك الخير والطمأنينة."
        )
