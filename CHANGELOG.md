
---

## 📄 文件 2：`CHANGELOG.md`

**位置**：`C:\Users\索群\AI_Native_Project\CHANGELOG.md`

**完整内容**：

```markdown
# Changelog

本项目所有重要变更均记录于此文件。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [SemVer 2.0.0](https://semver.org/lang/zh-CN/)。


## [ui/v3.4.1] - 2026-09-17

### Fixed
- **前端环境串台（图表 404）修复**：修复了部署到 Render 后，由于前端 API 请求硬编码为 `http://localhost:10000/api`，导致浏览器将请求发向用户本地电脑，进而引发生成图表路径 Not Found (404) 的问题。

### Changed
- **`frontend/src/api/client.ts`**：将硬编码的 `baseURL` 替换为 `import.meta.env.VITE_API_BASE_URL`，实现动态环境切换。
- **环境变量隔离**：新增 `frontend/.env.development`（本地开发指向 `http://localhost:10000/api`）和 `frontend/.env.production`（生产构建指向 `https://suo-agent-api.onrender.com/api`）。

### Improved
- **开发规范固化**：彻底分离本地开发与 Render 生产环境，今后不再发生前后端请求“串台”问题。

---

## [ui/v3.4.0] - 2026-09-15

### Added
- **故事 6.1 消息复制**：在每条用户/AI 消息气泡右上角始终显示"复制"按钮，一键复制消息内容到剪贴板。
- **故事 6.2 对话导出**：聊天窗顶部新增"导出对话"按钮，将当前会话导出为 `.md` 文件（保留 Markdown 格式、图片 URL、表格）。
- **故事 6.2 增强 单条导出**：AI 助手消息气泡右上角新增"下载"按钮，将单条 AI 回答导出为 `.md` 文件（含用户提问上下文 + 元信息）。
- **故事 6.3 清空对话**：聊天窗顶部新增"清空对话"按钮，通过二次确认弹窗防止误操作。
- **后端新增端点 `POST /api/history/clear`**：调用 `memory.clear(session_id)` 清空指定会话历史，仅清空 `history` 字段，保留 files / tenant 元数据。

### Changed
- **`Chat.tsx` 消息气泡 padding 动态化**：AI 消息右侧 64px（下载 + 复制两个按钮），用户消息右侧 44px（复制一个按钮），避免按钮遮挡文字。
- **`Chat.tsx` 头部按钮组改为 flex 布局**：`display: flex; gap: 4px;`，容纳多个按钮。

### Fixed
- **`handleClear` 作用域修复**：初次插入时被误放入 `handleExportOne` 函数体内，导致工具栏访问不到（报 `Cannot find name 'handleClear'`）。移动到 `handleExportOne` 之后作为平级函数。

---

## [ui/v3.3.0] - 2026-09-15

### Fixed
- **输入框卡顿性能问题**：修复 `Chat.tsx` 中 `input` 状态变化导致整个 `messages.map` 重渲染的问题。长会话（消息多）下每按一个键都会触发所有 `ReactMarkdown` 重新解析 Markdown，造成明显输入延迟。

### Changed
- **`Chat.tsx` 消息列表渲染优化**：将 `messages.map(...)` 抽为 `messageListJsx`，用 `useMemo` 缓存，依赖 `[messages, sessionId]`。输入时 `messages` 引用不变 → 缓存命中 → 不重渲染历史消息。
- **`Chat.tsx` import 更新**：新增 `useMemo`。

### Performance
- 长会话（50+ 消息）输入延迟从数百毫秒降至**几毫秒**。
- 输入性能与消息数量**解耦**——无论会话多长，输入响应速度恒定。

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

## [agent/v3.2.0] - 2026-09-15

### Added
- **故事 4 图片清理策略**：新增 `_cleanup_old_charts(days=30)` 函数，在 FastAPI `startup_event` 启动时自动清理 `uploads/charts/` 目录下超过 30 天的旧图表文件（仅清理 `.png`）。

