# 前端镜像（#83）：多阶段构建，node 产出静态资源，nginx 提供服务。
# /api 反代 backend 容器（compose 网络内服务名），详见同目录 nginx.conf。
FROM node:22-alpine AS build

WORKDIR /app

# 先装依赖层（package-lock.json 不变时复用镜像层缓存）。
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# 再复制源码构建（.dockerignore 排除 node_modules/dist/e2e 等）。
COPY . .
RUN npm run build

FROM nginx:1.27-alpine

# 自定义站点配置：静态文件 + /api 反代 backend（SSE 不缓冲）。
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

# 官方镜像默认 CMD 即 nginx 前台运行，无需覆盖。
