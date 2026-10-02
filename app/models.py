import reflex as rx
from sqlalchemy import String, Text
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    MappedAsDataclass,
    mapped_column,
)


class Base(MappedAsDataclass, DeclarativeBase, kw_only=True):
    pass


class PanelStateSnapshot(Base):
    __tablename__ = "panel_state_snapshot"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default="panel"
    )
    payload: Mapped[str] = mapped_column(Text, default="{}")
