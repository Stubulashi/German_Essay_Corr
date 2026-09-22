"""add handwriting training

Revision ID: c71d9f4b2a30
Revises: b924fa2a598d
Create Date: 2026-09-20 20:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c71d9f4b2a30'
down_revision: Union[str, Sequence[str], None] = 'b924fa2a598d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('handwriting_samples',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('class_id', sa.Integer(), nullable=False),
    sa.Column('student_name', sa.String(length=128), nullable=True),
    sa.Column('student_id', sa.String(length=64), nullable=True),
    sa.Column('image_path', sa.String(length=255), nullable=False),
    sa.Column('name_crop_path', sa.String(length=255), nullable=True),
    sa.Column('features', sa.JSON(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['class_id'], ['classes.id'], name='fk_handwriting_samples_class_id'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('handwriting_samples', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_handwriting_samples_class_id'), ['class_id'], unique=False)

    op.create_table('handwriting_models',
    sa.Column('class_id', sa.Integer(), nullable=False),
    sa.Column('samples_count', sa.Integer(), nullable=False),
    sa.Column('students_count', sa.Integer(), nullable=False),
    sa.Column('level', sa.String(length=16), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['class_id'], ['classes.id'], name='fk_handwriting_models_class_id'),
    sa.PrimaryKeyConstraint('class_id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('handwriting_models')
    with op.batch_alter_table('handwriting_samples', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_handwriting_samples_class_id'))

    op.drop_table('handwriting_samples')
