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

