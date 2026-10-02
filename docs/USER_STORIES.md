# 用户故事档案（User Stories）

> 本项目所有用户故事的正式源头档案。未来迁至 Azure DevOps / JIRA 时，从本文件同步。
> 维护人：产品负责人
> 最后更新：2026-10-02

---

## 编号说明

- **US-01 ~ US-04**（历史批次，2026-09）：CHANGELOG 有标题记录，完整描述**待产品负责人补充**。
- **US-05（Reranker 重排）**：产品负责人后续提供的 RAG 优化故事，本文件按此为准。
- **US-06 ~ US-10**：2026-10-01 ~ 10-02 期间的技术债清理 Sprint。
- **US-11 起**：后续新故事按顺序编号。

---

## 目录

- [US-05 引入 Reranker 重排模型](#us-05-引入-reranker-重排模型)
- [US-06 修复 /api/logs 越权访问漏洞](#us-06-修复-apilogs-越权访问漏洞)
- [US-07 ROLE_PERMISSIONS 前后端单一真源](#us-07-role_permissions-前后端单一真源)
- [US-08 Memory Schema Version 迁移机制](#us-08-memory-schema-version-迁移机制)
- [US-09 CI 补齐 lint 检查](#us-09-ci-补齐-lint-检查)
- [US-10 修复部署后页面缓存导致的旧版本问题](#us-10-修复部署后页面缓存导致的旧版本问题)
- [待补充：US-01 ~ US-04](#待补充us-01--us-04)

---

## US-05 引入 Reranker 重排模型

| 字段 | 内容 |
|---|---|
| 编号 | US-05 |
| 标题 | 引入 Reranker 重排模型，提升 RAG 检索的上下文精确率（Context Precision） |
| 角色 | 企业知识库管理员 |
| 优先级 | P1 |
| 状态 | 已完成（AC 修订后验收） |
| 完成时间 | 2026-10-01 |
| 关联 tag | `rag/v2.6.9` |
| 关联 commit | `6d090da` |

### 背景

当前 RAG 采用 `gte-small-zh` 进行向量检索，在 Top-5 候选文档中存在"宁多勿漏"的现象（例如问 IT 打印机报错，召回结果里混入了销售数据）。虽然 LLM 能勉强过滤，但无效的上下文增加了 LLM 幻觉风险。

### 描述

作为一名企业知识库管理员，我希望系统在检索时不仅仅依靠 Embedding 向量相似度进行粗排，还能在经过 RRF 融合后，引入 Reranker（重排序）模型对 Top-N 候选文档进行二次精排，以便在将上下文喂给 LLM 之前，过滤掉那些"语义相似但实际不相关"的边缘噪音文档，从而提升 RAG 评估中的排序质量指标。

### 验收标准

- **AC-1**：在 `common/rag_v2.py` 的 `search_knowledge_v2` 函数中，RRF 融合之后、最终返回之前，新增 Reranker 重排逻辑。
- **AC-2**：使用轻量级 Cross-Encoder 模型 `BAAI/bge-reranker-base`（约 400MB），存入 `UPLOAD_DIR/models` 持久化目录。
- **AC-3**（修订后）：排序类指标达标——`precision@1 ≥ 0.85`、`hit@3 ≥ 0.90`、`mrr ≥ 0.85`。
  - 原 AC-3 为 `context_precision ≥ 0.8`，因 `final_top_k=8` + 单文档 ground_truth 架构下数学上限 = 1/8 = 0.125，不可达，已修订。
- **AC-4**：`context_recall ≥ 0.85`（重排不能过度过滤召回率）。实测达到 **1.0**。
- **AC-5**：确保 Render 2GB 内存能承载（经测试，加载 bge-reranker-base + gte-small-zh 总内存占用约 1.2GB，安全）。

### 技术规格

- **文件**：`common/rag_v2.py`
- **函数**：`search_knowledge_v2`
- **关键参数**：
  - Reranker 截断长度：250 字符（`text[:250]`）
  - 候选池：RRF 融合后 Top-10
  - 最终返回：`final_top_k=8`
- **模型**：`BAAI/bge-reranker-base`，CrossEncoder，device=cpu
- **评估脚本**：`rag_test/evaluate_rag_live.py`
  - 新增指标：`hit@3`、`recall@3`（替代原 `precision@3`）
  - 排序类指标 positive-only 平均（避免负样本 0 分稀释）
  - `passed` 判定加固：`context_recall` + `precision@1` + `hit@3` + `mrr` 四项联合校验

### 依赖

无。

### 遗留项

- **B-001**（记入 BACKLOG）：Q50「X1 2024 开机黑屏，电源灯亮」Top-1 误召 TS-01（电源灯不亮）。根因是 Reranker 截断 250 字符未捕获"亮/不亮"关键区分词。优先级 P2。

---

## US-06 修复 /api/logs 越权访问漏洞

| 字段 | 内容 |
|---|---|
| 编号 | US-06 |
| 标题 | 修复 `/api/logs` 与 `/api/logs/export` 越权访问漏洞 |
| 角色 | 系统管理员 |
| 优先级 | P0（安全） |
| 状态 | 已完成 |
| 完成时间 | 2026-10-01 |
| 关联 tag | `security/v1.0.0` |
| 关联 commit | `b59a32f` + `d5183cc` |

### 背景

- `main.py` 中 `api_logs(session_id: str = "")` 使用空字符串默认值，`if session_id:` 判断在请求不带参数时直接被跳过，权限校验形同虚设。
- `Chat.tsx` 中 `handleExportLogs` 调用 `/logs/export` 时未传 `session_id`，前端行为与后端校验设计不匹配。
- US-02（release/v5.8.0）虽然补了"viewer 拦截"，但只覆盖了带 `session_id` 的路径，未覆盖"空 session_id 绕过"。

### 描述

作为一名系统管理员，我希望 `/api/logs` 和 `/api/logs/export` 两个端点**强制校验请求者身份**，以便防止未登录用户或 viewer 角色通过 F12 直连或 curl 绕过前端拦截，窃取系统操作日志。

### 验收标准

- **AC-1**：请求 `/api/logs` 不带 `session_id` 时返回 `{"status": "error"}`，不返回日志数据。
- **AC-2**：请求 `/api/logs/export` 不带 `session_id` 时返回错误，不导出 CSV。
- **AC-3**：传入不存在的用户名时返回错误。
- **AC-4**：viewer 角色传入自己的 `session_id` 时被拒。
- **AC-5**：manager / admin 传入自己的 `session_id` 时正常返回。
- **AC-6**：`Chat.tsx` 的 `handleExportLogs` 携带 `session_id`。
- **AC-7**：`rag_test/regression/test_permissions.py` 新增 4 条用例覆盖上述场景，全部通过。

### 技术规格

- **后端**：`common/main.py`
  - `api_logs`：`if not session_id or not session_id.strip(): return {"status": "error"}`
  - `api_logs_export`：同样加固
  - 用户不存在：`if not _row: return {"status": "error"}`
- **前端**：`frontend/src/pages/Chat.tsx`
  - `handleExportLogs`：`api.get(\`/logs/export?session_id=${sessionId}\`)`
- **回归测试**：`rag_test/regression/test_permissions.py`
  - 新增 4 条边界：无 `session_id` / 空 `session_id` / 不存在用户 / export 无参数

### 依赖

无。

---

## US-07 ROLE_PERMISSIONS 前后端单一真源

| 字段 | 内容 |
|---|---|
| 编号 | US-07 |
| 标题 | ROLE_PERMISSIONS 前后端单一真源 |
| 角色 | 开发工程师 |
| 优先级 | P1 |
| 状态 | 已完成 |
| 完成时间 | 2026-10-01 |
| 关联 tag | `rbac/v1.0.0` |
| 关联 commit | `ee5dd5c` |

### 背景

- `auth.py` 的 `ROLE_PERMISSIONS` 是权限逻辑的唯一真源（工具权限映射）。
- 但 `Chat.tsx` 中角色中文名（`roleMap`）和用户管理页的角色下拉列表（3 处 `<Select.Option>`）是**硬编码**。
- 一旦后端新增角色（如 `auditor`），前端不会自动出现，且中文名映射需要手改多处，容易漏改。

### 描述

作为一名开发工程师，我希望前端角色列表（中文名 + 英文值）**从后端 API 动态获取**，以便在新增/修改角色时只需改后端一处，前后端定义不再漂移。

### 验收标准

- **AC-1**：`auth.py` 新增 `ROLE_DISPLAY_NAMES` 映射表。
- **AC-2**：`main.py` 新增 `GET /api/users/roles` 端点，返回 `[{value, label}, ...]`。
- **AC-3**：`Chat.tsx` 的角色中文名渲染不再使用硬编码 `roleMap`，改从 API 获取。
- **AC-4**：`Chat.tsx` 中 3 处硬编码的 `<Select.Option>` 改为 `rolesList.map()`。
- **AC-5**：手动测试——新增一个后端角色（临时），前端刷新后能自动出现，无需改前端代码。

### 技术规格

- **后端**：
  - `common/auth.py`：新增常量 `ROLE_DISPLAY_NAMES: Dict[str, str]`
  - `common/main.py`：新增 `@app.get("/api/users/roles")`
- **前端**：`frontend/src/pages/Chat.tsx`
  - 新增 state：`const [rolesList, setRolesList] = useState<Array<{value, label}>>([])`
  - 新增 useEffect：`api.get('/users/roles')`
  - 删除 `roleMap`，改 `getRoleLabel(role)` 查表函数
  - 3 处 `<Select.Option>` → `rolesList.map()`
- **回归测试**：`rag_test/regression/test_permissions.py`
  - 新增 1 条：`/api/users/roles` 返回 4 个角色

### 依赖

无。

---

## US-08 Memory Schema Version 迁移机制

| 字段 | 内容 |
|---|---|
| 编号 | US-08 |
| 标题 | Memory Schema Version 迁移机制 |
| 角色 | 系统架构师 |
| 优先级 | P2 |
| 状态 | 已完成 |
| 完成时间 | 2026-10-01 |
| 关联 tag | `memory/v1.0.0` |
| 关联 commit | `83c6c3e` |

### 背景

- 当前 `memory.py` 的 `append` 写入结构为 `{history, tenant, files, file_context}`，无版本标识。
- 当底层 memory 结构升级（如新增字段、修改 history 语义）时，旧数据会被原样加载，可能触发"旧数据复述"问题（如 release/v5.8.0 修复的"bob 复述旧越权查询记录"）。
- 无版本机制意味着每次结构变更都要手动清理 memory，不可持续。

### 描述

作为一名系统架构师，我希望 `memory.json` 中的每条会话记录携带 `schema_v` 字段，并在加载时根据版本号自动清理不兼容的旧数据，以便从根上解决"旧格式 memory 被 LLM 复述造成幻觉"的问题。

### 验收标准

- **AC-1**：`memory.py` 新增常量 `CURRENT_SCHEMA_V`。
- **AC-2**：`_load()` 调用 `_migrate()`，对 `schema_v < CURRENT_SCHEMA_V` 的 session 清空 `history`，保留 `files` / `tenant`。
- **AC-3**：`_migrate()` 打印清理日志（清理了哪些 session、数量）。
- **AC-4**：`append` / `set_file_context` / `add_uploaded_file` 写入时带上 `schema_v`。
- **AC-5**：首次启动后，`memory.json` 中所有 session 都带 `schema_v: 2`。
- **AC-6**：**副作用已知并接受**——本次上线会清空所有现有会话的 history。

### 技术规格

- **文件**：`common/memory.py`
- **新增常量**：`CURRENT_SCHEMA_V = 2`
- **新增方法**：`_migrate(raw_store: dict) -> dict`
- **修改方法**：
  - `_load()`：调用 `self._migrate(raw_store)`
  - `append` / `set_file_context` / `add_uploaded_file`：写入时加 `"schema_v": CURRENT_SCHEMA_V`，且 `setdefault("schema_v", ...)` 防御
- **迁移效果**：
  - 本地：清理 84 个旧 session
  - 云端（Render）：清理 69 个旧 session

### 依赖

无。

### 遗留项

- 未来升级：改 `CURRENT_SCHEMA_V`，并在 `_migrate` 里补对应迁移分支。

---

## US-09 CI 补齐 lint 检查

| 字段 | 内容 |
|---|---|
| 编号 | US-09 |
| 标题 | CI 补齐 lint 检查 |
| 角色 | 开发工程师 |
| 优先级 | P1 |
| 状态 | 已完成 |
| 完成时间 | 2026-10-01 |
| 关联 tag | `ci/v1.0.0` |
| 关联 commit | `a0585c6` |

### 背景

- `.github/workflows/deploy.yml` 目前包含：pip cache → HF cache → install → prepare data → start backend → run regression → node setup → npm ci → npm build → deploy。
- **没有 lint 步骤**。
- 上周计划中的"lint + build + 评估"三件事，只落地了后两件。

### 描述

作为一名开发工程师，我希望 CI 流程在 build 和回归测试之外，**额外执行 Python 和前端 lint**，以便在 PR 阶段拦截语法错误、未定义变量、引用错误等低级问题，避免流入生产。

### 验收标准

- **AC-1**：`deploy.yml` 新增 `Lint Python code` 步骤，运行 `ruff check`，只检查 E9/F63/F7/F82 四类硬错误，`continue-on-error: false`。
- **AC-2**：`deploy.yml` 新增 `Lint Frontend code` 步骤，运行 `npm run lint`，`continue-on-error: true`（先 warn，不阻塞）。
- **AC-3**：`frontend/package.json` 包含 `lint` script。
- **AC-4**：CI 在 RAG_V2_EXPERIMENTAL 分支下一次 push 时，lint 步骤显示绿色（或前端为黄色 warning）。
- **AC-5**：CI lint 上线时，修复 ruff 抓到的 2 个真问题。

### 技术规格

- **CI 配置**：`.github/workflows/deploy.yml`
  - Python lint：`ruff check common/ bus_memory/ rag_test/ scripts/ --select=E9,F63,F7,F82 --ignore=F401 --no-cache`
  - 前端 lint：`cd frontend && npm run lint`
- **ESLint 配置**：`frontend/eslint.config.js`（flat config）
  - 放宽历史技术债规则（B-002）：`no-explicit-any` off、`no-unused-vars` warn、`react-hooks/immutability` off
  - 从 52 errors → 0 errors / 17 warnings
- **F821 修复**：
  - `common/main.py:948`：异步生成器引用已被 Python 清除的异常变量 `e`，改为提前捕获为 `error_msg`
  - `common/agents_memory.py`：`TYPE_CHECKING` 保护 `EventBus` 类型注解

### 依赖

无。

### 遗留项

- **B-002**（记入 BACKLOG）：前端 52 条 lint 债（本 Sprint 从 52 errors 降为 17 warnings），下一轮集中清理后恢复严格规则。

---

## US-10 修复部署后页面缓存导致的旧版本问题

| 字段 | 内容 |
|---|---|
| 编号 | US-10 |
| 标题 | 修复部署后用户看到旧版本页面的缓存问题 |
| 角色 | 云端系统用户 |
| 优先级 | P0（影响所有用户，每次部署必现） |
| 状态 | 已完成 |
| 完成时间 | 2026-10-02 |
| 关联 tag | `deploy/v1.0.0` |
| 关联 commit | `03d5eb7` |

### 背景

- 用户在云端输入"各月咖啡销售趋势并画出饼图……"提示词，图表偏离中间位置（v5.9.10 前端居中修复未生效）
- 同一提示词在本地环境显示正常
- 用户按 `Ctrl+Shift+R` 强制刷新后，云端显示正常

**根因分析**（三层）：

1. **`index.html` 被浏览器缓存**
   - `common/main.py` 的 `serve_react` 返回 `FileResponse` 时**未设置 `Cache-Control`**
   - 浏览器按默认策略缓存 `index.html`（几分钟到几小时）
   - 缓存期内，浏览器一直返回**旧的 `index.html`**（引用旧 JS hash）
   - 即使服务器上已经上传了新版 JS，用户也加载不到

2. **Vite hash 策略被绕过**
   - `index.html` 中引用的 JS 文件名带 hash（`index-XXXX.js`），内容变则 hash 变
   - 但这个机制的前提是**浏览器每次都拿到最新的 `index.html`**
   - 一旦 `index.html` 被缓存，hash 机制的"自动发现新版本"就失效

3. **依赖版本漂移加剧 hash 不稳定**
   - Render Build Command 曾使用 `npm install`（宽松版本解析）
   - 每次 build 可能装到不同小版本的依赖 → 产物 hash 每次都变

### 描述

作为一名使用云端系统的企业用户，我希望**每次后端部署完成后，我只需要普通刷新（F5）就能看到最新版本的页面**，以便我不需要知道"什么是强制刷新"，也不因缓存问题看不到刚发布的修复或功能，从而不产生"系统是不是坏了"的客服咨询。

### 验收标准

- **AC-1**：`/` 和 SPA 回退路由（`/{full_path}`）返回的 `index.html` 带以下响应头：
Cache-Control: no-cache, no-store, must-revalidate
Pragma: no-cache
Expires: 0

- **AC-2**：`/assets/*` 返回的静态资源带以下响应头：
Cache-Control: public, max-age=31536000, immutable

- **AC-3**：本地验证通过：
- `curl.exe -s -D - -o NUL "http://127.0.0.1:10000/"` 显示 `cache-control: no-cache, no-store, must-revalidate`
- `curl.exe -I "http://127.0.0.1:10000/assets/index-XXXX.js"` 显示 `cache-control: public, max-age=31536000, immutable`
- **AC-4**：云端（Render）验证通过：
- `curl.exe -s -D - -o NUL "https://suo-agent.onrender.com/"` 显示 no-cache 响应头
- 用户**普通 F5** 后能加载到最新版 JS（hash 与本地最新 build 一致）
- **AC-5**：Render Build Command 改为 `npm ci` 而非 `npm install`，保证每次 build 产出的 hash 稳定。
- **AC-6**：回归测试 31/31 全绿（本次改动不影响测试）。
- **AC-7**：本问题记入 `docs/BACKLOG.md`，编号 B-003。

### 技术规格

- **文件**：`common/main.py`
- 新增模块级常量 `_HTML_NO_CACHE_HEADERS`（`Cache-Control` / `Pragma` / `Expires`）
- `serve_react()` 和 `serve_spa()` 的 `FileResponse` 带 `headers=_HTML_NO_CACHE_HEADERS`
- 自定义 `_CacheControlledStaticFiles(StaticFiles)` 类，重写 `file_response` 加 `Cache-Control: immutable`
- `app.mount("/assets", _CacheControlledStaticFiles(...))` 替代原 `StaticFiles`
- **Render 后台**：Build Command 由 `npm install` 改为 `npm ci`
- 新值：`cd /opt/render/project/src && pip install -r requirements.txt && cd frontend && npm ci && npm run build`

### 依赖

无。

### 遗留项

无。本故事全部 AC 达成。

---

## US-11 图表资源化——LLM 自主决定报告结构与图表位置

| 字段 | 内容 |
|---|---|
| 编号 | US-11 |
| 标题 | 图表资源化——LLM 自主决定报告结构与图表位置 |
| 角色 | 企业用户 / 开发工程师 |
| 优先级 | P1 |
| 状态 | 已完成 |
| 完成时间 | 2026-10-02 |
| 关联 tag | `agent/v3.5.0` |
| 关联 commit | 待补 |

### 背景

- v5.9.4 引入 `{CHART}` 占位符，防止 LLM 改写图片 URL。LLM 保留占位符则位置由 LLM 决定；丢失则后端兜底插顶部。
- 但当用户未指定报告格式时，LLM 对 `{CHART}` 的位置决策**随机**（有时图表在前，有时数据在前）。
- 早期尝试过"锚定顶部"，但破坏了用户的格式偏好。
- 根因分析：
  1. `{CHART}` 是自定义占位符，LLM 训练数据里没有，thinking mode 下处理不一致
  2. 工具返回"现成 markdown"，LLM 直接抄，被动输出
  3. 后端动辄"重排"是打补丁，不是提升 AI 能力

### 描述

作为一名企业用户，我希望：
1. **我指定格式时**，LLM 完全按我的要求输出；
2. **我未指定时**，LLM 用人类报告习惯自主组织（数据 → 图表 → 分析），而不是随机；
3. **系统内部不在 SYSTEM_PROMPT 堆积规则**，而是通过提升 LLM 的输入语义质量来实现。

### 验收标准

- **AC-1**：用户指定格式（如"格式：1.饼图 2.数据 3.分析"），LLM 严格按用户要求输出。
- **AC-2**：用户未指定格式时，LLM 自主决定顺序（不强求一致，但合理——如"数据 → 图表 → 分析"）。
- **AC-3**：LLM 保留真实 markdown 图片（`![标题](/charts/xxx.png)`），无 `{CHART}` 残留。
- **AC-4**：工具返回"资源清单"（含 URL + 使用说明），不再返回现成 markdown。
- **AC-5**：SYSTEM_PROMPT 中无 `{CHART}` 描述、无报告格式规则。
- **AC-6**：LLM 可自主决定生成多张图（如"饼图 + 折线图"加强理解），这是合理的语义判断。

### 技术规格

- **`common/tools.py`**：
  - `generate_chart` 返回资源清单：`[图表资源已生成]` / 类型 / 标题 / 图片 URL / 使用说明 / 统计摘要
  - `generate_image` 同步改造
- **`common/main.py`**：
  - SYSTEM_PROMPT 删除 `{CHART}` 描述
  - tool 循环从结果中提取所有图片 URL（不再替换为 `{CHART}`）
  - 最终图片处理：宽松匹配 LLM 引用的 markdown（支持相对路径、大写 hex）
  - LLM 未引用任何图片时，用记录的 URL 兜底插顶部
- **模型**：主循环使用 `deepseek-flash`（V4.1，默认 thinking mode）

### 设计理念

- **LLM 从"抄写员"变"编辑"**：必须自己判断要不要用图、放哪里、怎么构造 markdown
- **不靠 prompt 堆规则**：SYSTEM_PROMPT 保持精简
- **不靠后端重排**：后端只做 URL 保护 + 兜底 + 素材交付
- **符合"让 AI 变强，而不是呵护它"** 的架构原则

### 依赖

无。

### 遗留项

无。

---

## 待补充：US-01 ~ US-04

以下 4 个历史故事（2026-09 期间完成）在 `CHANGELOG.md` 的 `release/v5.8.0` 段落中有标题记录，但**完整描述待产品负责人补充**。

| 编号 | 标题（来自 CHANGELOG） | 完成时间 |
|---|---|---|
| US-01 | 账户禁用漏洞修复 | 2026-09-22 |
| US-02 | 观察者角色权限拦截 | 2026-09-22 |
| US-03 | Worker 实时状态监控 | 2026-09-22 |
| US-04 | 系统健康仪表板重构 | 2026-09-22 |

> **补录格式建议**：与 US-05~US-10 保持一致（背景 / 描述 / AC / 技术规格 / 状态 / 依赖）。
> **补录来源**：产品负责人 Excel 里的用户故事表（或本次迁移到 Azure DevOps / JIRA 后的正式条目）。

---

## 附：编号冲突说明

`CHANGELOG.md` 中 `release/v5.8.0` 段落提到 "US-05 聊天窗口用户身份展示优化"，但产品负责人后续以 **US-05 编号**重新描述了 "引入 Reranker 重排模型" 的故事。

本文件**以产品负责人后续提供的版本为准**（US-05 = Reranker 重排）。

> 若 "聊天窗口用户身份展示优化" 也需正式归档，请产品负责人确认其**新编号**（建议 US-11 或纳入 US-01~US-04 补录批次）。

---

**维护约定**：
1. 每新增一个用户故事，先在本文件追加条目（编号 / 背景 / 描述 / AC / 技术规格 / 状态 / 依赖）。
2. 代码完成后更新 `状态` 字段为"已完成"，并补 `关联 tag` / `关联 commit`。
3. 用户故事迁至 Azure DevOps / JIRA 后，本文件保留为"归档档案"，不再更新（或作为只读镜像）。

---