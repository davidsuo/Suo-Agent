
---

## 📄 文件 2：`CHANGELOG.md`

**位置**：`C:\Users\索群\AI_Native_Project\CHANGELOG.md`

**完整内容**：

```markdown
# Changelog

本项目所有重要变更均记录于此文件。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [SemVer 2.0.0](https://semver.org/lang/zh-CN/)。

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