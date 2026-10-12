# AI Native 架构盘点

> 版本：v1.0
> 日期：2026-10-12
> 目的：以 AI Native 五层架构为标尺，盘点现有系统的现状与 Gap
> 维护者：产品负责人 + 开发团队

---

## 一、五层架构现状映射

### 1. 应用层（Application Layer）

**职责**：面向用户的边缘，用户意图与概率系统操作动态交互。

**现有实现**：

| 能力 | 位置 | 状态 |
|---|---|---|
| 对话式 UI（Chat + SSE 流式） | `frontend/src/pages/Chat.tsx` | ✅ |
| 登录 UI | `frontend/src/pages/Login.tsx` | ✅ |
| 后端 API 端点（chat / login / upload / kb / users / logs / health / feedback / status） | `common/main.py::api_*` | ✅ |
| 文件上传（临时） | `main.py::api_upload_temp` / `api_upload` | ✅ |
| SPA 静态服务 | `main.py::serve_react` / `serve_spa` | ✅ |
| 自主工作流 | `tools.py::execute_workflow_tool` | ⚠️ 简单实现 |
| 高度个性化推荐组件 | — | ❌ 缺失 |

**Gap**：
- ❌ **无主动推送能力**（日报、预警、决策建议）
- ❌ **无任务型交互**（用户说目标 → AI 规划 → 执行 → 汇报）
- ⚠️ **多模态输入有限**（仅支持"上传后调 OCR/ASR"，非原生多模态对话）

---

### 2. 编排层（Orchestration Layer）

**职责**：应用逻辑的"大脑"。管理工作流、动态路由查询、处理安全机制。

**现有实现**：

| 能力 | 位置 | 状态 |
|---|---|---|
| 主流程编排 | `main.py::chat_core_stream` | ✅ |
| 语义分析（能力筛选 + 格式判断 + 任务性质） | `main.py::_analyze_query` | ✅ |
| 数据源目录注入 | `main.py::_discover_data_sources` | ✅ |
| 物理层无条件检索 | `main.py::_retrieve_background` | ✅ |
| 上下文防爆 | `main.py::_shrink_messages` | ✅ |
| 输入过滤（字符串级） | `guardrails.py::input_guard` | ⚠️ 无语义审核 |
| 输出脱敏（字符串级） | `guardrails.py::output_guard` | ⚠️ 无语义审核 |
| 工具二次确认 | `guardrails.py::tool_call_guard` | ✅ |
| 权限物理层拦截（RBAC） | `main.py` 工具循环 | ✅ |
| 任务队列 + 统计 | `agents_memory.py::WorkerAgent / QueryWorker` | ⚠️ 仅任务路由，非 Agent |
| 事件总线 | `bus_memory/event_bus.py` | ⚠️ 未充分利用 |

**Gap**：
- ❌ **无多 Agent 协作**（QueryWorker/CommandWorker 只是任务队列）
- ❌ **无动态路由机制**（`_analyze_query` 是单次决策，无多轮规划）
- ❌ **Guardrails 仅字符串过滤**（缺少语义审核）
- ⚠️ **编排逻辑与 FastAPI 路由混在 `main.py`（1000+ 行）**，无独立模块

---

### 3. 数据与上下文层（Data & Context Layer）

**职责**：连续的语义知识库，从僵化的表格转向非结构化的智能处理。

**现有实现**：

| 能力 | 位置 | 状态 |
|---|---|---|
| 向量数据库（ChromaDB） | `rag_v2.py` + `uploads/chroma_db/` | ✅ |
| 混合检索（向量 + BM25 + RRF + Reranker） | `rag_v2.py::search_knowledge_v2` | ✅ |
| 分层索引（语义类全量 / 表格类摘要） | `rag_v2.py::index_document_v2` | ✅ |
| 部门分片（多 collection） | `rag_v2.py::_get_collection` | ✅ |
| 智能分块（Markdown / 表格 / 通用） | `rag_v2.py::_smart_chunk_text` | ✅ |
| 短期对话记忆 | `memory.py`（`memory.json`） | ✅ |
| 知识库元数据 | `uploads/rag_data.json` | ✅ |
| 上下文组装 | `main.py` 拼接 + `memory.get()` | ⚠️ 简单拼接 |
| 实时流数据管道 | — | ❌ 缺失 |
| 结构化长期记忆（偏好/决策/图谱） | — | ❌ 缺失 |

**Gap**：
- ❌ **无 Agentic RAG**（LLM 不能自主决定检索什么、几次、用什么工具）
- ❌ **无结构化长期记忆**（用户偏好、历史决策、上下文图谱）
- ❌ **无实时流数据管道**
- ⚠️ **上下文组装器简单**（字符串拼接，无智能压缩/摘要）

---

### 4. 模型层（Model Layer）

**职责**：承载基础推理引擎，生成实时预测和概率输出。

**现有实现**：