### Performance
- 避免图表文件长期累积占用磁盘空间，启动时一次性清理，无需定时任务框架。

### 设计说明
- **时机选择**：放在 `startup_event`，因为后端启动频率低，一次性清理成本小。
- **保留范围**：只清理 `.png` 图表，不触碰其它文件，避免误删。
- **可观测**：每次启动打印 `###图片清理###` 日志，含"删除 N 个 / 失败 M 个 / 保留天数"。

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

## [rag/v2.4.0] - 2026-09-12

### Added
- 分层索引策略（语义类全量编码，表格类仅摘要块）。
- 数据摘要块：让 CSV 也有向量进入 ChromaDB，作为"领域筛选入口信号"。
- 多年度数据检测：跨年数据时主动报告。

### Changed
- 按月切分表格数据，导航标注含中文/数字/计数三种表达。
- BM25 索引文本截断至 ~500 字符，避免长度归一化惩罚稀有词。

---

## [release/v5.6.0] - 2026-09-18

### Added
- **SSE 流式输出（打字机效果）**：后端 `chat_core_stream` 改造为异步生成器，前端 `Chat.tsx` 使用 `fetch` 对接 SSE。实现了状态提示（分析中/调用工具）和最终回答的逐字展示，彻底消除长文本等待焦虑。
- **云端 RAG 评估闭环**：修复评估脚本兼容 SSE 格式，跑通真实云端后端评估，最终通过率达到 **95%**，`context_recall` 达到 **0.875**。
- **RRF 分数熔断机制**：在 `rag_v2.py` 中新增 `RRF_SCORE_THRESHOLD = 0.025` 熔断，拒绝无效边缘匹配，大幅提升负样本的 `rejection_accuracy`。

### Changed
- **Render 实例升级**：从 Starter (512MB) 升级至 **Standard (2GB)**，成功支持 `GTE-small` 向量模型在云端加载。
- **RAG 向量检索恢复**：全面恢复 `ChromaDB` + `sentence_transformers`，将向量阈值从 0.55 提升至 **0.82**，精准过滤“边缘泛化”语义噪音。
- **全链路持久化路径统一**：所有 SQLite 数据库、RAG 索引、模型缓存、图表文件全部统一指向 `UPLOAD_DIR`，彻底解决 Render 重启数据丢失问题。

### Fixed
- **`web_search` 云端阻塞**：为 `web_search` 增加 10 秒超时熔断机制，防止云端网络限制导致整个 SSE 流假死。
- **`rag_v2.py` 变量顺序错误**：修复 `CHROMA_DIR` 和 `RAG_DATA_FILE` 未定义导致的 `NameError`。
- **API 路径读取错误**：修复知识库列表、用户管理 SQLite 路径读取错误，统一指向 `$UPLOAD_DIR`。

### Performance
- **模型加载提速**：模型缓存持久化至磁盘，Render 重启加载时间从 6 分钟缩短至约 10-30 秒。

---

## [release/v5.5.0] - 2026-09-18

### Added
- **云端持久化架构**：所有数据（对话记忆、知识库索引、SQLite数据库、图表）全面迁移至 Render 持久化磁盘（`UPLOAD_DIR`），彻底解决重新部署后数据丢失的问题。

### Fixed
- **图表排版与中文字体**：将图表默认字体替换为 `SimHei`，彻底解决 Linux 云端中文方块乱码；优化 Markdown 标题和图片注入逻辑，使报告排版完美契合业务需求。
- **API 读取路径**：修复了知识库列表 API 从源码临时目录读取的错误路径，统一指向持久化磁盘。
- **Git 合并冲突**：清理了 `common/main.py` 的合并残余代码，保障代码分支一致性。

### Changed
- **前端 Markdown 渲染**：将 H1-H4 标题渲染为 `p` 标签，彻底解决标题字号过大破坏 UI 体验的问题。

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