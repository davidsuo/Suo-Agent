# Backlog

> 记录已知问题、技术债、待优化项，不阻塞当前 release，按优先级排期。

---

## P2 - RAG 检索质量

### B-001：Reranker 未捕获"电源灯亮/不亮"等极细粒度区分词

- **发现版本**：`rag/v2.6.9`（US-05 验收评估）
- **失败样本**：Q50「X1 2024 开机黑屏怎么办，电源灯是亮的」
- **失败表现**：
  - `ground_truth = [TS-02, TS-03]`
  - `retrieved_ids = [TS-01, TS-03, TS-06, TS-02, ...]`
  - `precision@1 = 0.0`，`mrr = 0.5`
  - Reranker 对 TS-01（"开机没反应、电源灯**不亮**"）打出 0.9994 高分
- **根因**：`common/rag_v2.py` 中 Reranker 精排文本截断长度 250 字符。TS-01 和 TS-03 前 250 字符高度重叠（都是"ThinkPad X1 Carbon 2024 开机黑屏"类描述），关键区分词"电源灯亮/不亮"被截断在外。
- **备选方案**：
  - A. 精排文本截断长度 250 → 500（成本：Reranker 耗时可能翻倍，query 总耗时从 ~0.9s → ~1.2s，不保证修复）
  - B. Query 侧属性提取：把"电源灯亮/不亮"、"机器转/不转"等作为结构化过滤条件，在 RRF 融合前先缩小候选池
  - C. 数据集层面：Q50 与 Q13 描述高度重叠但 gt 不同，考虑合并或重新设计
- **优先级**：P2（1/41 positive 失败，错误率 2.4%，不影响 US-05 验收）
- **负责**：待定

---


## P2 - 前端 lint 技术债

### B-002：Chat.tsx / App.tsx 共 52 条 ESLint 错误待清理

- **发现版本**：`ci/v1.0.0`（US-09 CI 补齐 lint 检查）
- **现状**：`npm run lint` 报告 52 errors，主要分类：
  - `@typescript-eslint/no-explicit-any` ~30 条（类型化重构）
  - `@typescript-eslint/no-unused-vars` ~10 条（清理未使用变量）
  - `react-hooks/immutability` ~5 条（React Compiler 新规则）
  - `prefer-const` / `no-useless-escape` 等 ~7 条（风格）
- **处理策略**：本轮 CI 前端 lint 步骤以 `continue-on-error: true` 上线，规则在 `eslint.config.js` 中放宽，不阻塞部署。
- **下一轮目标**：清理 52 条 → 0，然后：
  1. `eslint.config.js` 恢复严格规则
  2. `deploy.yml` 前端 lint 步骤改为 `continue-on-error: false`
- **优先级**：P2
- **负责**：待定

---

### B-003：前端部署后缓存问题

- **发现版本**：US-10（部署后 index.html 缓存）
- **现象**：每次部署后，用户访问站点看到旧版本，需 Ctrl+Shift+R 强制刷新
- **根因**：`index.html` 无 Cache-Control 响应头，浏览器按默认策略缓存
- **修复**：`common/main.py` 给 index.html 加 no-cache，给 /assets/* 加 immutable
- **状态**：已修复（US-10）

### B-004：重复提问时 LLM 输出"元对话"影响 Faithfulness

- **发现版本**：US-12 RAGAS 评估
- **现象**：test_set 中有 5 组语义重复问题。LLM 检测到历史有相同问题后，
  输出"你已经问过 N 次"、"不再重复"等元对话，导致 RAGAS Faithfulness 被判 0
- **影响**：Faithfulness 从预估 0.65+ 掉到 0.42
- **决策待定**：
  - 方案 A：LLM 遇到重复问题应正常回答（改 SYSTEM_PROMPT）
  - 方案 B：LLM 应简短提示"答案同上"（保持现状，但测试集应去重）
  - 方案 C：测试集层面去掉重复，不再测这类边界
- **优先级**：P2（产品决策，非技术 bug）


## P3 - 观察期

### B-004：RAGAS Faithfulness 0.58，观察期

- **发现版本**：`rag/v2.7.0`（US-12 RAGAS 评估）
- **现状**：41 条 positive 样本的 Faithfulness = 0.5823
- **根因**：LLM 在知识库答案后追加"通用经验/行业常识"，这些内容不在检索上下文中，被 RAGAS 判为"不忠实"
- **已尝试**：
  - 修复 `max_tokens`（无变化，说明不是截断问题）
  - Session 隔离（0.42 → 0.58，**+0.16**，确认历史污染是主因之一）
- **未做方案**（产品决策暂不做）：
  - A：SYSTEM_PROMPT 约束"仅基于上下文"
  - B：扩充知识库收纳"通用建议"
  - C：接受现状
- **观察期内的关注信号**：
  1. 云端 Faithfulness 是否稳定在 0.58 附近
  2. 用户反馈"回答太短 / 没给建议" → 倾向走 B/C
  3. 用户反馈"答案有编造内容" → 倾向走 A
  4. 业务量增长后指标是否恶化
- **决策机制**：等实际信号出现后再动，不预先加规则
- **优先级**：P3

### B-005：清技术债、补历史、新方向

- 候选 A：清技术债
项	内容	预估
B-001	Reranker 未捕获"电源灯亮/不亮"（US-05 遗留 Q50）	1-2 小时
B-002	前端 52 条 lint 债，恢复严格规则	2-3 小时
B-003	（如果 US-10 后记入）前端缓存监控	低

- 候选 B：补历史
项	内容
US-01 ~ US-04 补录	你 Excel 里的正式文本，补到 USER_STORIES.md
docs/CONTRACT.md 更新	本轮学到的"AI 原生"原则沉淀为契约

- 候选 C：新方向
项	内容
观察云端运行	让 Render 跑几天，看 US-08 清历史后是否还有"旧数据复述"
on-prem 部署验证	本地 on-prem 环境跑一遍 US-05 ~ US-11
新用户故事	你手上还有哪些待做
RAG 评估体系扩展	加入多轮对话、工具调用正确率等新指标
