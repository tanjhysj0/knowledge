.PHONY: help test unit-test e2e-test test-all services-up services-down install install-backend install-frontend

# Default target
help:
	@echo "Available targets:"
	@echo "  make install         - Install all dependencies"
	@echo "  make install-backend - Install backend dependencies"
	@echo "  make install-frontend - Install frontend dependencies"
	@echo "  make test            - Run all tests (unit + e2e)"
	@echo "  make unit-test       - Run backend unit tests (pytest)"
	@echo "  make e2e-test        - Run frontend E2E tests (Playwright)"
	@echo "  make services-up     - Start docker-compose services"
	@echo "  make services-down   - Stop docker-compose services"

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

# Start docker-compose services (for E2E tests)
services-up:
	docker-compose up -d

# Stop docker-compose services
services-down:
	docker-compose down
