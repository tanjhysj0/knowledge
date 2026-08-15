# E2E 测试

本目录包含前端设置的端到端测试。

## 前置条件

运行 E2E 测试前需要启动后端服务：

```bash
# 1. 启动 docker-compose (PostgreSQL/pgvector)
cd /Users/jason/go/src/knowledge
docker-compose up -d

# 2. 启动后端 API
cd backend
export POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=docqa POSTGRES_PASSWORD=docqa POSTGRES_DB=docqa
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 3. 启动前端并运行测试
cd frontend
npx playwright test
```

## 测试说明

这些是**真正的 E2E 测试**，不走 mock，直接调用真实后端 API。
