"""
AWS SigV4 署名ユーティリティ
Bedrock API呼び出しにAWS認証情報を注入する
"""
from dataclasses import dataclass

import botocore.auth
import botocore.credentials
from botocore.awsrequest import AWSRequest


@dataclass
class AWSCredentials:
    """AWS認証情報"""

    access_key_id: str
    secret_access_key: str
    session_token: str | None = None
    region: str = "us-west-2"


def sign_request(
    credentials: AWSCredentials,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes,
    service: str = "bedrock",
) -> dict[str, str]:
    """
    リクエストにSigV4署名を付与する

    Args:
        credentials: AWS認証情報
        method: HTTPメソッド
        url: リクエストURL
        headers: 既存ヘッダー
        body: リクエストボディ
        service: AWSサービス名

    Returns:
        署名済みヘッダー辞書
    """
    creds = botocore.credentials.Credentials(
        access_key=credentials.access_key_id,
        secret_key=credentials.secret_access_key,
        token=credentials.session_token,
    )

    aws_request = AWSRequest(method=method, url=url, headers=headers, data=body)
    signer = botocore.auth.SigV4Auth(creds, service, credentials.region)
    signer.add_auth(aws_request)

    return dict(aws_request.headers)
