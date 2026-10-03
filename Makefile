.PHONY: help run list test install install-skill

PODBAY := uv run --project .

help:
	@echo "make run       open the TUI"
	@echo "make list      the same rows as plain text"
	@echo "make test      run the tests"
	@echo "make install   ~/.local/bin/podbay symlink + status line in every Claude account"
	@echo "make install-skill HOME_REPO=~/Repositories/jeeves   link skills/head-jeeves into that repo"

run:
	@$(PODBAY) podbay

list:
	@$(PODBAY) podbay list

test:
	@$(PODBAY) pytest -q

# The status line is the source of podbay's context and quota columns, so
# every Claude Code config dir (~/.claude and ~/.claude-<label>) points at
# the one script. An existing statusLine setting is replaced; see README.
install:
	@mkdir -p "$$HOME/.local/bin"
	@ln -sfn "$(CURDIR)/bin/podbay" "$$HOME/.local/bin/podbay" && echo "→ ~/.local/bin/podbay"
	@for d in "$$HOME/.claude" "$$HOME"/.claude-*; do \
	  [ -f "$$d/settings.json" ] || continue; \
	  jq '.statusLine = {"type":"command","command":"sh $(CURDIR)/statusline.sh"}' \
	    "$$d/settings.json" > "$$d/settings.json.tmp" && mv "$$d/settings.json.tmp" "$$d/settings.json" \
	    && echo "→ $$d/settings.json statusLine"; \
	done

# Head Jeeves' instructions, linked into the repo every session starts from
# (the home repo), where Claude Code picks skills up. A link, so the skill
# in this checkout stays the one copy.
install-skill:
	@test -n "$(HOME_REPO)" || { echo "usage: make install-skill HOME_REPO=<path>"; exit 1; }
	@mkdir -p "$(HOME_REPO)/.claude/skills"
	@ln -sfn "$(CURDIR)/skills/head-jeeves" "$(HOME_REPO)/.claude/skills/head-jeeves" && echo "→ $(HOME_REPO)/.claude/skills/head-jeeves"
