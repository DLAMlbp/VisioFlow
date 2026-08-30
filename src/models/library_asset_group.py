from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

if TYPE_CHECKING:
    from src.models.library_asset import LibraryAsset


class LibraryAssetGroup(Base):
    __tablename__ = "library_asset_groups"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    tag_key: Mapped[str] = mapped_column(String(2000), unique=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    assets: Mapped[list[LibraryAsset]] = relationship(back_populates="group")
