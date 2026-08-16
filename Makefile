.PHONY: help test unit-test e2e-test test-all install install-backend install-frontend docker-build docker-up docker-down docker-logs services-up services-down

# Default target
help:
	@echo "Available targets:"
	@echo "  make install         - Install all dependencies"
	@echo "  make install-backend - Install backend dependencies"
	@echo "  make install-frontend - Install frontend dependencies"
	@echo "  make test            - Run all tests (unit + e2e)"
	@echo "  make unit-test       - Run backend unit tests (pytest)"
	@echo "  make e2e-test        - Run frontend E2E tests (Playwright)"
	@echo "  make docker-up       - Build & start full stack (postgres/embedding/backend/frontend)"
	@echo "  make docker-down     - Stop full stack"
	@echo "  make docker-logs     - Tail logs of all services"
	@echo "  make docker-build    - Build all images only"
	@echo "  make services-up     - Alias of docker-up"
	@echo "  make services-down   - Alias of docker-down"

# Install all dependencies
install: install-backend install-frontend
	@echo "All dependencies installed"

# Install backend dependencies
install-backend:
	cd backend && pip install -r requirements.txt -q

# Install frontend dependencies
install-frontend:
	cd frontend && npm install

# Run all tests
test: unit-test e2e-test

# Run backend unit tests
unit-test:
	cd backend && python3 -m pytest tests/ -v --cov=app --cov-report=term-missing

# Run frontend E2E tests (Playwright)
# 默认串行（--workers=1）以避免跨文件会话/文档状态污染。如需开启并行可
# 用 `make test E2E_WORKERS=N`。
ifndef E2E_WORKERS
E2E_WORKERS := 1
endif
e2e-test:
	cd frontend && npx playwright test --workers=$(E2E_WORKERS)

# Build all images
# 依赖链 postgres → embedding → backend → frontend 由 compose 健康检查
# 保证（condition: service_healthy），无连接竞态。
docker-build:
	docker compose build

# Build & start the full stack (one command)
docker-up:
	docker compose up -d --build

# Stop the full stack
docker-down:
	docker compose down

# Tail logs of all services
docker-logs:
	docker compose logs -f

# 兼容旧目标（仅启动外部依赖服务）
services-up: docker-up
services-down: docker-down
