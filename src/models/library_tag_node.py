from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

if TYPE_CHECKING:
    from src.models.library_asset import LibraryAsset


class LibraryTagNode(Base):
    __tablename__ = "library_tag_nodes"
    __table_args__ = (
        UniqueConstraint("parent_id", "name", name="uq_library_tag_nodes_parent_name"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("library_tag_nodes.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    parent: Mapped[LibraryTagNode | None] = relationship(
        remote_side="LibraryTagNode.id", back_populates="children"
    )
    children: Mapped[list[LibraryTagNode]] = relationship(
        back_populates="parent", order_by="LibraryTagNode.sort_order"
    )
    assets: Mapped[list[LibraryAsset]] = relationship(back_populates="leaf_tag_node")
