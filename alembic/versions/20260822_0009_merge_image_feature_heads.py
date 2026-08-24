"""merge image feature heads

Revision ID: 20260822_0009
Revises: 20260820_0008, 20260822_0008
Create Date: 2026-08-22
"""

from typing import Sequence, Union


revision: str = "20260822_0009"
down_revision: Union[str, Sequence[str], None] = ("20260820_0008", "20260822_0008")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
