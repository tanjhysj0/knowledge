# 文档问答助手 - 架构决策记录

## ADR-001: 整体架构决策

### 状态：已批准

### 背景
用户需要一个 Web 应用，允许上传文档（TXT/MD/PDF/DOCX），然后基于文档内容进行多轮对话问答，AI 优先检索文档内容，找不到答案时补充外部知识。无需用户登录，支持文档管理。

### 决策

| 维度 | 决策 |
|------|------|
| 前端 | React |
| 后端 | Python + FastAPI |
| AI 框架 | LlamaIndex |
| 文档解析 | pdfplumber (PDF), python-docx (DOCX) |
| 向量数据库 | Milvus |
| 嵌入模型 | OpenAI text-embedding-3-small（或可切换） |
| LLM | OpenAI GPT-4o-mini（可配置） |

### 架构图

```
┌─────────────────────────────────────────────────────────────┐
│                        React Frontend                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │  文档上传    │  │  文档列表    │  │  多轮对话窗口    │   │
│  └──────────────┘  └──────────────┘  └──────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     FastAPI Backend                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ /upload      │  │ /documents   │  │ /chat            │   │
│  │ /documents/  │  │ /documents/  │  │ /chat/stream     │   │
│  │   {id}       │  │   {id}/delete│  │                  │   │
│  └──────────────┘  └──────────────┘  └──────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
      ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
      │  Milvus      │ │  文件存储    │ │  LlamaIndex  │
      │  (向量)      │ │  (本地磁盘)  │ │  (检索引擎)  │
      └──────────────┘ └──────────────┘ └──────────────┘
```

### 核心流程

1. **文档上传流程**
   ```
   用户上传 → FastAPI 接收 → 解析内容(TXT/MD/PDF/DOCX) → 文本分块
           → 嵌入向量(Embedding) → 存储至 Milvus + 原始文件落盘
   ```

2. **问答流程**
   ```
   用户提问 → 检索 Milvus(文档优先) → 如无结果调用外部 LLM
           → 组装 Prompt → 返回流式响应 → 前端展示
   ```

### 风险与对策

| 关系数据库 | PostgreSQL（Docker Compose 启动） |

---

## ADR-002: API 设计

### 端点设计

```
POST   /api/documents/upload     # 上传文档
GET    /api/documents           # 列出所有文档
DELETE /api/documents/{id}      # 删除文档
POST   /api/chat                # 发送消息(非流式)
POST   /api/chat/stream         # 流式对话
DELETE /api/chat/history        # 清除对话历史
```

### 请求/响应示例

**POST /api/documents/upload**
```json
// Request: multipart/form-data
// file: [binary]

// Response:
{
  "id": "uuid",
  "filename": "example.pdf",
  "size": 102400,
  "created_at": "2026-08-05T10:00:00Z"
}
```

**POST /api/chat/stream**
```json
// Request:
{
  "message": "文档主要内容是什么？",
  "document_ids": ["uuid1", "uuid2"],  // 可选，指定文档范围
  "use_external_knowledge": true      // 文档无答案时是否用外部知识
}

// Response: Server-Sent Events (SSE)
data: {"content": "根据", "delta": "根据"}
data: {"content": "文档内容，", "delta": "文档内容，"}
data: {"content": "[DONE]"}
```

---

## ADR-003: 数据模型

### Document
```python
class Document:
    id: str              # UUID
    filename: str        # 原始文件名
    file_path: str       # 存储路径
    file_type: str       # txt/md/pdf/docx
    size: int            # 字节数
    chunk_count: int     # 分块数量
    created_at: datetime
```

### ChatMessage
```python
class ChatMessage:
    id: str
    role: str            # user/assistant
    content: str
    document_ids: list[str]  # 引用的文档 ID
    created_at: datetime
```

### 存储策略
- **Milvus Collection**: 存储文档向量，字段包括 `id`, `document_id`, `content`, `chunk_index`
- **本地文件系统**: `uploads/` 目录存储原始文件
- **PostgreSQL**: 存储 Document 和 ChatMessage 元数据(Docker Compose 启动)
