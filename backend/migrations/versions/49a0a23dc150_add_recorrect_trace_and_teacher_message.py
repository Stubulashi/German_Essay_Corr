"""add recorrect trace and teacher message

Revision ID: 49a0a23dc150
Revises: e42a079e0596
Create Date: 2026-09-18 00:45:42.003241

说明:
- 新增"重新批改"追溯列:次数 / 最近时间 / 上一次结果摘要(总分与错因数);
- 新增学生版"教师寄语"列(教师可编辑,为空时使用系统默认寄语);
- 透明加密类型(EncryptedText)与现有存储(TEXT)兼容,不产生类型变更;
  env.py 已加入自定义 compare_type 将加密类型视为与底层存储等价,避免噪声迁移。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '49a0a23dc150'
down_revision: Union[str, Sequence[str], None] = 'e42a079e0596'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema:重新批改追溯与教师寄语列(SQLite 批处理模式)"""
    with op.batch_alter_table('correction_tasks', schema=None) as batch_op:
        # NOT NULL 新列必须带 server_default,否则存量行无法升级
        batch_op.add_column(
            sa.Column('recorrect_count', sa.Integer(), nullable=False, server_default='0')
        )
        batch_op.add_column(
            sa.Column('last_recorrect_at', sa.DateTime(timezone=True), nullable=True)
        )
        # 透明加密列:底层存储为 TEXT(存储兼容)
        batch_op.add_column(sa.Column('prev_overall_score', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('prev_error_count', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('teacher_message', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema:移除本迁移新增列"""
    with op.batch_alter_table('correction_tasks', schema=None) as batch_op:
        batch_op.drop_column('teacher_message')
        batch_op.drop_column('prev_error_count')
        batch_op.drop_column('prev_overall_score')
        batch_op.drop_column('last_recorrect_at')
        batch_op.drop_column('recorrect_count')
