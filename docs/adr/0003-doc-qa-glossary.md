# 文档问答助手 - 术语表

## 核心概念

| 术语 | 英文 | 定义 |
|------|------|------|
| 文档分块 | Chunking | 将长文档拆分为小块文本，便于向量检索和精准匹配 |
| 嵌入向量 | Embedding | 将文本转换为高维数值向量，用于语义相似度计算 |
| 向量检索 | Vector Search | 在向量空间中查找与查询最相似的文档块 |
| RAG | Retrieval-Augmented Generation | 检索增强生成，结合检索结果优化 LLM 输出 |
| 流式响应 | Streaming Response | 实时逐字/逐句返回 AI 生成内容 |
| 混合检索 | Hybrid Retrieval | 组合多路检索（向量、BM25、章节/实体/事件），再融合排序 |
| 查询规划 | Query Planner | LLM 将原问题拆解为子查询与检索线索 |
| BM25 | Best Matching 25 | 基于词频统计的稀疏检索算法，兜底关键词召回 |
| 倒数排名融合 | RRF | Reciprocal Rank Fusion，按各路的排名倒数加权融合去重 |
| 重排 | Reranking | 对融合后的候选块用 LLM 重新排序 |
| 证据包 | Evidence Pack | 检索得到的候选证据块及其检索元数据的集合 |
| 证据循环 | Evidence Agent | 判断证据是否足够，不足则补充检索，直至足够或达迭代上限 |

## 技术栈术语

| 术语 | 用途 |
|------|------|
| LlamaIndex | Python AI 框架，简化 LLM + RAG 开发 |
| Milvus | 开源向量数据库，高性能向量存储与检索 |
| FastAPI | Python Web 框架，支持异步和自动 OpenAPI 文档 |
| pdfplumber | PDF 文本提取库 |
| python-docx | Word 文档解析库 |
| SSE | Server-Sent Events，服务端推送技术 |
| text-embedding-3-small | OpenAI 最新嵌入模型，高性价比 |
| LangGraph | 图状状态机框架，用于实现证据循环 Agent |
| jieba | 中文分词库，用于 BM25 分词与实体词性标注 |

## 项目术语

| 术语 | 定义 |
|------|------|
| Document | 用户上传的文档实体 |
| ChatSession | 一个对话会话，包含多条消息 |
| ChatMessage | 单条对话消息 (user/assistant) |
| KnowledgeBase | 文档知识库，即上传文档的集合 |
| 章节锚点 | Chapter Anchor | 章节标题到 chunk 区间的映射，支撑按章节号检索 |
| 实体锚点 | Entity Anchor | 专名实体（人名/地名）与其出现 chunk 的映射 |
| 事件锚点 | Event Anchor | LLM 抽取的剧情事件与其相关 chunk 的映射 |
