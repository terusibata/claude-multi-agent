"""
AWS Bedrock権限チェックユーティリティ
Claude Agent SDKで必要な権限を確認する
"""
import logging
import os
from typing import Dict, List

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

logger = logging.getLogger(__name__)


class BedrockPermissionChecker:
    """Bedrock権限チェッカークラス"""

    REQUIRED_PERMISSIONS = [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
        "bedrock:ListInferenceProfiles",
    ]

    def __init__(self):
        """初期化"""
        self.region = os.environ.get("AWS_REGION", "us-west-2")
        self.bedrock_client = None

    def check_credentials(self) -> Dict[str, any]:
        """
        AWS認証情報をチェック

        Returns:
            チェック結果の辞書
        """
        result = {
            "has_credentials": False,
            "region": self.region,
            "error": None,
        }

        try:
            # boto3セッションを作成
            session = boto3.Session(
                aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
                region_name=self.region,
            )

            # 認証情報が取得できるか確認
            credentials = session.get_credentials()
            if credentials:
                result["has_credentials"] = True
                result["access_key_id"] = credentials.access_key[:10] + "..."
                logger.info(f"AWS認証情報が見つかりました: {result['access_key_id']}")
            else:
                result["error"] = "AWS認証情報が見つかりません"
                logger.error(result["error"])

        except NoCredentialsError as e:
            result["error"] = f"AWS認証情報エラー: {str(e)}"
            logger.error(result["error"])
        except Exception as e:
            result["error"] = f"予期しないエラー: {str(e)}"
            logger.error(result["error"], exc_info=True)

        return result

    def check_bedrock_permissions(self, model_id: str) -> Dict[str, any]:
        """
        Bedrock権限をチェック

        Args:
            model_id: Bedrockモデ��ID

        Returns:
            チェック結果の辞書
        """
        result = {
            "permissions_ok": False,
            "checks": {},
            "recommendations": [],
        }

        try:
            # Bedrockクライアントを作成
            self.bedrock_client = boto3.client(
                "bedrock-runtime",
                region_name=self.region,
                aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
            )

            # 1. InferenceProfile一覧取得テスト
            try:
                bedrock_control = boto3.client(
                    "bedrock",
                    region_name=self.region,
                    aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
                    aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
                )
                bedrock_control.list_inference_profiles()
                result["checks"]["list_inference_profiles"] = True
                logger.info("✓ bedrock:ListInferenceProfiles 権限OK")
            except ClientError as e:
                error_code = e.response["Error"]["Code"]
                result["checks"]["list_inference_profiles"] = False
                result["recommendations"].append(
                    "bedrock:ListInferenceProfiles 権限が不足しています"
                )
                logger.error(f"✗ bedrock:ListInferenceProfiles 権限エラー: {error_code}")

            # 2. モデル呼び出しテスト（空のリクエストで権限のみチェック）
            try:
                # 実際には呼び出さず、権限エラーをチェック
                self.bedrock_client.invoke_model(
                    modelId=model_id,
                    body='{"anthropic_version":"bedrock-2023-05-31","max_tokens":1,"messages":[{"role":"user","content":"test"}]}',
                )
                result["checks"]["invoke_model"] = True
                logger.info("✓ bedrock:InvokeModel 権限OK")
            except ClientError as e:
                error_code = e.response["Error"]["Code"]
                if error_code == "AccessDeniedException":
                    result["checks"]["invoke_model"] = False
                    result["recommendations"].append(
                        "bedrock:InvokeModel 権限が不足しています"
                    )
                    logger.error(f"✗ bedrock:InvokeModel 権限エラー: {error_code}")
                else:
                    # ValidationExceptionなどの場合は権限はOK
                    result["checks"]["invoke_model"] = True
                    logger.info("✓ bedrock:InvokeModel 権限OK（検証エラーは無視）")

            # すべての必須チェックがOKか確認
            result["permissions_ok"] = all(result["checks"].values())

            if not result["permissions_ok"]:
                result["recommendations"].append(
                    "\nIAMポリシーに以下の権限を追加してください："
                )
                result["recommendations"].append(
                    "- AmazonBedrockFullAccess (マネージドポリシー)"
                )
                result["recommendations"].append("または、カスタムポリシー:")
                result["recommendations"].append(
                    '  {"Effect": "Allow", "Action": ['
                    '"bedrock:InvokeModel", '
                    '"bedrock:InvokeModelWithResponseStream", '
                    '"bedrock:ListInferenceProfiles"'
                    '], "Resource": "*"}'
                )

        except NoCredentialsError as e:
            result["error"] = f"AWS認証情報エラー: {str(e)}"
            result["recommendations"].append(
                "AWS_ACCESS_KEY_IDとAWS_SECRET_ACCESS_KEYを設定してください"
            )
            logger.error(result["error"])
        except Exception as e:
            result["error"] = f"予期しないエラー: {str(e)}"
            logger.error(result["error"], exc_info=True)

        return result


def check_bedrock_setup(model_id: str) -> Dict[str, any]:
    """
    Bedrock環境をチェック

    Args:
        model_id: Bedrockモデル ID

    Returns:
        チェック結果の辞書
    """
    checker = BedrockPermissionChecker()

    # 認証情報チェック
    creds_result = checker.check_credentials()
    if not creds_result["has_credentials"]:
        return {
            "status": "error",
            "message": "AWS認証情報が設定されていません",
            "details": creds_result,
        }

    # 権限チェック
    perms_result = checker.check_bedrock_permissions(model_id)
    if not perms_result["permissions_ok"]:
        return {
            "status": "error",
            "message": "Bedrock権限が不足しています",
            "details": perms_result,
        }

    return {
        "status": "ok",
        "message": "Bedrock環境は正常です",
        "details": {"credentials": creds_result, "permissions": perms_result},
    }