| 能力 | 位置 | 状态 |
|---|---|---|
| 主 LLM（DeepSeek Flash） | `main.py::chat_core_stream` 主循环 | ✅ |
| 语义分析 LLM（DeepSeek Chat） | `main.py::_analyze_query` | ✅ |
| 向量模型（GTE-small-zh） | `rag_v2.py::_vector_model` | ✅ |
| 精排模型（BGE-reranker-base） | `rag_v2.py::_reranker_model` | ✅ |
| RAGAS judge（DeepSeek Chat） | `rag_test/evaluate_ragas.py` | ✅ |
| 多模型智能路由 | — | ⚠️ 手动指定，非自动 |
| 跨模型失败回退 | — | ❌ 仅同模型重试 3 次 |
| 本地托管 LLM（vLLM / LLaMA） | — | ❌ 缺失 |

**Gap**：
- ❌ **无多模型智能路由**（按任务类型自动选模型）
- ❌ **无跨模型失败回退**（flash 失败 → 回退 chat）
- ❌ **无本地托管 LLM**（P3，见 BACKLOG）

---

### 5. 基础设施层（Infrastructure Layer）

**职责**：提供训练和运行大规模模型所需的原始计算能力和执行环境。

**现有实现**：

| 能力 | 位置 | 状态 |
|---|---|---|
| 云部署 | Render | ✅ |
| CI/CD | `.github/workflows/deploy.yml` | ✅ |
| 持久化存储 | Render Persistent Disk（5GB） | ✅ |
| 启动脚本 | `start.sh`（自动下载模型） | ✅ |
| 依赖管理 | `requirements.txt` | ✅ |
| K8s 编排 | — | ❌ 不需要 |
| GPU 训练 | — | ❌ CPU-only |
| 边缘节点 | — | ❌ 不需要 |

**Gap**：当前阶段业务无刚需，**不作为 Sprint 目标**。

---

## 二、Gap 清单（按优先级）

### P0（本 Sprint 应立刻做）

| # | Gap | 位置 | 价值 |
|---|---|---|---|
| P0-1 | `main.py` 缺层次 docstring | `main.py` 头部 | 为后续模块化提供"地图" |
| P0-2 | 编排逻辑未模块化 | `main.py::chat_core_stream` | 1000 行巨石，改动风险高 |

### P1（1-2 个 Sprint 内）

| # | Gap | 价值 |
|---|---|---|
| P1-1 | 双向 Guardrails（输入 + 输出语义审核） | 安全 + 回答质量 |
| P1-2 | 多模型智能路由 + 跨模型失败回退 | 稳定性 + 成本优化 |
| P1-3 | Agentic RAG（LLM 自主决定检索策略） | RAG 质量天花板 |

### P2（2-3 个 Sprint 内）

| # | Gap | 价值 |
|---|---|---|
| P2-1 | 结构化长期记忆（用户偏好、历史决策） | 个性化 + 连续性 |
| P2-2 | 任务型交互（目标 → 规划 → 执行 → 汇报） | 用户价值跃升 |
| P2-3 | 主动推送（日报、预警、决策建议） | 用户粘性 |

### P3（放进 BACKLOG，等业务场景明确）

| # | Gap | 备注 |
|---|---|---|
| P3-1 | 本地托管 LLM（vLLM / LLaMA） | 硬件门槛高，暂不启动 |
| P3-2 | 多 Agent 协作（检索/分析/执行 Agent） | 依赖 P0/P1 完成后再上 |

---

## 三、后续 Sprint 规划（用户故事）

详见 `docs/USER_STORIES.md` 的 US-13 ~ US-19 条目。

**Sprint 顺序**：

- **Sprint 1**：US-13（模块化拆分）+ US-14（双向 Guardrails）
- **Sprint 2**：US-15（多模型路由）+ US-16（Agentic RAG）
- **Sprint 3+**：US-17 ~ US-19（长期记忆 / 任务型 / 多 Agent）

---

## 四、附录：现有代码模块清单

| 文件 | 职责 | 行数级 | 架构层 |
|---|---|---|---|
| `common/main.py` | FastAPI 路由 + 编排 + 生成 | ~1000 | 应用层 + 编排层 + 模型层 |
| `common/tools.py` | 工具函数库（含 RAG 检索、OCR、搜索等 19 个） | ~1000 | 编排层 + 数据层（调用） |
| `common/rag_v2.py` | 混合检索（向量 + BM25 + Reranker） | ~600 | 数据与上下文层 |
| `common/auth.py` | 认证 + RBAC | ~200 | 应用层 + 编排层（权限） |
| `common/memory.py` | 短期对话记忆 | ~180 | 数据与上下文层 |
| `common/guardrails.py` | 字符串级护栏 | ~100 | 编排层 |
| `common/agents_memory.py` | Worker + 队列 | ~200 | 编排层 |
| `bus_memory/event_bus.py` | 事件总线 | ~30 | 编排层 |
| `frontend/src/pages/Chat.tsx` | 聊天 UI | ~1100 | 应用层 |
| `rag_test/evaluate_ragas.py` | RAGAS 评估 | ~250 | 测试 |