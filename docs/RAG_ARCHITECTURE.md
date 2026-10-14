# RAG 架构与逻辑流程

> 版本：v3.0.0
> 日期：2026-10-14
> 目的：以 AI Native 五层架构为标尺，可视化 RAG 在系统中的位置、数据流、请求路径与降级路径
> 维护者：产品负责人 + 开发团队
> 关联文档：`docs/AI_NATIVE_ARCHITECTURE.md`、`docs/CONTRACT.md`

---

## 一、文档目的与读者

本文档回答三个问题：

1. **RAG 在 AI 原生系统五层架构中的位置是什么？**
2. **用户不同输入触发哪些路径？哪些走 RAG 检索，哪些不走？**
3. **每一环失败时降级到哪里？**

读者：开发工程师、架构评审人、新加入成员。

---

## 二、RAG 在 AI 原生系统五层架构中的位置

| 层级 | RAG 相关组件 | 代码位置 |
|---|---|---|
| 应用层 | 知识库管理界面、上传接口、聊天界面 | `Chat.tsx` / `main.py::api_kb_*` |
| 编排层 | 物理层无条件检索、工具包装、输出后处理 | `orchestrator.py` / `tools.py` |
| **数据与上下文层** | **向量库、BM25、RRF、Reranker、元数据** | **`rag_v2.py`** |
| 模型层 | GTE-small-zh 向量模型、bge-reranker-base 精排模型 | `rag_v2.py` |
| 基础设施层 | ChromaDB 持久化、模型缓存、Render 磁盘 | `uploads/` |

**核心定位**：RAG 是**数据与上下文层**的核心实现，向上被**编排层**调用，向下依赖**模型层**的向量与精排模型。

---

## 三、总览图（五层架构 + 闭环 + 降级路径）

```mermaid
flowchart TB
    U_IN["用户输入提示词"]

    subgraph L1["1. 应用层"]
        UI["Chat.tsx"]
        API["main.py::api_chat"]
    end

    subgraph L2["2. 编排层"]
        G_IN["Guardrails 输入"]
        ANA["_analyze_query 能力筛选"]
        ROUTE{"按 capability 分流"}
        LOOP["run_orchestration 主循环"]
        MR["model_router 多模型路由"]
        G_OUT["Guardrails 输出"]
    end

    subgraph L3["3. 数据与上下文层"]
        D_MEM[("memory.json")]
        D_CHROMA[("ChromaDB")]
        D_RJ[("rag_data.json")]
        D_CAL[("calendar.db")]
        D_CSV[("CSV 文件")]
    end

    subgraph L4["4. 模型层"]
        M_FLASH["deepseek-flash"]
        M_CHAT["deepseek-chat"]
        M_REASON["deepseek-reasoner"]
        M_GTE["GTE-small-zh"]
        M_RERANK["bge-reranker-base"]
    end

    subgraph L5["5. 基础设施层"]
        I_RENDER["Render 云端"]
        I_DISK["uploads 持久化磁盘"]
    end

    subgraph FB["降级路径 Fail-Safe"]
        FB_GUARD["审核 LLM 失败 -> fail-open"]
        FB_MODEL["主模型失败 -> 跨模型回退"]
        FB_RAG["RAG 检索失败 -> context 为空"]
    end

    U_IN --> UI
    UI --> API
    API --> G_IN
    G_IN --> ANA
    ANA --> ROUTE

    ROUTE -->|rag_search| P_RAG["路径 D: RAG 检索"]
    ROUTE -->|schedule| P_SCHED["路径 B: 日程"]
    ROUTE -->|file_analysis| P_DATA["路径 C: 数据分析"]

    P_RAG -.-> D_CHROMA
    P_RAG -.-> D_RJ
    P_SCHED -.-> D_CAL
    P_DATA -.-> D_CSV

    P_RAG --> LOOP
    P_SCHED --> LOOP
    P_DATA --> LOOP

    LOOP --> MR
    MR -.-> M_FLASH
    MR -.-> M_CHAT
    MR -.-> M_REASON
    LOOP -.-> D_MEM
    P_RAG -.-> M_GTE
    P_RAG -.-> M_RERANK
    ANA -.-> M_CHAT

    MR --> G_OUT
    G_OUT --> API
    API -->|SSE| UI
    UI --> U_OUT["用户接收答案"]
    U_OUT -.多轮对话.-> U_IN

    G_IN -.失败.-> FB_GUARD
    G_OUT -.失败.-> FB_GUARD
    MR -.失败.-> FB_MODEL
    P_RAG -.失败.-> FB_RAG
    FB_GUARD -.放行.-> LOOP
    FB_MODEL -.回退链.-> LOOP
    FB_RAG -.降级为拒答.-> G_OUT

    classDef user fill:#fff3e0,stroke:#ff6f00,stroke-width:3px
    classDef app fill:#e8f5e9,stroke:#4caf50,stroke-width:2px
    classDef orch fill:#e3f2fd,stroke:#1976d2,stroke-width:2px
    classDef data fill:#ffebee,stroke:#d32f2f,stroke-width:2px
    classDef model fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
    classDef infra fill:#fff8e1,stroke:#f9a825,stroke-width:2px
    classDef fb fill:#ffcdd2,stroke:#c62828,stroke-width:2px

    class U_IN,U_OUT user
    class UI,API app
    class G_IN,ANA,ROUTE,LOOP,MR,G_OUT orch
    class D_MEM,D_CHROMA,D_RJ,D_CAL,D_CSV data
    class M_FLASH,M_CHAT,M_REASON,M_GTE,M_RERANK model
    class I_RENDER,I_DISK infra
    class FB,FB_GUARD,FB_MODEL,FB_RAG fb
```

