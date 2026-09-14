# 版本历史与命名规范

> 最后更新：2026-09-14
> 维护者：索群

本文档记录 AI Native Project 的完整版本演进史，并定义新的标签命名规范。

---

## 一、命名规范（自 v3.1.1 起生效）

### 标签格式

`<线>/v<主版本>.<次版本>.<修订版本>[-<可选标签>]`

### 五条正交的线

本项目实际包含五条**并行的架构演进线**，早期混用同一套主版本号，导致标签混乱。从今日起，新标签按线归入命名空间：

| 线 | 前缀 | 范围 | 说明 |
| :--- | :--- | :--- | :--- |
| **UI 架构线** | `ui/` | 前端框架、UI 布局、交互 | Gradio → React 迁移 |
| **RAG 架构线** | `rag/` | 检索架构、向量库、切分策略 | RAG V1 → V2 混合 |
| **Agent 架构线** | `agent/` | 决策、执行、输出三层架构 | AI Native 架构 |
| **综合发布线** | `release/` | 里程碑发布，对应 `main` 分支部署 | 部署到 Render |
| **文档/规范线** | `docs/` | 工程规范、文档体系 | VERSION_HISTORY、CHANGELOG |

### 分支策略

| 分支 | 定位 | 部署 |
| :--- | :--- | :--- |
| `main` | 生产分支，部署到 Render | ✅ |
| `RAG_V2_EXPERIMENTAL` | 开发集成分支 | ❌ |
| `feature/xxx`（未来） | 单功能开发 | ❌ |
| `hotfix/xxx`（未来） | 紧急修复 | 直接合 main |

### 打标签流程

```bash
# 1. 在开发分支完成里程碑
git add -A
git commit -m "feat: <描述>"
git tag -a agent/v3.2.0 -m "<里程碑说明>"
git push origin RAG_V2_EXPERIMENTAL --tags

# 2. 里程碑发布时，合并到 main
git checkout main
git merge RAG_V2_EXPERIMENTAL
git tag -a release/v3.2.0 -m "<发布说明>"
git push origin main --tags

# 3. 切回开发分支
git checkout RAG_V2_EXPERIMENTAL

二、旧标签归档表
说明：以下旧标签保留不删，作为项目历史档案。新规范生效后，未来标签按第一部分命名。

2.1 UI 架构线（Gradio 时代）
旧标签	里程碑	归档到
v1.0	企业AI原生系统 UI 基础版定型	ui/v1.0.0
v1.1	用户管理后台定型	ui/v1.1.0
v1.2	RAG 极速与完美 UI 版本	ui/v1.2.0
v2.1	UI 完美版，RAG 稳定版	ui/v2.1.0
v3.0	企业 AI 原生系统 UI 与 RAG 全面成熟版	ui/v3.0.0
v4.0	Gradio 完美 UI 和基本功能 demo	ui/v4.0.0

2.2 综合发布线（React 迁移 + 早期 RAG V2）
旧标签	里程碑	归档到
v5.0.0	React 架构基础版定型（UI/聊天/SQLite）	release/v5.0.0
v5.1.0	React 用户管理后台定型（含已知禁用 bug）	release/v5.1.0
v5.2.0	Sprint 1：RAG V2 基础管道	release/v5.2.0
v5.2.1	Sprint 2：RAG V2 混合检索 + 元数据过滤	release/v5.2.1
v5.3.0	Sprint 3：RAG V2 全面成熟（含 V1 回滚开关）	release/v5.3.0
v5.4.0	Sprint 4：RAG 持续评估闭环 + 向量刷新	release/v5.4.0

2.3 RAG 架构线
旧标签	里程碑	归档到
v2.0.0-bm25	V2 实验版：BM25 基础混合检索	rag/v2.0.0
v2.1.0-bm25-semantic	V2 稳定版：BM25 语义检索 + 物理统计	rag/v2.1.0
v2.2.0-semantic-tools	物理级通用数据分析工具，终结幻觉	rag/v2.2.0
v2.3.0-physical-route	物理级路由	rag/v2.3.0
v2.4.0-layered-rag-semantic	分层索引 + 语义驱动架构定型	rag/v2.4.0
v6.0.0-pre-refactor	重构前基线	rag/v6.0.0-pre

2.4 Agent 架构线（AI Native）
旧标签	里程碑	归档到
v3.0.0	AI Native V3 收官：决策校验器 + 工具化聚合 + 图表通道分离	agent/v3.0.0
v3.1.0	图片快照化：图片走静态 URL，刷新不丢图	agent/v3.1.0
v3.1.1	Bugfix：memory 存储含图片 markdown	agent/v3.1.1

2.5 文档/规范线
新标签	时间	里程碑
docs/v1.0.0	2026-09-14	建立版本管理规范：VERSION_HISTORY.md + CHANGELOG.md


2.6 新规范下的标签记录（正式启用）

> 从 `ui/v3.2.0` 开始，新标签严格遵循 `<线>/vX.Y.Z` 命名规范。以下是已发布的正式标签：

| 新标签 | 时间 | 归属线 | 里程碑 |
| :--- | :--- | :--- | :--- |
| `ui/v3.2.0` | 2026-09-14 | UI | Logo 资产快照化（base64 → 静态文件） |

> **说明**：此章节将持续更新。每次打新标签时，在此登记一行，并在 `CHANGELOG.md` 记录详细变更。

---

# 查看所有标签
git tag

三、版本查看
# 查看按线分组的标签（未来生效）
git tag -l "ui/*"
git tag -l "rag/*"
git tag -l "agent/*"
git tag -l "release/*"
git tag -l "docs/*"

# 查看某标签的详细信息
git show agent/v3.1.1

四、版本号语义
采用 SemVer 2.0.0：

主版本（MAJOR）：不兼容的架构变更（如 Gradio → React、RAG V1 → V2）

次版本（MINOR）：向后兼容的功能新增（如新增 generate_chart 工具）

修订版本（PATCH）：向后兼容的 Bug 修复（如 memory 顺序修复）

预发布标签：用 -pre、-alpha、-beta、-rc 后缀，如 rag/v6.0.0-pre。
