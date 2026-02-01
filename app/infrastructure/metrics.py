"""
Prometheusメトリクス収集

アプリケーションの監視メトリクスを収集・公開
"""
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class Counter:
    """カウンターメトリクス"""

    name: str
    description: str
    labels: list[str] = field(default_factory=list)
    _values: dict[tuple, float] = field(default_factory=dict)

    def inc(self, value: float = 1.0, **labels) -> None:
        """カウンターを増加"""
        key = tuple(labels.get(l, "") for l in self.labels)
        self._values[key] = self._values.get(key, 0) + value

    def get(self, **labels) -> float:
        """現在の値を取得"""
        key = tuple(labels.get(l, "") for l in self.labels)
        return self._values.get(key, 0)


@dataclass
class Gauge:
    """ゲージメトリクス"""

    name: str
    description: str
    labels: list[str] = field(default_factory=list)
    _values: dict[tuple, float] = field(default_factory=dict)

    def set(self, value: float, **labels) -> None:
        """値を設定"""
        key = tuple(labels.get(l, "") for l in self.labels)
        self._values[key] = value

    def inc(self, value: float = 1.0, **labels) -> None:
        """値を増加"""
        key = tuple(labels.get(l, "") for l in self.labels)
        self._values[key] = self._values.get(key, 0) + value

    def dec(self, value: float = 1.0, **labels) -> None:
        """値を減少"""
        key = tuple(labels.get(l, "") for l in self.labels)
        self._values[key] = self._values.get(key, 0) - value

    def get(self, **labels) -> float:
        """現在の値を取得"""
        key = tuple(labels.get(l, "") for l in self.labels)
        return self._values.get(key, 0)


