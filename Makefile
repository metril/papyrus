.PHONY: dev dev-backend dev-frontend build up down logs clean

# Development
dev-backend:
	cd backend && uvicorn app.main:app --reload --port 8080

dev-frontend:
	cd frontend && npm run dev

# Docker
build:
	docker compose -f docker/compose.yaml build

up:
	docker compose -f docker/compose.yaml up -d

down:
	docker compose -f docker/compose.yaml down

logs:
	docker compose -f docker/compose.yaml logs -f

# Testing
test-backend:
	cd backend && pytest

test-frontend:
	cd frontend && npm test

# Cleanup
clean:
	docker compose -f docker/compose.yaml down -v
	rm -rf frontend/dist frontend/node_modules backend/__pycache__
