"""Add email, reminder preferences, timezone and email log

Revision ID: b4d8e2f6a1c7
Revises: 9c2e5a7d41b3
Create Date: 2026-10-21 12:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = 'b4d8e2f6a1c7'
down_revision = '9c2e5a7d41b3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user') as batch:
        batch.add_column(sa.Column('email', sa.String(length=254), nullable=True))
        batch.add_column(sa.Column('email_verified', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column('email_reminders', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column('email_digest', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column('tz', sa.String(length=64), nullable=True))
        batch.create_index('ix_user_email', ['email'], unique=True)

    op.create_table(
        'email_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=160), nullable=False),
        sa.Column('sent_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'key', name='uq_email_log_user_key'),
    )
    op.create_index('ix_email_log_user_id', 'email_log', ['user_id'])


def downgrade():
    op.drop_index('ix_email_log_user_id', table_name='email_log')
    op.drop_table('email_log')
    with op.batch_alter_table('user') as batch:
        batch.drop_index('ix_user_email')
        for col in ('tz', 'email_digest', 'email_reminders', 'email_verified', 'email'):
            batch.drop_column(col)
