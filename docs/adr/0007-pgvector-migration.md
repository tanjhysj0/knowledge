# ADR-0007: 向量存储从 Milvus 迁移至 PostgreSQL（pgvector）

## 状态
已批准（#71）

## 背景
dense 向量存储在 Milvus standalone（依赖 etcd + minio 三件套）：

1. 部署侧复杂度高：三个容器、专用 network、两个数据卷，本地/CI 启动慢、资源占用大；
2. 存储侧数据割裂：全文与 chunk 内容仅存于向量库与文件系统，PostgreSQL 无法完整还原解析后文本；
3. 项目实际语料规模为小说级（单本几百到几千 chunk），无需专用向量库的吞吐与扩展能力。

#71 要求：dense 向量存储迁至 PostgreSQL（pgvector），彻底移除 Milvus 全部基础设施与依赖，保持 insert / delete_by_document_id / search 公开契约与问答链路（sources、证据包、SSE 事件）行为不变。

## 决策

### 1. 向量存储：`vector_chunks` 表 + pgvector HNSW/COSINE 检索
每个 chunk 一行（`document_id` / `chunk_index` / `content` / `embedding vector(dim)`），启动时 `CREATE EXTENSION vector` 并建 `USING hnsw (embedding vector_cosine_ops)` 索引。检索在 PG 内用 `<=>` 算子求余弦距离，返回 `1 - distance` 作为相似度——与迁移前 pymilvus COSINE ``distance``（越大越相关）语义一致，上层 0.5 阈值过滤与 RRF 融合零改动。维度自适应保留：表维度与 `settings.embedding_dim` 不一致时 drop 重建。

### 2. 全文入库：`document_texts` 表
解析后的全文随上传索引写入（幂等 upsert，每本小说一行），删除小说时随向量级联清理；可从库中完整还原解析后文本。

迁移后存量 ready 小说的 dense 向量与全文由 `scripts/rebuild_dense_indexes.py` 重建补齐（#72）：重新解析原文 → 全文入库 → 分块 → 重嵌入 → 写入 `vector_chunks`；默认跳过已有向量的小说，`--all` 强制重建、`--id` 指定单本。

### 3. 连接策略：同步 psycopg2 独立连接
`VectorStoreService` 保持同步接口（调用方在 executor 线程中调用），使用 psycopg2 驱动同步 engine，与既有调用点（DenseRetriever 检索、后台索引、删除清理）零改动衔接。主业务链路继续走 asyncpg 异步会话，两者互不干扰。

## 否决方案
- **换用 asyncpg 异步化 VectorStoreService**：需改写 DenseRetriever 与 documents 删除路径的全部调用点，契约破坏大；同步 psycopg2 已足够覆盖小说级并发。
- **继续保留 Milvus**：部署三件套与双库数据割裂问题无法解决。
- **pgvector IVF 索引**：建索引需要先有数据，增量流程复杂；HNSW 构建即用且精度足够。