@dataclass
class Histogram:
    """ヒストグラムメトリクス"""

    name: str
    description: str
    labels: list[str] = field(default_factory=list)
    buckets: list[float] = field(
        default_factory=lambda: [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
    )
    _counts: dict[tuple, dict[float, int]] = field(default_factory=dict)
    _sums: dict[tuple, float] = field(default_factory=dict)
    _totals: dict[tuple, int] = field(default_factory=dict)

    def observe(self, value: float, **labels) -> None:
        """観測値を記録"""
        key = tuple(labels.get(l, "") for l in self.labels)

        if key not in self._counts:
            self._counts[key] = {b: 0 for b in self.buckets}
            self._counts[key][float('inf')] = 0

        for bucket in self.buckets:
            if value <= bucket:
                self._counts[key][bucket] += 1
        self._counts[key][float('inf')] += 1

        self._sums[key] = self._sums.get(key, 0) + value
        self._totals[key] = self._totals.get(key, 0) + 1


class MetricsRegistry:
    """メトリクスレジストリ"""

    def __init__(self):
        self._metrics: dict[str, Counter | Gauge | Histogram] = {}

    def counter(
        self,
        name: str,
        description: str,
        labels: Optional[list[str]] = None,
    ) -> Counter:
        """カウンターを登録・取得"""
        if name not in self._metrics:
            self._metrics[name] = Counter(name, description, labels or [])
        return self._metrics[name]

    def gauge(
        self,
        name: str,
        description: str,
        labels: Optional[list[str]] = None,
    ) -> Gauge:
        """ゲージを登録・取得"""
        if name not in self._metrics:
            self._metrics[name] = Gauge(name, description, labels or [])
        return self._metrics[name]

    def histogram(
        self,
        name: str,
        description: str,
        labels: Optional[list[str]] = None,
        buckets: Optional[list[float]] = None,
    ) -> Histogram:
        """ヒストグラムを登録・取得"""
        if name not in self._metrics:
            self._metrics[name] = Histogram(
                name, description, labels or [],
                buckets or [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
            )
        return self._metrics[name]

    def export_prometheus(self) -> str:
        """Prometheus形式でエクスポート"""
        lines = []

        for name, metric in self._metrics.items():
            if isinstance(metric, Counter):
                lines.append(f"# HELP {name} {metric.description}")
                lines.append(f"# TYPE {name} counter")
                for key, value in metric._values.items():
                    label_str = self._format_labels(metric.labels, key)
                    lines.append(f"{name}{label_str} {value}")

            elif isinstance(metric, Gauge):
                lines.append(f"# HELP {name} {metric.description}")
                lines.append(f"# TYPE {name} gauge")
                for key, value in metric._values.items():
                    label_str = self._format_labels(metric.labels, key)
                    lines.append(f"{name}{label_str} {value}")

            elif isinstance(metric, Histogram):
                lines.append(f"# HELP {name} {metric.description}")
                lines.append(f"# TYPE {name} histogram")
                for key, bucket_counts in metric._counts.items():
                    label_str = self._format_labels(metric.labels, key)
                    cumulative = 0
                    for bucket, count in sorted(bucket_counts.items()):
                        cumulative += count
                        if bucket == float('inf'):
                            lines.append(f'{name}_bucket{{{label_str.strip("{}") + "," if label_str else ""} le="+Inf"}} {cumulative}')
                        else:
                            lines.append(f'{name}_bucket{{{label_str.strip("{}") + "," if label_str else ""} le="{bucket}"}} {cumulative}')
                    total = metric._totals.get(key, 0)
                    sum_val = metric._sums.get(key, 0)
                    lines.append(f"{name}_sum{label_str} {sum_val}")
                    lines.append(f"{name}_count{label_str} {total}")

        return "\n".join(lines)

    def _format_labels(self, label_names: list[str], label_values: tuple) -> str:
        """ラベルをPrometheus形式にフォーマット"""
        if not label_names:
            return ""
        pairs = [f'{name}="{value}"' for name, value in zip(label_names, label_values)]
        return "{" + ", ".join(pairs) + "}"


# グローバルレジストリ
_registry: Optional[MetricsRegistry] = None


def get_metrics_registry() -> MetricsRegistry:
    """メトリクスレジストリのシングルトンを取得"""
    global _registry
    if _registry is None:
        _registry = MetricsRegistry()
    return _registry


# 定義済みメトリクス

def get_request_counter() -> Counter:
    """HTTPリクエストカウンター"""
    return get_metrics_registry().counter(
        "http_requests_total",
        "Total number of HTTP requests",
        ["method", "endpoint", "status_code"]
    )


def get_request_duration() -> Histogram:
    """HTTPリクエスト処理時間"""
    return get_metrics_registry().histogram(
        "http_request_duration_seconds",
        "HTTP request duration in seconds",
        ["method", "endpoint"],
        [0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0]
    )


def get_active_connections() -> Gauge:
    """アクティブ接続数"""
    return get_metrics_registry().gauge(
        "active_connections",
        "Number of active connections",
        ["type"]
    )


def get_db_pool_gauge() -> Gauge:
    """DBコネクションプール状態"""
    return get_metrics_registry().gauge(
        "db_pool_connections",
        "Database connection pool status",
        ["state"]
    )


def get_redis_operations() -> Counter:
    """Redis操作カウンター"""
    return get_metrics_registry().counter(
        "redis_operations_total",
        "Total number of Redis operations",
        ["operation", "status"]
    )


def get_bedrock_requests() -> Counter:
    """Bedrockリクエストカウンター"""
    return get_metrics_registry().counter(
        "bedrock_requests_total",
        "Total number of Bedrock API requests",
        ["model", "status"]
    )


def get_bedrock_tokens() -> Counter:
    """Bedrockトークン使用量"""
    return get_metrics_registry().counter(
        "bedrock_tokens_total",
        "Total number of tokens used",
        ["model", "type"]
    )


def get_agent_executions() -> Counter:
    """エージェント実行カウンター"""
    return get_metrics_registry().counter(
        "agent_executions_total",
        "Total number of agent executions",
        ["tenant_id", "status"]
    )


def get_agent_execution_duration() -> Histogram:
    """エージェント実行時間"""
    return get_metrics_registry().histogram(
        "agent_execution_duration_seconds",
        "Agent execution duration in seconds",
        ["tenant_id"],
        [1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0]
    )


def get_s3_operations() -> Counter:
    """S3操作カウンター"""
    return get_metrics_registry().counter(
        "s3_operations_total",
        "Total number of S3 operations",
        ["operation", "status"]
    )


def get_error_counter() -> Counter:
    """エラーカウンター"""
    return get_metrics_registry().counter(
        "errors_total",
        "Total number of errors",
        ["type", "code"]
    )


@contextmanager
def measure_time(histogram: Histogram, **labels):
    """
    処理時間を計測するコンテキストマネージャー

    使用例:
        with measure_time(get_request_duration(), method="GET", endpoint="/api/test"):
            # 処理
            pass
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        duration = time.perf_counter() - start
        histogram.observe(duration, **labels)
