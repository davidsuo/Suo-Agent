# 版本历史与命名规范

> 最后更新：2026-09-14
> 维护者：索群

本文档记录 AI Native Project 的完整版本演进史，并定义新的标签命名规范。

---

## 一、命名规范（自 v3.1.1 起生效）

### 标签格式

<线>/v<主版本>.<次版本>.<修订版本>[-<可选标签>]


### 四条正交的线

本项目实际包含四条**并行的架构演进线**，早期混用同一套主版本号，导致标签混乱。从今日起，新标签按线归入命名空间：

| 线 | 前缀 | 范围 | 说明 |
| :--- | :--- | :--- | :--- |
| **UI 架构线** | `ui/` | 前端框架、UI 布局、交互 | Gradio → React 迁移 |
| **RAG 架构线** | `rag/` | 检索架构、向量库、切分策略 | RAG V1 → V2 混合 |
| **Agent 架构线** | `agent/` | 决策、执行、输出三层架构 | AI Native 架构 |
| **综合发布线** | `release/` | 里程碑发布，对应 `main` 分支部署 | 部署到 Render |

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