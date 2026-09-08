"""为 Meme 增加独立展示名称并收束图片内容寻址身份。"""

from alembic import op


revision = "0022_separate_image_display_names_and_content_identity"
down_revision = "0021_search_metadata_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """迁移展示字段并在安装新身份约束前拒绝历史脏记录。

    该版本改变物理身份语义，不能把旧文件名记录静默留在新 schema 中；先完成可
    诊断的全表检查，再安装有效约束和内容唯一键，确保模型声明与 PostgreSQL 事实一致。
    """
    op.execute(
        """
        ALTER TABLE memes
            ADD COLUMN IF NOT EXISTS display_name VARCHAR(255);
        UPDATE memes
           SET display_name = 'image'
         WHERE display_name IS NULL;
        ALTER TABLE memes
            ALTER COLUMN metadata_schema_version SET DEFAULT 1;
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1
                  FROM memes
                 WHERE display_name IS NULL
                    OR display_name = ''
                    OR char_length(display_name) > 255
                    OR display_name <> btrim(display_name, ' .')
                    OR position('/' in display_name) > 0
                    OR position(chr(92) in display_name) > 0
                    OR display_name ~ '[[:cntrl:]]'
                    OR display_name IN ('.', '..', '.staging', '.quarantine')
                    OR lower(display_name) LIKE '%.png'
                    OR lower(display_name) LIKE '%.jpg'
                    OR lower(display_name) LIKE '%.jpeg'
                    OR lower(display_name) LIKE '%.gif'
                    OR extension <> lower(extension)
                    OR extension NOT IN ('.png','.jpg','.jpeg','.gif')
                    OR sha256 !~ '^[0-9a-f]{64}$'
                    OR storage_key <> sha256 || extension
            ) THEN
                RAISE EXCEPTION 'image_identity_preflight_failed: historical Meme identity is not content addressed';
            END IF;
            IF EXISTS (
                SELECT 1
                  FROM memes
                 GROUP BY scope_id, sha256, extension
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION 'image_identity_preflight_failed: duplicate scope content identity';
            END IF;
            IF EXISTS (
                SELECT 1
                  FROM storage_operations
                 WHERE operation_type NOT IN ('upload','rename','delete')
            ) THEN
                RAISE EXCEPTION 'image_identity_preflight_failed: invalid storage operation type';
            END IF;
        END $$;
        ALTER TABLE memes
            ALTER COLUMN display_name SET NOT NULL;
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_memes_extension_normalized') THEN
                ALTER TABLE memes ADD CONSTRAINT ck_memes_extension_normalized
                    CHECK (extension = lower(extension) AND extension IN ('.png','.jpg','.jpeg','.gif'));
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_memes_sha256_hex') THEN
                ALTER TABLE memes ADD CONSTRAINT ck_memes_sha256_hex CHECK (sha256 ~ '^[0-9a-f]{64}$');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_memes_storage_key_content_addressed') THEN
                ALTER TABLE memes ADD CONSTRAINT ck_memes_storage_key_content_addressed CHECK (storage_key = sha256 || extension);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_memes_display_name_safe') THEN
                ALTER TABLE memes ADD CONSTRAINT ck_memes_display_name_safe CHECK (
                    display_name IS NOT NULL
                    AND display_name <> '' AND char_length(display_name) <= 255
                    AND display_name = btrim(display_name, ' .')
                    AND position('/' in display_name) = 0
                    AND position(chr(92) in display_name) = 0
                    AND display_name !~ '[[:cntrl:]]'
                    AND display_name NOT IN ('.', '..', '.staging', '.quarantine')
                    AND lower(display_name) NOT LIKE '%.png'
                    AND lower(display_name) NOT LIKE '%.jpg'
                    AND lower(display_name) NOT LIKE '%.jpeg'
                    AND lower(display_name) NOT LIKE '%.gif'
                );
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_memes_scope_content') THEN
                ALTER TABLE memes
                    ADD CONSTRAINT uq_memes_scope_content UNIQUE (scope_id, sha256, extension);
            END IF;
            IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_storage_operation_type') THEN
                ALTER TABLE storage_operations DROP CONSTRAINT ck_storage_operation_type;
            END IF;
            ALTER TABLE storage_operations ADD CONSTRAINT ck_storage_operation_type
                CHECK (operation_type IN ('upload','rename','delete'));
        END $$;
        CREATE INDEX IF NOT EXISTS ix_memes_scope_display_name
            ON memes (scope_id, display_name, id);
        UPDATE installation_state
           SET schema_revision = '0022_separate_image_display_names_and_content_identity'
         WHERE key = 'local';
        """
    )


def downgrade() -> None:
    """项目 schema 只允许前向升级，避免恢复可变物理文件名语义。"""
    raise RuntimeError("本项目 schema 只允许前向升级")