**图读法**：

- 五层架构自上而下穿过，每个 subgraph 对应一层
- 实线：主流程，从"用户输入"到"用户接收答案"
- 虚线 `.->.`：数据读写 / 模型调用 / 多轮对话闭环
- 红色虚线：降级路径，任何环节失败均有兜底

**核心洞察**：三类用户输入触发**三条不同路径**，只有 `rag_search` 会真正走 ChromaDB 检索。

| 用户输入类型 | capabilities | 是否走 RAG 检索 |
|---|---|---|
| 日程类 | `['schedule']` | 否 |
| 数据分析类 | `['file_analysis']` | 否 |
| 知识问答类 | `['rag_search']` | **是** |

---

## 四、单路径详图

### 4.1 路径 B：日程（不走 RAG）

**典型输入**：`明天上午9点提醒我开周会`

```mermaid
flowchart TD
    U["用户输入: 明天上午9点提醒我开周会"]

    subgraph L1["1. 应用层"]
        UI["Chat.tsx"]
        API["main.py::api_chat"]
    end

    subgraph L2["2. 编排层"]
        G1["input_guard 字符串过滤"]
        G2["input_semantic_guard 语义审核"]
        ANA["_analyze_query 能力筛选"]
        DEC{"capabilities"}
        SKIP["跳过 RAG 检索"]
        INJ["注入系统时间"]
        LOOP["LLM 主循环"]
        T1["tool: add_event"]
        G3["output_guard 脱敏"]
        G4["output_semantic_guard 审核"]
    end

    subgraph L3["3. 数据与上下文层"]
        CAL[("calendar.db")]
        MEM[("memory.json")]
    end

    subgraph L4["4. 模型层"]
        CHAT["deepseek-chat"]
        FLASH["deepseek-flash"]
    end

    U --> UI
    UI --> API
    API --> G1
    G1 --> G2
    G2 --> ANA
    ANA -.-> CHAT
    ANA --> DEC
    DEC -->|schedule| SKIP
    SKIP --> INJ
    INJ --> LOOP
    LOOP -.-> MEM
    LOOP -.-> FLASH
    LOOP --> T1
    T1 -->|写| CAL
    LOOP --> G3
    G3 --> G4
    G4 --> API
    API -->|SSE| UI
    UI --> OUT["日程已添加 ID:3"]

    classDef user fill:#fff3e0,stroke:#ff6f00
    classDef app fill:#e8f5e9,stroke:#4caf50
    classDef orch fill:#e3f2fd,stroke:#1976d2
    classDef data fill:#ffebee,stroke:#d32f2f
    classDef model fill:#f3e5f5,stroke:#7b1fa2
    class U,OUT user
    class UI,API app
    class G1,G2,ANA,DEC,SKIP,INJ,LOOP,T1,G3,G4 orch
    class CAL,MEM data
    class CHAT,FLASH model
```

**关键点**：

- `capabilities=['schedule']` → **跳过 `_retrieve_background`**
- 注入系统时间，让 LLM 知道"明天"是哪天
- 一次工具调用 `add_event`，写入 `calendar.db`

### 4.2 路径 C：数据分析（不走 RAG 检索）

**典型输入**：`分析2024年月度咖啡销售收入并生成报告`

