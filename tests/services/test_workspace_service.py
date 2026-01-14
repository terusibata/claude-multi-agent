"""
ワークスペースサービスの単体テスト（S3モック使用）
"""
import os

import boto3
import pytest
from moto import mock_aws
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Model, Tenant


@pytest.fixture
def mock_s3(aws_credentials, s3_bucket_name):
    """S3モックを設定"""
    with mock_aws():
        # S3クライアント作成
        s3_client = boto3.client("s3", region_name="us-east-1")

        # バケット作成
        s3_client.create_bucket(Bucket=s3_bucket_name)

        # 環境変数設定
        os.environ["S3_BUCKET_NAME"] = s3_bucket_name
        os.environ["S3_WORKSPACE_PREFIX"] = "workspaces/"

        yield s3_client


class TestWorkspaceServiceBasic:
    """ワークスペースサービス基本機能のテスト"""

    @pytest.mark.unit
    async def test_workspace_path_generation(
        self, db_session: AsyncSession, mock_s3, s3_bucket_name
    ):
        """ワークスペースパス生成"""
        from app.services.workspace_service import WorkspaceService

        service = WorkspaceService(db_session)

        # ローカルパス
        local_path = service.get_workspace_local_path("test-conversation-id")
        assert "test-conversation-id" in local_path

    @pytest.mark.unit
    async def test_s3_key_generation(
        self, db_session: AsyncSession, mock_s3, s3_bucket_name
    ):
        """S3キー生成"""
        from app.services.workspace_service import WorkspaceService

        service = WorkspaceService(db_session)

        s3_key = service._get_s3_key("tenant-001", "conv-001", "uploads/test.txt")
        assert s3_key == "workspaces/tenant-001/conv-001/uploads/test.txt"


class TestWorkspaceFileOperations:
    """ワークスペースファイル操作のテスト"""

    @pytest.fixture
    async def setup_conversation(self, db_session: AsyncSession):
        """テスト用会話を作成"""
        from app.schemas.model import ModelCreate
        from app.services.model_service import ModelService

        # モデル作成
        model_service = ModelService(db_session)
        await model_service.create(
            ModelCreate(
                model_id="workspace-test-model",
                display_name="Workspace Test Model",
                bedrock_model_id="us.anthropic.claude-workspace:0",
                input_token_price="1.0",
                output_token_price="5.0",
            )
        )

        # テナント作成
        tenant = Tenant(
            tenant_id="workspace-test-tenant",
            model_id="workspace-test-model",
        )
        db_session.add(tenant)
        await db_session.flush()

        # 会話作成（ワークスペース有効）
        conversation = Conversation(
            tenant_id="workspace-test-tenant",
            user_id="workspace-test-user",
            model_id="workspace-test-model",
            enable_workspace=True,
        )
        db_session.add(conversation)
        await db_session.flush()

        return {
            "tenant_id": tenant.tenant_id,
            "conversation_id": conversation.conversation_id,
        }

    @pytest.mark.unit
    async def test_upload_file_to_s3(
        self, db_session: AsyncSession, mock_s3, s3_bucket_name, setup_conversation
    ):
        """S3へのファイルアップロード"""
        from app.services.workspace_service import WorkspaceService

        service = WorkspaceService(db_session)

        tenant_id = setup_conversation["tenant_id"]
        conversation_id = setup_conversation["conversation_id"]

        # ファイルアップロード
        file_content = b"Hello, World!"
        file_info = await service.upload_file(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            file_path="uploads/test.txt",
            original_name="test.txt",
            content=file_content,
            content_type="text/plain",
        )

        assert file_info is not None
        assert file_info.file_path == "uploads/test.txt"
        assert file_info.original_name == "test.txt"
        assert file_info.file_size == len(file_content)
        assert file_info.source == "user_upload"

    @pytest.mark.unit
    async def test_download_file_from_s3(
        self, db_session: AsyncSession, mock_s3, s3_bucket_name, setup_conversation
    ):
        """S3からのファイルダウンロード"""
        from app.services.workspace_service import WorkspaceService

        service = WorkspaceService(db_session)

        tenant_id = setup_conversation["tenant_id"]
        conversation_id = setup_conversation["conversation_id"]

        # ファイルアップロード
        original_content = b"Download test content"
        await service.upload_file(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            file_path="uploads/download-test.txt",
            original_name="download-test.txt",
            content=original_content,
            content_type="text/plain",
        )

        # ダウンロード
        downloaded_content, content_type = await service.download_file(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            file_path="uploads/download-test.txt",
        )

        assert downloaded_content == original_content
        assert content_type == "text/plain"

    @pytest.mark.unit
    async def test_list_files(
        self, db_session: AsyncSession, mock_s3, s3_bucket_name, setup_conversation
    ):
        """ファイル一覧取得"""
        from app.services.workspace_service import WorkspaceService

        service = WorkspaceService(db_session)

        tenant_id = setup_conversation["tenant_id"]
        conversation_id = setup_conversation["conversation_id"]

        # 複数ファイルアップロード
        for i in range(3):
            await service.upload_file(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                file_path=f"uploads/file{i}.txt",
                original_name=f"file{i}.txt",
                content=f"Content {i}".encode(),
                content_type="text/plain",
            )

        # 一覧取得
        file_list = await service.list_files(tenant_id, conversation_id)

        assert file_list.total_count == 3
        assert len(file_list.files) == 3

    @pytest.mark.unit
    async def test_register_ai_file(
        self, db_session: AsyncSession, mock_s3, s3_bucket_name, setup_conversation
    ):
        """AIファイル登録"""
        from app.services.workspace_service import WorkspaceService

        service = WorkspaceService(db_session)

        tenant_id = setup_conversation["tenant_id"]
        conversation_id = setup_conversation["conversation_id"]

        # AI作成ファイルをS3に直接配置
        s3_key = f"workspaces/{tenant_id}/{conversation_id}/result.json"
        mock_s3.put_object(
            Bucket=s3_bucket_name,
            Key=s3_key,
            Body=b'{"result": "success"}',
            ContentType="application/json",
        )

        # AIファイル登録
        file_info = await service.register_ai_file(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            file_path="result.json",
            is_presented=True,
        )

        assert file_info is not None
        assert file_info.source == "ai_created"
        assert file_info.is_presented is True

    @pytest.mark.unit
    async def test_get_presented_files(
        self, db_session: AsyncSession, mock_s3, s3_bucket_name, setup_conversation
    ):
        """Presentedファイル一覧取得"""
        from app.services.workspace_service import WorkspaceService

        service = WorkspaceService(db_session)

        tenant_id = setup_conversation["tenant_id"]
        conversation_id = setup_conversation["conversation_id"]

        # 通常ファイルとPresentedファイルを登録
        await service.upload_file(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            file_path="uploads/normal.txt",
            original_name="normal.txt",
            content=b"Normal file",
            content_type="text/plain",
        )

        s3_key = f"workspaces/{tenant_id}/{conversation_id}/presented.json"
        mock_s3.put_object(
            Bucket=s3_bucket_name,
            Key=s3_key,
            Body=b'{"presented": true}',
            ContentType="application/json",
        )
        await service.register_ai_file(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            file_path="presented.json",
            is_presented=True,
        )

        # Presentedファイルのみ取得
        presented_files = await service.get_presented_files(tenant_id, conversation_id)

        assert len(presented_files) == 1
        assert presented_files[0].is_presented is True
        assert presented_files[0].file_path == "presented.json"
