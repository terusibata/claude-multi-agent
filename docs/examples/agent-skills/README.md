# Agent Skills サンプル

Claude Agent SDKで使用するAgent Skillsのサンプルです。

## Agent Skills とは

Agent Skillsは、エージェントの動作をカスタマイズするための仕組みです。
特定のタスクに対して、どのツールを使い、どのような手順で処理するかを定義できます。

## ファイル形式

Agent Skillsは **SKILL.md** 形式のみサポートされています。

### 必須構造

```markdown
---
name: skill-name-lowercase
description: What the skill does and when to use it
---

# Skill Title

[Instructions and content]
```

### YAMLフロントマターのフィールド

| フィールド | 必須 | 説明 |
|-----------|------|------|
| `name` | ✅ | 小文字+数字+ハイフン、64文字以下 |
| `description` | ✅ | 何ができるか＋いつ使うか（1024文字以下） |
| `allowed-tools` | ❌ | 使用可能なツールを制限 |
| `model` | ❌ | 特定のモデルを指定 |
| `context` | ❌ | `fork` で独立したサブエージェント実行 |
| `user-invocable` | ❌ | `false` でスラッシュメニューから非表示 |

## ディレクトリ構造

```
.claude/skills/
└── skill-name/
    ├── SKILL.md          # メイン定義（必須）
    ├── reference.md      # 参考資料（任意）
    └── examples.md       # 使用例（任意）
```

## SDK での有効化

```python
from claude_agent_sdk import ClaudeAgentOptions

options = ClaudeAgentOptions(
    cwd="/path/to/project",           # .claude/skills/ を含むディレクトリ
    setting_sources=["user", "project"],  # 重要: これがないとSkillsが読み込まれない
    allowed_tools=["Skill", "Read", "Bash"]
)
```

## サンプル一覧

| スキル名 | 説明 |
|---------|------|
| `servicenow-docs-search` | ServiceNowドキュメントを検索・閲覧 |

## 注意事項

- `setting_sources` を設定しないとSkillsが読み込まれません
- `description` は三人称で記述（"Searches..." ではなく "Search..."）
- SKILL.mdは約500行以内に抑え、詳細は別ファイルにリンク

## 参考リンク

- [Agent Skills Overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
- [Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
