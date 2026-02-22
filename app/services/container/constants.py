"""
コンテナ管理共通定数
Docker / ECS 両モードで共有される Redis キーおよび TTL 定数
"""

# Redis キープレフィックス
REDIS_KEY_CONTAINER = "workspace:container"  # workspace:container:{conversation_id}
REDIS_KEY_CONTAINER_REVERSE = "workspace:container_reverse"  # workspace:container_reverse:{container_id} → conversation_id
REDIS_KEY_WARM_POOL = "workspace:warm_pool"  # List
REDIS_KEY_WARM_POOL_INFO = "workspace:warm_pool_info"  # workspace:warm_pool_info:{container_id}
REDIS_KEY_ECS_TASK = "workspace:ecs_task"  # workspace:ecs_task:{container_id} → task_arn (ECSモード専用)

# コンテナRedis TTL
CONTAINER_TTL_SECONDS = 3600  # 1時間
WARM_POOL_TTL_SECONDS = 1800  # 30分
