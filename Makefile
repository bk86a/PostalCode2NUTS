.PHONY: lint format test run docker-build docker-run

lint:
	ruff check app/ scripts/

format:
	ruff format app/ scripts/

test:
	pytest tests/ -v

run:
	uvicorn app.main:app --reload --port 8000

docker-build:
	docker build -t postalcode2nuts .

docker-run:
	docker run -p 8000:8000 postalcode2nuts
