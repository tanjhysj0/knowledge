# DocQA - 文档问答助手

基于 RAG (检索增强生成) 的文档问答系统，支持上传 TXT/MD/PDF/DOCX 文档进行智能问答。

## 技术栈

- **前端**: React (Vite) + TypeScript
- **后端**: Python 3.11 + FastAPI
- **AI 框架**: LlamaIndex
- **向量存储**: PostgreSQL pgvector（#71 自 Milvus 迁移）
- **关系数据库**: PostgreSQL
- **嵌入模型**: BAAI/bge-m3（Infinity 独立服务，容器内常驻）
- **LLM**: DeepSeek（OpenAI 兼容协议，可在管理端配置其他模型）

## 快速开始（从零到用）

### 1. 环境要求

- Docker（含 compose 插件，一条命令启动全栈所需）
- `make`（可选，直接使用 docker compose 命令亦可）

### 2. 获取代码

```bash
git clone https://github.com/tanjhysj0/knowledge.git
cd knowledge
```

### 3. 一键启动全栈

```bash
make docker-up    # 等价于 docker compose up -d --build
```

构建并后台启动全部 4 个服务（postgres → embedding → backend → frontend），
服务按健康检查依赖链顺序启动，无连接竞态。

### 4. 配置 LLM（DeepSeek）

打开 **http://localhost:8080/admin/settings**（模型管理页），新增模型：

| 字段 | 值 |
|------|-----|
| Provider | OpenAI（DeepSeek 兼容 OpenAI 协议） |
| Base URL | `https://api.deepseek.com` |
| 模型名 | `deepseek-chat`（可点击拉取模型列表） |
| API Key | DeepSeek 开放平台申请的 key |
| 设为默认 | 勾选 |

保存后即可问答。未配置默认模型 key 时，聊天接口返回 503「OpenAI API Key 未配置」。

### 5. 开始使用

- 管理端 **http://localhost:8080/admin**：上传文档、查看索引状态（就绪后可问答）、管理文档
- 首页 **http://localhost:8080/**：小说列表书架，点击卡片进入问答（/chat）

## 常用操作（容器部署）

| 操作 | 命令 |
|------|------|
| 启动（含重建） | `make docker-up` |
| 停止 | `make docker-down` |
| 查看日志 | `make docker-logs` |
| 仅构建镜像 | `make docker-build` |

- 前端入口：http://localhost:8080（nginx 服务，`/api` 自动反代后端，SSE 不缓冲）
- 后端 API 直连：http://localhost:8000
- 数据持久化：`postgres_data` 卷（文档、封面、会话、模型配置），容器重建不丢

## 本地开发

### 1. 启动外部依赖

```bash
docker compose up -d postgres embedding
```

验证服务启动：

- PostgreSQL (pgvector): localhost:55432
- Embedding (Infinity/bge-m3): localhost:7997

### 2. 配置环境变量

```bash
cd backend && cp .env.example .env
# 默认值已适配 docker compose 外部依赖，一般无需修改；LLM 配置在管理端维护
```

### 3. 启动后端

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 4. 启动前端

```bash
cd frontend
npm install
npm run dev
```

访问 http://localhost:5173 开始使用。

## API 端点

| 方法 | 路径 | 描述 |
|------|------|------|
| POST | /api/documents/upload | 上传文档 |
| GET | /api/documents | 列出文档 |
| DELETE | /api/documents/{id} | 删除文档 |
| POST | /api/v1/chat | 聊天问答（非流式） |
| POST | /api/v1/chat/stream | 流式对话 |

## 项目结构

```
.
├── backend/              # FastAPI 后端
│   ├── app/
│   │   ├── api/         # API 路由
│   │   ├── core/        # 核心配置
│   │   ├── models/      # 数据模型
│   │   ├── services/    # 业务逻辑
│   │   └── main.py      # 应用入口
│   └── requirements.txt
├── frontend/            # React 前端
│   ├── src/
│   │   ├── components/  # 组件
│   │   ├── pages/       # 页面
│   │   ├── services/   # API 调用
│   │   └── App.tsx
│   └── package.json
├── docker-compose.yml   # 全栈编排（postgres/embedding/backend/frontend）
└── .env.example         # 环境变量模板
```

## 相关文档

- [ADR-0008: 已实现功能与技术架构总览](docs/adr/0008-features-architecture.md) - 功能清单 / 总体结构 / 检索管线 / 存储 / LLM 与嵌入 / 部署 / 测试

## 开发说明

- 文档文件存储在 `backend/uploads/` 目录
- 所有外部依赖通过 docker-compose 统一管理
- 支持的文档格式: TXT, MD, PDF, DOCX
- LLM 配置在管理端模型页维护（`llm_models` 表为唯一事实源），无需编辑 .env
