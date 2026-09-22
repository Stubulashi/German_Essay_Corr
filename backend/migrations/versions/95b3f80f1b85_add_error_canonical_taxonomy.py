"""add error canonical taxonomy

新增 error_records.canonical_type 标准化考点分类列,并回填存量数据:
- 列以 server_default='OTHER' 加入,保证非空表可安全 ALTER(SQLite batch 模式);
- 对历史使用英文标签的记录做一次映射归一(与 error_taxonomy 的别名规则一致)。

Revision ID: 95b3f80f1b85
Revises: eaca32fbbc05
Create Date: 2026-09-17 22:07:16.452561

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '95b3f80f1b85'
down_revision: Union[str, Sequence[str], None] = 'eaca32fbbc05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. 新增标准化分类列(带 server_default,兼容存量行)
    with op.batch_alter_table('error_records', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'canonical_type',
                sa.String(length=64),
                nullable=False,
                server_default='OTHER',
            )
        )
        batch_op.create_index(
            batch_op.f('ix_error_records_canonical_type'), ['canonical_type'], unique=False
        )

    # 2. 存量数据回填:历史英文标签 -> 标准化键名(其余归 OTHER)
    op.execute(
        """
        UPDATE error_records SET canonical_type = CASE error_type
            WHEN 'Word Order' THEN 'VERB_POSITION'
            WHEN 'Satzstellung' THEN 'VERB_POSITION'
            WHEN 'Satzbau' THEN 'SENTENCE_STRUCTURE'
            WHEN 'Case Ending' THEN 'CASE_DECLENSION'
            WHEN 'Case' THEN 'CASE_DECLENSION'
            WHEN 'Vocabulary' THEN 'WORD_CHOICE'
            WHEN 'Word Choice' THEN 'WORD_CHOICE'
            WHEN 'Verb Form' THEN 'VERB_FORM'
            WHEN 'Preposition' THEN 'PREPOSITION'
            WHEN 'Tense' THEN 'TENSE'
            WHEN 'Spelling' THEN 'SPELLING'
            WHEN 'Punctuation' THEN 'PUNCTUATION'
            WHEN 'Article' THEN 'ARTICLE'
            WHEN 'Adjective Ending' THEN 'ADJECTIVE_ENDING'
            WHEN 'Capitalization' THEN 'CAPITALIZATION'
            ELSE 'OTHER' END
        WHERE canonical_type = 'OTHER'
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('error_records', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_error_records_canonical_type'))
        batch_op.drop_column('canonical_type')
