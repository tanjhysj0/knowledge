# ADR-0008: 已实现功能与技术架构总览

## 状态
已批准（#84）

## 背景
README 已覆盖「从 git clone 到配置 LLM 并开始使用」的完整流程，但缺少系统全貌：已实现哪些功能、整体架构如何组织。沉淀本 ADR 作为功能与技术架构的统一索引，README「相关文档」节指向本文件。

## 已实现功能

### 文档管理

- 上传 TXT/MD/PDF/DOCX，上传与索引分离（#63）：响应秒级返回，索引走后台任务
- 索引进度与状态：`pending → processing → ready / failed`，管理端轮询展示
- 解析失败标记 `failed` 并展示错误信息，可手动重试（#65）
- 文档编辑（标题/正文）、删除、封面管理（#47）
- 分页列表、全量视图（含未就绪文档）

### RAG 问答

- 混合检索管线（#66）：Dense / BM25 / 实体 / 事件 / 章节 / 图谱 六路并行检索
- Query Planner 查询计划（#79）：动态策略列表、意图解析、子查询
- RRF 融合 + 重排（Reranker），证据包（Evidence Pack）构建
- Evidence Agent 证据循环（#66）：证据不足自动补充检索
- 非流式（`/api/v1/chat`）与流式（`/api/v1/chat/stream`）双端点
- v1 全量策略白名单、v2 子集（dense+bm25）双版本（#75/#76/#77）
- SSE 事件契约：`thinking` / `message` / `evidence` / `done` / `error`
- 多轮对话上下文、思考内容折叠 UI、检索来源展示

### 会话与绑定

- 会话创建/列表/消息历史/删除（#36），`X-Client-Id` 客户端空间隔离
- 小说 ↔ 会话绑定（#52）：点击卡片恢复既有会话，删除小说保留历史

### 知识图谱（GraphRAG，#80）

- LLM 实体-关系三元组抽取，`graph_entities` / `graph_relations` 存储
- 图一跳邻居检索接入混合管线，多节点回源去重

### LLM 模型管理（#68/#69）

- 模型 CRUD、默认模型标记、Provider 模型列表拉取
- preflight 配置守卫（未配置返回 503）、运行时配置热更新
- `llm_models` 表为唯一事实源（不再读取环境变量）

### 部署与测试基建

- 全栈容器化（#82/#83/#84）：postgres / embedding / backend / frontend 四服务
- 健康检查依赖链（`condition: service_healthy`），nginx 反代 SSE 不缓冲
- E2E 全程 Mock：`X-E2E-Test` 请求头切换 MockLLMProvider，可控 thinking/错误/证据判定（#45/#66）

## 技术架构

### 总体结构

前后端分离 + 独立嵌入服务，全栈容器化编排（docker-compose 四服务）。

```
浏览器
  │
  ▼
frontend (nginx :8080) ──/api 反代──▶ backend (FastAPI :8000)
                                        │
              ┌─────────────────────────┼─────────────────────┐
              ▼                         ▼                     ▼
      embedding (Infinity      postgres (pgvector     uploads/
       bge-m3 :7997)            :5432 文档/会话/模型/图谱/向量)   文件存储
```

### 前端（React + Vite + TypeScript）

- 页面：首页书架（/）、聊天（/chat）、管理端（/admin：文档管理、索引进度、模型设置 /admin/settings）
- 流式对话：SSE 解析（thinking 折叠、message 流式渲染、sources/evidence 展示）
- 容器：多阶段构建，nginx 托管静态资源并反代 `/api`（SSE 不缓冲）

### 后端（FastAPI + SQLAlchemy async + LlamaIndex）

- 路由分层：`api/router.py` 声明式注册，v1（全量检索策略）/ v2（dense+bm25 子集）双端点
- 服务层：`services/` 按领域划分（documents / chat / conversations / models / graph / rag）
- 文档处理：解析（parser）→ 分块（chunker）→ 嵌入 → 向量入库，后台任务异步执行，状态机 `pending → processing → ready / failed`

### 检索管线（混合 RAG）

```
Query Planner → 并行检索（dense / bm25 / entity / event / chapter / graph）
              → RRF 融合 → Reranker 重排 → Evidence Agent 证据循环 → 回答
```

- 检索器自描述 strategy，装配层按 settings 开关组装（依赖注入，可替换）
- Dense：pgvector HNSW/COSINE；BM25：PostgreSQL 全文检索；图谱：一跳邻居回溯
- 证据包随 SSE `evidence` 事件下发，来源以 `doc_<id>` 引用

### 存储

- **PostgreSQL（唯一数据库）**：文档元数据、会话/消息、`llm_models`（LLM 配置唯一事实源）、`graph_entities` / `graph_relations`、向量（pgvector）
- **文件系统**：`backend/uploads/` 文档正文与封面（bind mount 持久化）

### LLM 与嵌入

- 嵌入：Infinity 服务常驻 bge-m3（OpenAI 兼容 `/embeddings`），后端进程内零模型加载
- LLM：OpenAI 兼容协议（默认 DeepSeek）与 Anthropic 双 provider，工厂 + 运行时单例（写路径热更新），preflight 守卫
- Mock：`X-E2E-Test` 请求头 → MockLLMProvider（E2E 零真实调用），`X-E2E-Mock-Thinking/LLM-Error/Judge` 控制行为

### 部署（#82/#83/#84）

- 依赖链：`postgres → embedding → backend → frontend`，全部 `condition: service_healthy`
- 端口：8080（前端）/ 8000（API）/ 7997（嵌入）/ 55432（宿主机 postgres）
- 数据卷：`postgres_data`；uploads 为宿主目录 bind mount

### 测试

- 单元：pytest（后端，mock 全部外部依赖）
- E2E：Playwright + 真实后端（MockLLMProvider），前置 project 保证 preflight 契约
- 一键：`make test`（单元 627 + E2E 90）

## 后果

- 本 ADR 是功能与架构的单一索引源；新增功能模块或架构调整时同步更新本文件
- README 只保留上手流程，细节与全貌以本 ADR 为准
