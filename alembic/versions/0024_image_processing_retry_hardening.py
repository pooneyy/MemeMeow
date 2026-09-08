"""加严图片处理活动任务的目标绑定检查。"""

from alembic import op


revision = "0024_image_processing_retry_hardening"
down_revision = "0023_image_processing_fixed_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """拒绝无法证明仍指向当前图片的活动历史任务。"""
    op.execute(
        """
        DO $$
        DECLARE unresolved INTEGER;
        BEGIN
            SELECT count(*) INTO unresolved
              FROM tasks AS task
              LEFT JOIN memes AS meme
                ON meme.scope_id = task.scope_id
               AND meme.id = task.target_meme_id
             WHERE task.task_type IN ('visual_embedding_generation','meme_context_generation','image_auto_rename','text_embedding_generation')
               AND task.status IN ('queued','running')
               AND (
                    task.target_meme_id IS NULL
                    OR task.target_image_sha256 IS NULL
                    OR meme.id IS NULL
                    OR lower(meme.sha256) <> lower(task.target_image_sha256)
               );
            IF unresolved > 0 THEN
                RAISE EXCEPTION 'image_processing_history_unresolved: % active image tasks cannot be bound to a current target', unresolved;
            END IF;
        END $$;
        UPDATE installation_state
           SET schema_revision = '0024_image_processing_retry_hardening'
         WHERE key = 'local';
        """
    )


def downgrade() -> None:
    """数据库 schema 只允许前向升级。"""
    raise RuntimeError("本项目 schema 只允许前向升级")
