"""Add database-backed email links (confirm email, reset password)

Revision ID: d7a1c4e9b3f2
Revises: c5e9f3a7b2d8
Create Date: 2026-11-02 12:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = 'd7a1c4e9b3f2'
down_revision = 'c5e9f3a7b2d8'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'link_token',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=12), nullable=False),
        sa.Column('data', sa.String(length=254), nullable=True),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_link_token_user_id', 'link_token', ['user_id'])
    op.create_index('ix_link_token_token_hash', 'link_token', ['token_hash'])


def downgrade():
    op.drop_index('ix_link_token_token_hash', table_name='link_token')
    op.drop_index('ix_link_token_user_id', table_name='link_token')
    op.drop_table('link_token')
