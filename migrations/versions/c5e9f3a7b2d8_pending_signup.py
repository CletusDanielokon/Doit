"""Add pending sign-ups (confirm email before the account is created)

Revision ID: c5e9f3a7b2d8
Revises: b4d8e2f6a1c7
Create Date: 2026-10-24 12:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = 'c5e9f3a7b2d8'
down_revision = 'b4d8e2f6a1c7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'pending_signup',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=254), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=True),
        sa.Column('username', sa.String(length=80), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('sent_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_pending_signup_email', 'pending_signup', ['email'], unique=True)
    op.create_index('ix_pending_signup_token_hash', 'pending_signup', ['token_hash'])


def downgrade():
    op.drop_index('ix_pending_signup_token_hash', table_name='pending_signup')
    op.drop_index('ix_pending_signup_email', table_name='pending_signup')
    op.drop_table('pending_signup')
