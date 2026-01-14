-- PostgreSQL初期化スクリプト
-- このスクリプトはPostgreSQLコンテナの初回起動時に自動実行されます

-- 拡張機能の有効化（必要に応じて）
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 接続確認用のログ出力
DO $$
BEGIN
    RAISE NOTICE 'Database initialization completed successfully';
END $$;
