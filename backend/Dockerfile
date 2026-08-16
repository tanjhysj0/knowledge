# 后端 API 镜像（#82）：FastAPI 应用纯容器部署。
# 依赖 postgres（pgvector）与 embedding（Infinity）由 compose 网络内服务名互访。
FROM python:3.12-slim

WORKDIR /app

# 先装依赖层（requirements 不变时复用镜像层缓存）。
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再复制应用代码（.dockerignore 排除 .env/uploads/测试产物）。
COPY app ./app

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# 端口与 host 与 config.py 默认值一致；运行时配置全部来自环境变量。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
