# ADR-0005: 问答管线升级为混合检索 + Evidence Agent 证据循环

## 状态
已批准（#66）

## 背景
原问答链路是「单一 dense 向量检索 → 拼 RAG prompt → LLM 作答」：

1. 小说类问答中，dense 检索对实体、事件、章节号等结构化线索召回不稳定；
2. 检索结果一次定型，无法判断「证据是否足够回答」，也无法按需补充检索；
3. 对外 SSE 契约（thinking/message/done/error）与请求体已稳定，前端不能破坏性变更。

#66 要求：Query Planner → 五路混合检索 → RRF 融合 → LLM 重排 → Evidence Pack → Evidence Agent 证据循环，同时保持对外契约兼容、服务稳定降级。

## 决策

### 1. BM25 采用「PG 存 jieba tokens + 应用层算分」
否决 Milvus sparse vector / BM25 function（受 pymilvus 2.4 与部署复杂度限制），也否决 PostgreSQL zhparser 中文全文检索（需额外扩展安装）。方案：索引构建时把每个 chunk 的 jieba 分词结果存入 `bm25_chunks` 表（JSONB），检索时按 `document_ids` 加载候选 chunk，应用层算标准 BM25（K1=1.5, B=0.75, idf 平滑）。小说级语料单次加载规模可控，且逻辑可单测（纯函数）。

### 2. 三路元数据检索器（章节/实体/事件）
- **章节**：正则解析「第X章/节/回/卷 / Chapter N」标题行（确定性，无需 LLM），映射为 chunk 区间锚点 `chapter_anchors`。
- **实体**：jieba.posseg 词性标注提取 nr/ns/nz 专名，仅保留全书出现 ≥2 次的实体（只出现一次的专名多为词性误标，同时避免索引表行数爆炸）。否决 LLM 抽取实体（成本与延迟不可控，专名任务用词性标注足够）。
- **事件**：LLM 抽取剧情事件（每 10 chunk 一批）；LLM 未配置时直接跳过，该路检索自动降级为空。

### 3. RRF 融合 + LLM 重排
五路结果按 (document_id, chunk_index) 去重后做 Reciprocal Rank Fusion（k=60），取 top-N；重排由 LLM 完成（`[RERANK]` marker 协议），LLM 不可用/输出非法时**直通**融合顺序（不阻断问答）。

### 4. Evidence Agent 用 LangGraph StateGraph 实现
循环：judge（证据是否足够）→ 不足则 plan_more_queries（生成补充查询）→ retrieve → 再 judge，受 `max_iterations`（默认 2）上限约束，达上限强制作答并在 prompt 中附「证据有限」提示。降级策略（fail-open）：空证据不调 LLM 直接判不足；judge LLM 失败视为足够；plan/retrieve 失败跳过继续。

### 5. 对外契约兼容优先
- 请求体不变；`sources` 仍是 `["doc_N", ...]` 数组；
- SSE 新增**可选** `event: evidence`（证据包摘要），done 事件扩展结构化 `evidence` 字段（向下兼容：旧字段不变）；
- 前端零改动，`used_external` 语义保持「证据包完全为空」；
- 文档上传管线追加索引构建，**失败不阻断上传**（仅记日志），存量文档提供 `scripts/rebuild_metadata_indexes.py` 重建 CLI。

### 6. E2E mock 按 prompt 标记驱动
`MockLLMProvider` 识别 `[QUERY_PLAN]` / `[JUDGE_EVIDENCE]` / `[PLAN_QUERIES]` / `[EXTRACT_EVENTS]` 标记返回确定性响应；planner 的 sub_queries 回填原问题，保证 dense 检索行为与旧单路一致。新增 `x-e2e-mock-judge: insufficient` 头控制 judge 判定，E2E 覆盖证据循环分支。

## 后果

**正面**
- 五路检索互补：章节号/实体/事件结构化线索可稳定命中，BM25 兜底关键词召回；
- 证据循环按需补充检索，证据不足时带提示作答，避免硬答；
- 全部检索器异常降级为空列表、LLM 组件 fail-open，单点故障不阻断问答；
- 契约兼容，前端零改动；新增单测 100+（含 Evidence Agent 三分支）、E2E evidence 事件契约。

**代价**
- 上传管线新增索引构建耗时（实体抽取为 CPU 密集，事件抽取依赖 LLM），失败时索引可能不完整（可重建）；
- 每次问答新增 planner/judge 等 LLM 调用，延迟上升（`evidence_max_iterations` 可控）；
- 检索行为从单一路径变为管线，调试需理解 planner → fusion → agent 全链路。

**遗留**
- 五路权重目前等权，未做权重调优；后续可引入点击/反馈数据调权。
- 实体索引基于 jieba 词典，专名边界随词典版本变化。
