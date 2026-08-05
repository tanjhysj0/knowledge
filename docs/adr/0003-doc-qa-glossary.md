# 文档问答助手 - 术语表

## 核心概念

| 术语 | 英文 | 定义 |
|------|------|------|
| 文档分块 | Chunking | 将长文档拆分为小块文本，便于向量检索和精准匹配 |
| 嵌入向量 | Embedding | 将文本转换为高维数值向量，用于语义相似度计算 |
| 向量检索 | Vector Search | 在向量空间中查找与查询最相似的文档块 |
| RAG | Retrieval-Augmented Generation | 检索增强生成，结合检索结果优化 LLM 输出 |
| 流式响应 | Streaming Response | 实时逐字/逐句返回 AI 生成内容 |

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

## 项目术语

| 术语 | 定义 |
|------|------|
| Document | 用户上传的文档实体 |
| ChatSession | 一个对话会话，包含多条消息 |
| ChatMessage | 单条对话消息 (user/assistant) |
| KnowledgeBase | 文档知识库，即上传文档的集合 |
