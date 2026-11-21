.PHONY: check lint format-check typecheck test

check: lint format-check typecheck

lint:
	uv run --locked ruff check .

format-check:
	uv run --locked ruff format --check .

typecheck:
	uv run --locked mypy

test:
	docker build -q -f Dockerfile.test -t msnow-bootstrap-test .
	docker run --rm msnow-bootstrap-test