```mermaid
flowchart TD
    U["用户输入: 分析2024年月度咖啡销售收入并生成报告"]

    subgraph L1["1. 应用层"]
        UI["Chat.tsx"]
        API["main.py::api_chat"]
    end

    subgraph L2["2. 编排层"]
        G1["input_guard"]
        G2["input_semantic_guard"]
        ANA["_analyze_query"]
        DEC{"capabilities"}
        SKIP["跳过 RAG 检索"]
        DISC["_discover_data_sources 数据源目录"]
        LOOP["LLM 主循环"]
        T1["tool: analyze_file"]
        T2["tool: aggregate"]
        T3["tool: generate_chart"]
        G3["output_guard"]
        G4["output_semantic_guard"]
    end

    subgraph L3["3. 数据与上下文层"]
        CSV[("coffee_sales.csv")]
        CHARTS[("uploads/charts")]
        MEM[("memory.json")]
    end

    subgraph L4["4. 模型层"]
        CHAT["deepseek-chat"]
        FLASH["deepseek-flash"]
    end

    U --> UI
    UI --> API
    API --> G1
    G1 --> G2
    G2 --> ANA
    ANA -.-> CHAT
    ANA --> DEC
    DEC -->|file_analysis| SKIP
    SKIP --> DISC
    DISC --> LOOP
    LOOP -.-> MEM
    LOOP -.-> FLASH
    LOOP --> T1
    T1 -->|读| CSV
    LOOP --> T2
    T2 -->|读| CSV
    LOOP --> T3
    T3 -->|写| CHARTS
    LOOP --> G3
    G3 --> G4
    G4 --> API
    API -->|SSE| UI
    UI --> OUT["月度数据 + 柱状图 + 趋势分析"]

    classDef user fill:#fff3e0,stroke:#ff6f00
    classDef app fill:#e8f5e9,stroke:#4caf50
    classDef orch fill:#e3f2fd,stroke:#1976d2
    classDef data fill:#ffebee,stroke:#d32f2f
    classDef model fill:#f3e5f5,stroke:#7b1fa2
    class U,OUT user
    class UI,API app
    class G1,G2,ANA,DEC,SKIP,DISC,LOOP,T1,T2,T3,G3,G4 orch
    class CSV,CHARTS,MEM data
    class CHAT,FLASH model
```

**关键点**：

- `capabilities=['file_analysis']` → **跳过 `_retrieve_background`**
- 注入数据源目录，告诉 LLM 有 `coffee_sales.csv`
- 工具链：`analyze_file` → `aggregate` → `generate_chart`
- 输出含图片 URL，由 `output_guard` 保护不被脱敏（US-14.1）

### 4.3 路径 D：RAG 检索（唯一走 ChromaDB 的路径）

**典型输入**：`打印机报错 0x80004005 怎么处理`

```mermaid
flowchart TD
    U["用户输入: 打印机报错 0x80004005 怎么处理"]

    subgraph L1["1. 应用层"]
        UI["Chat.tsx"]
        API["main.py::api_chat"]
    end

    subgraph L2["2. 编排层"]
        G1["input_guard"]
        G2["input_semantic_guard"]
        ANA["_analyze_query"]
        DEC{"capabilities"}
        BG["_retrieve_background 物理层无条件检索"]
        INJ["注入 system prompt 企业知识库资料"]
        LOOP["LLM 主循环"]
        G3["output_guard"]
        G4["output_semantic_guard"]
    end

    subgraph L3["3. 数据与上下文层"]
        CHROMA[("ChromaDB 分部门 collection")]
        RJ[("rag_data.json")]
        MEM[("memory.json")]
    end

    subgraph L4["4. 模型层"]
        CHAT["deepseek-chat"]
        FLASH["deepseek-flash"]
        GTE["GTE-small-zh"]
        RERANK["bge-reranker-base"]
    end

    U --> UI
    UI --> API
    API --> G1
    G1 --> G2
    G2 --> ANA
    ANA -.-> CHAT
    ANA --> DEC
    DEC -->|rag_search| BG
    BG --> R1["1. 部门分片判定 best_sim 0.9643 >= 0.82"]
    R1 --> R2["2. 向量路 ChromaDB 21 条"]
    R1 --> R3["3. BM25 路 jieba 14 条"]
    R2 -.-> GTE
    R2 -.-> CHROMA
    R3 -.-> RJ
    R2 --> R4["4. RRF 融合 Top-5"]
    R3 --> R4
    R4 --> R5["5. Reranker 精排 最高分 0.9997"]
    R5 -.-> RERANK
    R5 --> R6["6. Top-8 返回 + RETRIEVED_IDS"]
    R6 --> INJ
    INJ --> LOOP
    LOOP -.-> MEM
    LOOP -.-> FLASH
    LOOP --> G3
    G3 --> G4
    G4 --> API
    API -->|SSE| UI
    UI --> OUT["故障排查答案 + 引用编号 IT-01/IT-06"]

    classDef user fill:#fff3e0,stroke:#ff6f00
    classDef app fill:#e8f5e9,stroke:#4caf50
    classDef orch fill:#e3f2fd,stroke:#1976d2
    classDef data fill:#ffebee,stroke:#d32f2f
    classDef model fill:#f3e5f5,stroke:#7b1fa2
    class U,OUT user
    class UI,API app
    class G1,G2,ANA,DEC,BG,INJ,LOOP,G3,G4 orch
    class CHROMA,RJ,MEM data
    class CHAT,FLASH,GTE,RERANK model
```

