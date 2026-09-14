
---

## 📄 文件 2：`CHANGELOG.md`

**位置**：`C:\Users\索群\AI_Native_Project\CHANGELOG.md`

**完整内容**：

```markdown
# Changelog

本项目所有重要变更均记录于此文件。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [SemVer 2.0.0](https://semver.org/lang/zh-CN/)。


## [rag/v2.5.0] - 2026-09-14

### Added
- **检索基础设施化（核心突破）**：新增 `_retrieve_background(query)` 函数，在 `chat_core` 构建 messages 前**无条件执行**企业知识库检索，检索结果作为"背景资料"注入 system prompt。检索不再依赖 LLM 决策，从根本上解决"LLM 不调 search_knowledge"的问题。
- **意图预判函数 `_is_data_query`**：使用轻量 LLM 调用判断用户问题是否涉及数据文件查询，按需注入 schema。避免"一刀切"注入污染上下文。

### Changed
- **Schema 注入策略**：从"无条件注入所有文件 schema"改为"按意图预判分流"——数据类问题注入 schema，非数据类问题跳过，让 LLM 视野干净。
- **`search_knowledge` 返回值结构**：附加 `[RETRIEVED_IDS]IT-01,IT-02[/RETRIEVED_IDS]` 结构化标记，供后端可靠提取真实检索 ID。
- **`main.py` 的 contexts 提取逻辑**：从"正则从散文抠 ID"改为"从结构化标记直接提取"，彻底杜绝 UUID 映射错位。
- **SYSTEM_PROMPT 能力边界**：从"用户问 X 就用 Y"的命令式，改为"系统已自动检索，背景资料已注入，优先基于背景资料回答"的事实陈述。
- **`chat_core` 的 `collected_sources` 初始化**：从 `[]` 改为 `list(bg["ids"])`，与背景检索结果保持一致。

### Fixed
- 修复"20 题评估中 19 题 `tools=[]`"的决策不触发问题。
- 修复"contexts 为空"问题（背景检索保证每道题都有上下文）。
- 修复 `_is_data_query` 意图预判失败时的 fallback 处理（保守注入 schema）。

### Performance
- **评估集通过率：5% → 85%**（17/20 通过）。
- **context_recall：0.325 → 0.85**（2.6 倍提升）。
- **contexts 覆盖率：9/20 题 → 20/20 题**。

### Known Issues（列入 Backlog）
- Q16、Q20 的 ground_truth 含两个 ID 但只召回一个（TS-05 未进 Top-5），属检索排序问题，待引入 Reranker 优化。
- Q19 negative 题 LLM 回答措辞未命中 `reject_signals`，属评测脚本措辞覆盖不全，待扩充。

---

## [ui/v3.2.0] - 2026-09-14

### Added
- **Logo 资产快照化**：将登录页和聊天页侧边栏的 Logo 从 base64 嵌入迁移到静态文件 `frontend/public/logo.png`。
- **PDF 转 PNG 工具**：新增 `scripts/pdf_to_png.py`，支持自动裁剪空白边缘。
- **新命名规范首例**：本标签是 `ui/` 前缀的首次应用，标志 UI 架构线正式进入规范化管理。

### Changed
- **`Login.tsx`**：移除约 65KB 的 base64 常量，改用 `<img src="/logo.png" />`。
- **`Chat.tsx`**：侧边栏标题从"🚀 某某企业AI原生系统平台"改为"Logo + AI 管理咨询"。

### Fixed
- 修复前端代码中因 base64 嵌入导致的代码可读性问题。

---

## [agent/v3.1.1] - 2026-09-14

### Fixed
- **图片快照化 bugfix**：`chat_core` 中 `memory.append` 早于"图片 markdown 插入"，导致刷新页面后图片丢失。调整执行顺序，先插入图片 markdown，再写入 memory。

---

## [agent/v3.1.0] - 2026-09-14

### Added
- **图片快照化**：`generate_chart` 生成的图表保存为静态文件到 `uploads/charts/{uuid}.png`，返回 URL markdown 而非 base64。
- **静态目录挂载**：FastAPI 挂载 `/charts` 静态目录。
- **Vite 代理**：`vite.config.ts` 新增 `/charts` 代理（开发环境）。

### Changed
- **架构简化**：移除 `[[CHART]]` 占位符 + 独立 `image` 字段方案，回归简单 `ReactMarkdown` 直接渲染 URL 图片。
- **memory 存储**：memory 现存储完整 markdown（含图片 URL），刷新/切窗/跨会话均可恢复图片。

### Fixed
- 修复刷新页面后图表丢失的问题。

---

## [agent/v3.0.0] - 2026-09-13

### Added
- **决策校验器**：当 LLM 首次决策未调用工具，但用户意图涉及数据统计时，系统拦截并要求 LLM 反思重试。
- **输出校验器**：拦截 LLM 回答中未经工具验证的数字。
- **物理层数据溯源**：根据有无上传气泡，后端物理层拼接"根据上传文件 xxx..."或"根据企业知识库文档..."前缀。
- **`generate_chart` 工具**：基于 Seaborn 的确定性绘图工具，LLM 只需传参，工具负责读取、聚合、渲染。
- **图片通道分离**：图片 base64 走独立 `image` 字段，不经过 LLM。

### Changed
- **`aggregate` 工具增强**：支持按 `month`/`year`/`quarter` 时间维度分组，缺失月份自动补齐为 0。
- **`aggregate` 统计摘要**：工具返回自动附带总和、平均值，避免 LLM 自己算导致幻觉。
- **System Prompt 语义化**：从硬规则转向语义边界 + Few-Shot 引导。
- **Schema 注入去样本化**：只提供列名和类型，不提供样本值，引导 LLM 主动调工具。

### Fixed
- 修复 LLM 因历史记忆污染而错误标注数据来源的问题。
- 修复 `UnboundLocalError: tool_trace` 作用域错误。
- 修复 `IndentationError` 缩进错误。
- 修复 `NameError: source_prefix` 作用域错误。

---

## [rag/v2.4.0] - 2026-09-12

### Added
- 分层索引策略（语义类全量编码，表格类仅摘要块）。
- 数据摘要块：让 CSV 也有向量进入 ChromaDB，作为"领域筛选入口信号"。
- 多年度数据检测：跨年数据时主动报告。

### Changed
- 按月切分表格数据，导航标注含中文/数字/计数三种表达。
- BM25 索引文本截断至 ~500 字符，避免长度归一化惩罚稀有词。

---

## [release/v5.4.0] - 2026-09-11

### Added
- RAG 持续评估闭环。
- 向量刷新安全机制。
- RAG 产线级基础能力。

---

## [release/v5.0.0] - 2026-09-10

### Added
- React 基础架构迁移完成。
- UI、聊天、SQLite 持久化。

---

> **说明**：本文档自 `agent/v3.1.1` 起开始正式维护。
> 早期版本的详细变更，请查阅 `git log` 或 `VERSION_HISTORY.md`。