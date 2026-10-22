"""Add notes, subtasks, due_has_time; widen password column

Revision ID: 7b3c1d2e9a10
Revises: 25e1af44dc91
Create Date: 2026-10-12 12:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '7b3c1d2e9a10'
down_revision = '25e1af44dc91'
branch_labels = None
depends_on = None


def upgrade():
    # Password hashes (scrypt) are ~160 chars; 80 breaks on PostgreSQL.
    with op.batch_alter_table('user') as batch:
        batch.alter_column('password', existing_type=sa.String(length=80),
                           type_=sa.String(length=255), existing_nullable=False)

    with op.batch_alter_table('task') as batch:
        batch.add_column(sa.Column('notes', sa.Text(), nullable=True))
        batch.add_column(sa.Column('due_has_time', sa.Boolean(), nullable=False,
                                   server_default=sa.false()))
        batch.create_index('ix_task_user_id', ['user_id'])

    # Existing due dates that carry a real time-of-day keep showing it.
    task = sa.table('task', sa.column('id', sa.Integer), sa.column('due_date', sa.DateTime),
                    sa.column('due_has_time', sa.Boolean))
    conn = op.get_bind()
    for row in conn.execute(sa.select(task.c.id, task.c.due_date)
                            .where(task.c.due_date.isnot(None))).fetchall():
        due = row.due_date
        if due and (due.hour or due.minute):
            conn.execute(task.update().where(task.c.id == row.id).values(due_has_time=True))

    op.create_table(
        'subtask',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('done', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['task_id'], ['task.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_subtask_task_id', 'subtask', ['task_id'])


def downgrade():
    op.drop_index('ix_subtask_task_id', table_name='subtask')
    op.drop_table('subtask')
    with op.batch_alter_table('task') as batch:
        batch.drop_index('ix_task_user_id')
        batch.drop_column('due_has_time')
        batch.drop_column('notes')
    with op.batch_alter_table('user') as batch:
        batch.alter_column('password', existing_type=sa.String(length=255),
                           type_=sa.String(length=80), existing_nullable=False)