**关键点**：

- **唯一走 ChromaDB 的路径**
- **物理层无条件检索**（v2.5.0 起）：不依赖 LLM 决策，保证 context 一定注入
- **6 步检索链**：部门分片 → 向量 + BM25 → RRF → Reranker → Top-8 → 注入 prompt
- **`[RETRIEVED_IDS]` 结构化标记**：供后端可靠提取真实命中 ID

---

## 五、降级路径单图（Fail-Safe 全景）

```mermaid
flowchart TD
    REQ["用户请求进入"]

    subgraph GS["降级路径 1: Guardrails"]
        G_CHECK["input/output semantic_guard"]
        G_TRY["调用审核 LLM deepseek-chat 超时 8s"]
        G_OK["返回 is_safe 判定"]
        G_FAIL["LLM 超时 / 异常 / 网络错误"]
        G_FO["默认 fail-open 放行"]
        G_FC["GUARD_FAIL_MODE=closed 时 fail-closed 拒答"]
    end

    subgraph MS["降级路径 2: 模型回退链"]
        M_FLASH["model=flash deepseek-flash 首选"]
        M_FAIL1["flash 失败"]
        M_CHAT["model=chat deepseek-chat 回退 1"]
        M_FAIL2["chat 失败"]
        M_REASON["model=reasoner deepseek-reasoner 回退 2"]
        M_FAIL3["reasoner 失败"]
        M_ERR["抛出 RuntimeError 返回错误"]
    end

    subgraph RS["降级路径 3: RAG 检索"]
        R_TRY["search_knowledge_v2"]
        R_OK["返回 Top-8 context 正常"]
        R_FAIL["向量 / BM25 / Reranker 异常"]
        R_EMPTY["context 置空"]
        R_REFUSE["LLM 基于空 context 拒答"]
    end

    subgraph DS["降级路径 4: 数据文件"]
        D_TRY["工具读 CSV / SQLite"]
        D_OK["返回数据"]
        D_FAIL["文件不存在 / 解析失败"]
        D_ERR["工具返回错误字符串"]
        D_LLM["LLM 收到错误信息 拒答或提示用户"]
    end

    REQ --> G_CHECK
    G_CHECK --> G_TRY
    G_TRY --> G_OK
    G_TRY --> G_FAIL
    G_FAIL --> G_FO
    G_FAIL --> G_FC
    G_OK --> M_FLASH
    G_FO --> M_FLASH

    M_FLASH --> M_FAIL1
    M_FAIL1 --> M_CHAT
    M_CHAT --> M_FAIL2
    M_FAIL2 --> M_REASON
    M_REASON --> M_FAIL3
    M_FAIL3 --> M_ERR

    M_CHAT --> R_TRY
    R_TRY --> R_OK
    R_TRY --> R_FAIL
    R_FAIL --> R_EMPTY
    R_EMPTY --> R_REFUSE

    R_OK --> D_TRY
    D_TRY --> D_OK
    D_TRY --> D_FAIL
    D_FAIL --> D_ERR
    D_ERR --> D_LLM

    D_OK --> ANS["返回答案 / 数据"]
    D_LLM --> ANS
    R_REFUSE --> ANS
    G_FC --> ANS
    M_ERR --> ANS

    classDef normal fill:#e8f5e9,stroke:#4caf50
    classDef danger fill:#ffcdd2,stroke:#c62828,stroke-width:2px
    classDef terminal fill:#fff3e0,stroke:#ff6f00,stroke-width:2px
    class G_OK,R_OK,D_OK normal
    class G_FAIL,M_FAIL1,M_FAIL2,M_FAIL3,R_FAIL,D_FAIL danger
    class G_FO,G_FC,M_ERR,R_REFUSE,D_LLM,ANS terminal
```

