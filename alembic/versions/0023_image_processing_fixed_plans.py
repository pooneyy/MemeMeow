"""为图片处理 Job 固定处理方式、阶段计划和结构化图片目标。"""

from alembic import op


revision = "0023_image_processing_fixed_plans"
down_revision = "0022_separate_image_display_names_and_content_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """只向前安装固定计划字段，并在无法归类活动历史时停止升级。"""
    op.execute(
        """
        ALTER TABLE image_processing_jobs
            ADD COLUMN IF NOT EXISTS processing_mode VARCHAR(16);
        UPDATE image_processing_jobs
           SET processing_mode = 'normal'
         WHERE processing_mode IS NULL;
        ALTER TABLE image_processing_jobs
            ALTER COLUMN processing_mode SET DEFAULT 'normal',
            ALTER COLUMN processing_mode SET NOT NULL;

        ALTER TABLE image_processing_stages
            ADD COLUMN IF NOT EXISTS planned BOOLEAN,
            ADD COLUMN IF NOT EXISTS skip_reason VARCHAR(32);
        UPDATE image_processing_stages
           SET planned = CASE WHEN status = 'skipped' THEN FALSE ELSE TRUE END,
               skip_reason = CASE WHEN status = 'skipped' THEN 'disabled' ELSE NULL END
         WHERE planned IS NULL;
        ALTER TABLE image_processing_stages
            ALTER COLUMN planned SET DEFAULT TRUE,
            ALTER COLUMN planned SET NOT NULL;

        ALTER TABLE tasks
            ADD COLUMN IF NOT EXISTS target_meme_id UUID,
            ADD COLUMN IF NOT EXISTS target_image_sha256 VARCHAR(64);

        UPDATE tasks AS task
           SET target_meme_id = COALESCE(task.target_meme_id, job.meme_id),
               target_image_sha256 = COALESCE(task.target_image_sha256, job.image_sha256)
         FROM image_processing_jobs AS job
         WHERE task.scope_id = job.scope_id
           AND task.processing_job_id = job.id
           AND (task.target_meme_id IS NULL OR task.target_image_sha256 IS NULL);

        UPDATE tasks AS task
           SET target_meme_id = COALESCE(task.target_meme_id, (task.payload ->> 'meme_id')::uuid),
               target_image_sha256 = COALESCE(task.target_image_sha256, lower(task.payload ->> 'image_sha256'))
         WHERE (task.target_meme_id IS NULL OR task.target_image_sha256 IS NULL)
           AND task.task_type IN ('visual_embedding_generation','meme_context_generation','image_auto_rename','text_embedding_generation')
           AND (task.payload ->> 'meme_id') ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
           AND (task.payload ->> 'image_sha256') ~* '^[0-9a-f]{64}$'
           AND EXISTS (
               SELECT 1 FROM memes AS meme
                WHERE meme.scope_id = task.scope_id
                  AND meme.id = (task.payload ->> 'meme_id')::uuid
                  AND lower(meme.sha256) = lower(task.payload ->> 'image_sha256')
           );

        DO $$
        DECLARE unresolved INTEGER;
        BEGIN
            SELECT count(*) INTO unresolved
              FROM tasks
             WHERE task_type IN ('visual_embedding_generation','meme_context_generation','image_auto_rename','text_embedding_generation')
               AND status IN ('queued','running')
               AND (target_meme_id IS NULL OR target_image_sha256 IS NULL);
            IF unresolved > 0 THEN
                RAISE EXCEPTION 'image_processing_history_unresolved: % active image tasks cannot be bound to a target', unresolved;
            END IF;
        END $$;

        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_tasks_target_meme') THEN
                ALTER TABLE tasks ADD CONSTRAINT fk_tasks_target_meme
                    FOREIGN KEY (scope_id, target_meme_id)
                    REFERENCES memes(scope_id, id) ON DELETE CASCADE;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_task_target_image_sha256') THEN
                ALTER TABLE tasks ADD CONSTRAINT ck_task_target_image_sha256
                    CHECK (target_image_sha256 IS NULL OR length(target_image_sha256) = 64);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_image_processing_mode') THEN
                ALTER TABLE image_processing_jobs ADD CONSTRAINT ck_image_processing_mode
                    CHECK (processing_mode IN ('normal','full_retry','repair'));
            END IF;
            ALTER TABLE image_processing_stages DROP CONSTRAINT IF EXISTS ck_image_processing_stage_plan;
            ALTER TABLE image_processing_stages DROP CONSTRAINT IF EXISTS ck_image_processing_stage_plan_status;
            ALTER TABLE image_processing_stages ADD CONSTRAINT ck_image_processing_stage_plan
                CHECK ((planned AND skip_reason IS NULL) OR (NOT planned AND skip_reason IN ('already_ready','disabled')));
            ALTER TABLE image_processing_stages ADD CONSTRAINT ck_image_processing_stage_plan_status
                CHECK ((planned AND status <> 'skipped') OR (NOT planned AND status = 'skipped'));
        END $$;
        CREATE INDEX IF NOT EXISTS ix_tasks_image_target_active
            ON tasks(scope_id, target_meme_id, target_image_sha256, status, created_at);
        UPDATE installation_state
           SET schema_revision = '0023_image_processing_fixed_plans'
         WHERE key = 'local';
        """
    )


def downgrade() -> None:
    """项目 schema 只允许前向升级，避免恢复可变的图片处理语义。"""
    raise RuntimeError("本项目 schema 只允许前向升级")
