"""
モデルサービスの単体テスト
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Model, Tenant, UsageLog
from app.services.model_service import ModelInUseError, ModelService


class TestModelServiceCRUD:
    """モデルサービスCRUD操作のテスト"""

    @pytest.mark.unit
    async def test_create_model(self, db_session: AsyncSession):
        """モデル作成"""
        service = ModelService(db_session)

        from app.schemas.model import ModelCreate

        model_data = ModelCreate(
            model_id="unit-test-model",
            display_name="Unit Test Model",
            bedrock_model_id="us.anthropic.claude-test-v1:0",
            input_token_price="1.0",
            output_token_price="5.0",
        )

        model = await service.create(model_data)

        assert model.model_id == "unit-test-model"
        assert model.display_name == "Unit Test Model"
        assert model.status == "active"

    @pytest.mark.unit
    async def test_get_by_id(self, db_session: AsyncSession):
        """ID指定でモデル取得"""
        service = ModelService(db_session)

        from app.schemas.model import ModelCreate

        # モデルを作成
        model_data = ModelCreate(
            model_id="get-test-model",
            display_name="Get Test Model",
            bedrock_model_id="us.anthropic.claude-test-v1:0",
            input_token_price="1.0",
            output_token_price="5.0",
        )
        await service.create(model_data)

        # 取得
        model = await service.get_by_id("get-test-model")

        assert model is not None
        assert model.model_id == "get-test-model"

    @pytest.mark.unit
    async def test_get_by_id_not_found(self, db_session: AsyncSession):
        """存在しないモデルはNoneを返す"""
        service = ModelService(db_session)

        model = await service.get_by_id("non-existent-model")

        assert model is None

    @pytest.mark.unit
    async def test_get_all(self, db_session: AsyncSession):
        """全モデル取得"""
        service = ModelService(db_session)

        from app.schemas.model import ModelCreate

        # 複数モデルを作成
        for i in range(3):
            model_data = ModelCreate(
                model_id=f"list-test-model-{i}",
                display_name=f"List Test Model {i}",
                bedrock_model_id=f"us.anthropic.claude-test-{i}:0",
                input_token_price="1.0",
                output_token_price="5.0",
            )
            await service.create(model_data)

        models = await service.get_all()

        assert len(models) >= 3

    @pytest.mark.unit
    async def test_get_all_filter_by_status(self, db_session: AsyncSession):
        """ステータスでフィルタリング"""
        service = ModelService(db_session)

        from app.schemas.model import ModelCreate

        # activeとdeprecatedのモデルを作成
        await service.create(
            ModelCreate(
                model_id="active-model",
                display_name="Active Model",
                bedrock_model_id="us.anthropic.claude-active:0",
                input_token_price="1.0",
                output_token_price="5.0",
            )
        )
        model = await service.create(
            ModelCreate(
                model_id="deprecated-model",
                display_name="Deprecated Model",
                bedrock_model_id="us.anthropic.claude-deprecated:0",
                input_token_price="1.0",
                output_token_price="5.0",
            )
        )
        await service.update_status("deprecated-model", "deprecated")

        # activeのみ取得
        active_models = await service.get_all(status="active")
        assert all(m.status == "active" for m in active_models)

        # deprecatedのみ取得
        deprecated_models = await service.get_all(status="deprecated")
        assert all(m.status == "deprecated" for m in deprecated_models)


class TestModelServiceDelete:
    """モデル削除（紐づきチェック）のテスト"""

    @pytest.mark.unit
    async def test_delete_success(self, db_session: AsyncSession):
        """紐づきのないモデルは削除可能"""
        service = ModelService(db_session)

        from app.schemas.model import ModelCreate

        # モデル作成
        await service.create(
            ModelCreate(
                model_id="delete-success-model",
                display_name="Delete Success Model",
                bedrock_model_id="us.anthropic.claude-delete:0",
                input_token_price="1.0",
                output_token_price="5.0",
            )
        )

        # 削除
        result = await service.delete("delete-success-model")

        assert result is True

        # 削除確認
        model = await service.get_by_id("delete-success-model")
        assert model is None

    @pytest.mark.unit
    async def test_delete_not_found(self, db_session: AsyncSession):
        """存在しないモデルはFalseを返す"""
        service = ModelService(db_session)

        result = await service.delete("non-existent-model")

        assert result is False

    @pytest.mark.unit
    async def test_delete_in_use_by_tenant(self, db_session: AsyncSession):
        """テナントで使用中のモデルは削除不可"""
        service = ModelService(db_session)

        from app.schemas.model import ModelCreate

        # モデル作成
        await service.create(
            ModelCreate(
                model_id="tenant-used-model",
                display_name="Tenant Used Model",
                bedrock_model_id="us.anthropic.claude-tenant:0",
                input_token_price="1.0",
                output_token_price="5.0",
            )
        )

        # テナント作成（モデルを参照）
        tenant = Tenant(
            tenant_id="test-tenant-for-model",
            model_id="tenant-used-model",
        )
        db_session.add(tenant)
        await db_session.flush()

        # 削除試行
        with pytest.raises(ModelInUseError) as exc_info:
            await service.delete("tenant-used-model")

        assert exc_info.value.usage_details["tenants"] >= 1

    @pytest.mark.unit
    async def test_delete_in_use_by_conversation(self, db_session: AsyncSession):
        """会話で使用中のモデルは削除不可"""
        service = ModelService(db_session)

        from app.schemas.model import ModelCreate

        # モデル作成
        await service.create(
            ModelCreate(
                model_id="conversation-used-model",
                display_name="Conversation Used Model",
                bedrock_model_id="us.anthropic.claude-conv:0",
                input_token_price="1.0",
                output_token_price="5.0",
            )
        )

        # テナント作成（会話の前提条件）
        tenant = Tenant(tenant_id="test-tenant-for-conv")
        db_session.add(tenant)
        await db_session.flush()

        # 会話作成（モデルを参照）
        conversation = Conversation(
            tenant_id="test-tenant-for-conv",
            user_id="test-user",
            model_id="conversation-used-model",
        )
        db_session.add(conversation)
        await db_session.flush()

        # 削除試行
        with pytest.raises(ModelInUseError) as exc_info:
            await service.delete("conversation-used-model")

        assert exc_info.value.usage_details["conversations"] >= 1

    @pytest.mark.unit
    async def test_check_model_usage(self, db_session: AsyncSession):
        """使用状況チェック"""
        service = ModelService(db_session)

        from app.schemas.model import ModelCreate

        # モデル作成
        await service.create(
            ModelCreate(
                model_id="usage-check-model",
                display_name="Usage Check Model",
                bedrock_model_id="us.anthropic.claude-usage:0",
                input_token_price="1.0",
                output_token_price="5.0",
            )
        )

        # 紐づきなしの状態
        usage = await service._check_model_usage("usage-check-model")
        assert usage["tenants"] == 0
        assert usage["conversations"] == 0
        assert usage["usage_logs"] == 0

        # テナント追加
        tenant = Tenant(tenant_id="usage-test-tenant", model_id="usage-check-model")
        db_session.add(tenant)
        await db_session.flush()

        usage = await service._check_model_usage("usage-check-model")
        assert usage["tenants"] == 1