**四条降级路径对照表**：

| # | 触发条件 | 降级行为 | 代码位置 |
|---|---|---|---|
| 1 | 审核 LLM 超时 / 异常 | fail-open 放行 / fail-closed 拒答 | `guardrails.py::_semantic_judge` |
| 2 | 主模型失败 | flash → chat → reasoner → 报错 | `model_router.py::call_with_fallback` |
| 3 | RAG 检索异常 | context 置空，LLM 拒答 | `orchestrator.py::_retrieve_background` |
| 4 | 数据文件读取失败 | 工具返回错误字符串，LLM 转述 | `tools.py::analyze_file` / `aggregate` |

---

## 六、RAG 与 AI 系统交互时序图

```mermaid
sequenceDiagram
    autonumber
    participant U as 用户
    participant F as 前端 Chat.tsx
    participant M as main.py
    participant O as orchestrator.py
    participant G as guardrails.py
    participant R as rag_v2.py
    participant L as LLM DeepSeek
    participant T as tools.py

    U->>F: 输入 query
    F->>M: POST /api/chat (SSE)
    M->>M: 用户状态校验
    M->>O: run_orchestration(session_id, query)

    Note over O: 1 前置管道
    O->>G: input_guard 字符串级
    O->>G: input_semantic_guard LLM 语义审核
    G-->>O: safe / block

    Note over O: 2 语义分析
    O->>L: _analyze_query
    L-->>O: capabilities, specified_format, task_nature

    Note over O,R: 3 物理层无条件检索
    alt capabilities 含 rag_search
        O->>R: _retrieve_background(query)
        R->>R: search_knowledge_v2
        R-->>O: context_text, sources, ids
        O->>O: 注入 system prompt
    end

    Note over O,L: 4 LLM 主循环 最多 8 轮
    loop iteration < 8
        O->>L: chat.completions.create
        L-->>O: tool_calls 或 answer
        alt 有 tool_calls
            O->>T: 执行工具
            T-->>O: 工具结果
            O->>L: 回灌结果
        else 无 tool_calls
            Note over O: 退出循环
        end
    end

    Note over O,G: 5 后处理
    O->>G: output_guard 字符串脱敏
    O->>G: output_semantic_guard LLM 语义审核
    O->>O: 图片处理 + source_prefix

    O-->>M: SSE chunk
    M-->>F: data: ...
    F-->>U: 打字机渲染
```

---

## 七、每层职责与代码文件映射表

| 层 | 职责 | 代码文件 | 关键函数 |
|---|---|---|---|
| 应用层 | 用户界面、API 端点 | `frontend/src/pages/Chat.tsx`、`common/main.py` | `api_chat` / `api_kb_index` |
| 编排层 | 安全过滤、能力筛选、检索调用、主循环 | `common/orchestrator.py`、`common/tools.py`、`common/guardrails.py`、`common/model_router.py` | `run_orchestration` / `_retrieve_background` / `call_with_fallback` |
| 数据与上下文层 | 检索、融合、精排、记忆 | `common/rag_v2.py`、`common/memory.py` | `search_knowledge_v2` / `index_document_v2` |
| 模型层 | 向量化、精排、LLM | `common/rag_v2.py`（模型加载）、`common/orchestrator.py`（LLM 调用） | GTE-small-zh / bge-reranker-base / DeepSeek |
| 基础设施层 | 持久化、部署 | `uploads/`、`start.sh`、`.github/workflows/deploy.yml` | ChromaDB / Render Disk |

---

## 八、术语表

| 术语 | 含义 |
|---|---|
| **RRF** | Reciprocal Rank Fusion，将多路检索结果按排名倒数融合 |
| **Reranker** | Cross-Encoder 精排模型，对 RRF 候选池二次打分 |
| **物理层无条件检索** | v2.5.0 起设计，不依赖 LLM 决策，主流程开始即执行 |
| **部门分片** | 按部门分 ChromaDB collection，检索时先算 best_sim 选片 |
| **`[RETRIEVED_IDS]`** | 结构化标记，后端可靠提取真实命中文档 ID |
| **fail-open** | 审核服务失败时放行，保证可用性 |
| **fail-closed** | 审核服务失败时拒绝，保证安全性 |
| **capabilities** | `_analyze_query` 输出的能力候选列表，如 `['rag_search']` |

---

## 九、版本历史

| 版本 | 日期 | 变更 |
|---|---|---|
| v3.0.0 | 2026-10-14 | 首版定稿：总览图 + 3 路径详图 + 降级路径图 + 时序图 |