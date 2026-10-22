"""Add weekly timetable and diary notes

Revision ID: 9c2e5a7d41b3
Revises: 7b3c1d2e9a10
Create Date: 2026-10-14 12:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '9c2e5a7d41b3'
down_revision = '7b3c1d2e9a10'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'timetable_entry',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=120), nullable=False),
        sa.Column('day', sa.Integer(), nullable=False),
        sa.Column('start_min', sa.Integer(), nullable=False),
        sa.Column('end_min', sa.Integer(), nullable=False),
        sa.Column('location', sa.String(length=120), nullable=True),
        sa.Column('color', sa.String(length=12), nullable=True),
        sa.Column('alerts', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_timetable_entry_user_id', 'timetable_entry', ['user_id'])

    op.create_table(
        'note',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('entry_date', sa.Date(), nullable=False),
        sa.Column('title', sa.String(length=150), nullable=True),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('mood', sa.String(length=12), nullable=True),
        sa.Column('pinned', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_note_user_id', 'note', ['user_id'])


def downgrade():
    op.drop_index('ix_note_user_id', table_name='note')
    op.drop_table('note')
    op.drop_index('ix_timetable_entry_user_id', table_name='timetable_entry')
    op.drop_table('timetable_entry')
