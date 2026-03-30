.PHONY: install serve run models sessions status clean help

PYTHON  := .venv/bin/python
SHOWMI  := .venv/bin/showmi
PORT    ?= 8765

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: .venv ## Install dependencies
	@echo "Done. Run 'make serve' to start."

.venv:
	@echo "Creating virtual environment..."
	@if command -v uv >/dev/null 2>&1; then \
		uv venv .venv && uv pip install -e .; \
	else \
		python3 -m venv .venv && .venv/bin/pip install -e .; \
	fi
	@echo ""
	@echo "  To configure a model:"
	@echo "    $(SHOWMI) models add"
	@echo ""

serve: .venv ## Start the server (PORT=8765)
	$(SHOWMI) serve -p $(PORT)

serve-dev: .venv ## Start with auto-reload
	$(SHOWMI) serve -p $(PORT) --reload

run: .venv ## Run a task: make run TASK="search for flights"
	$(SHOWMI) run "$(TASK)"

models: .venv ## List configured models
	$(SHOWMI) models list

sessions: .venv ## List recent sessions
	$(SHOWMI) sessions

status: .venv ## Check server status
	$(SHOWMI) status -p $(PORT)

clean: ## Remove venv and caches
	rm -rf .venv __pycache__ *.egg-info
