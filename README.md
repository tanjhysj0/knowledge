# DocQA - 文档问答助手

基于 RAG (检索增强生成) 的文档问答系统，支持上传 TXT/MD/PDF/DOCX 文档进行智能问答。

## 技术栈

- **前端**: React (Vite) + TypeScript
- **后端**: Python 3.11 + FastAPI
- **AI 框架**: LlamaIndex
- **向量存储**: PostgreSQL pgvector（#71 自 Milvus 迁移）
- **关系数据库**: PostgreSQL
- **嵌入模型**: OpenAI text-embedding-3-small
- **LLM**: OpenAI GPT-4o-mini

## 快速启动

### 1. 启动外部依赖

```bash
docker-compose up -d
```

验证服务启动：

- PostgreSQL (pgvector): localhost:55432

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 填入外部依赖连接信息；LLM 模型配置在设置页维护
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
├── docker-compose.yml   # 外部依赖
└── .env.example         # 环境变量模板
```

## 开发说明

- 文档文件存储在 `backend/uploads/` 目录
- 所有外部依赖通过 docker-compose 统一管理
- 支持的文档格式: TXT, MD, PDF, DOCX
