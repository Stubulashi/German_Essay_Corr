"""add classes and task metadata

Revision ID: 0694cf704e9f
Revises: 95b3f80f1b85
Create Date: 2026-09-17 22:13:00.617555

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0694cf704e9f'
down_revision: Union[str, Sequence[str], None] = '95b3f80f1b85'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. 班级表
    op.create_table('classes',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('note', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('classes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_classes_name'), ['name'], unique=True)

    # 2. 任务表增加班级与作业元数据(显式命名外键,保证可回滚)
    with op.batch_alter_table('correction_tasks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('class_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('assignment_name', sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column('topic', sa.Text(), nullable=True))
        batch_op.create_index(batch_op.f('ix_correction_tasks_class_id'), ['class_id'], unique=False)
        batch_op.create_foreign_key(
            'fk_correction_tasks_class_id_classes', 'classes', ['class_id'], ['id']
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('correction_tasks', schema=None) as batch_op:
        batch_op.drop_constraint('fk_correction_tasks_class_id_classes', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_correction_tasks_class_id'))
        batch_op.drop_column('topic')
        batch_op.drop_column('assignment_name')
        batch_op.drop_column('class_id')

    with op.batch_alter_table('classes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_classes_name'))

    op.drop_table('classes')
