.PHONY: help run list test check install ship install-skill shots plan apply outputs deploy analytics

PODBAY := uv run --project .
# The installed copy: a worktree at origin/main that scripts/release moves.
RELEASE := $(dir $(shell git rev-parse --path-format=absolute --git-common-dir)).release

# The site at podbay.tienoo.com: site/ is the page, infra/ its Terraform,
# analytics/ the nightly traffic rollup. AWS profile personal by default.
PROFILE ?= personal
AWS     := AWS_PROFILE=$(PROFILE) aws
TF      := AWS_PROFILE=$(PROFILE) mise exec -- terraform -chdir=infra
# The backup bucket that holds Terraform state, <login>-backup-<account id>
# by convention; set it to use another.
TFSTATE_BUCKET ?= $(USER)-backup-$(shell $(AWS) sts get-caller-identity --query Account --output text 2>/dev/null)

help:
	@echo "make run       open the TUI"
	@echo "make list      the same rows as plain text"
	@echo "make test      run the tests"
	@echo "make check     fail if a public doc quotes or names the user (scripts/check-quotes)"
	@echo "make install   test origin/main into .release/, link ~/.local/bin/podbay + status line to it"
	@echo "make ship      push main to origin, then make install: the one way a change reaches the installed copy"
	@echo "make install-skill HOME_REPO=~/Repositories/jeeves   link skills/head-jeeves into that repo"
	@echo "make shots     the site's screenshots from demo sessions, into site/img/"
	@echo "make plan      terraform plan for infra/, saved to infra/tfplan"
	@echo "make apply     terraform apply the saved plan"
	@echo "make deploy    ship site/ to podbay.tienoo.com"
	@echo "make analytics run the site's traffic rollup now (cron runs it nightly)"

run:
	@$(PODBAY) podbay

list:
	@$(PODBAY) podbay list

test:
	@$(PODBAY) pytest -q

# This repo is public. Rules in the skill and the docs are plain
# instructions; the user's own wording stays in the private home repo.
check:
	@scripts/check-quotes
	@scripts/check-signals

# The installed podbay and status line run from .release/, never from this
# working tree, so an edit in progress cannot break them (scripts/release).
# The status line is the source of podbay's context and quota columns, so
# every Claude Code config dir (~/.claude and ~/.claude-<label>) points at
# the one script. An existing statusLine setting is replaced; see README.
install:
	@scripts/release
	@mkdir -p "$$HOME/.local/bin"
	@ln -sfn "$(RELEASE)/bin/podbay" "$$HOME/.local/bin/podbay" && echo "→ ~/.local/bin/podbay"
	@for d in "$$HOME/.claude" "$$HOME"/.claude-*; do \
	  [ -f "$$d/settings.json" ] || continue; \
	  jq '.statusLine = {"type":"command","command":"sh $(RELEASE)/statusline.sh"}' \
	    "$$d/settings.json" > "$$d/settings.json.tmp" && mv "$$d/settings.json.tmp" "$$d/settings.json" \
	    && echo "→ $$d/settings.json statusLine"; \
	done

# A push alone leaves the installed copy behind, and nobody remembers the
# second step, so the push and the install are one target.
ship:
	@git push origin main
	@$(MAKE) install

# Head Jeeves' instructions, linked into the repo every session starts from
# (the home repo), where Claude Code picks skills up. A link, so the skill
# in this checkout stays the one copy.
install-skill:
	@test -n "$(HOME_REPO)" || { echo "usage: make install-skill HOME_REPO=<path>"; exit 1; }
	@mkdir -p "$(HOME_REPO)/.claude/skills"
	@ln -sfn "$(CURDIR)/skills/head-jeeves" "$(HOME_REPO)/.claude/skills/head-jeeves" && echo "→ $(HOME_REPO)/.claude/skills/head-jeeves"

# The real TUI and the board, mounted headless over made-up sessions, then
# rendered to PNG by Playwright. The first run downloads Chromium.
shots:
	@$(PODBAY) --with playwright python -m playwright install chromium >/dev/null
	@$(PODBAY) --with playwright python scripts/shots.py

plan:
	$(TF) init -input=false -backend-config="bucket=$(TFSTATE_BUCKET)"
	$(TF) plan -out=tfplan

apply:
	$(TF) apply tfplan

outputs:
	@$(TF) output

# Everything revalidates (no fingerprinted names here), the tracker pixel is
# never stored (a cached pixel never reaches the edge and is never logged),
# and data/ is excluded because the analytics pipeline owns it.
deploy:
	@BUCKET=$$($(TF) output -raw bucket_name); DIST=$$($(TF) output -raw distribution_id); \
	$(AWS) s3 sync site "s3://$$BUCKET" --delete --exclude ".*" --exclude "data/*" --exclude "t.gif" \
	  --cache-control "public,no-cache" && \
	$(AWS) s3 cp site/t.gif "s3://$$BUCKET/t.gif" --cache-control "no-store" --content-type "image/gif" && \
	$(AWS) cloudfront create-invalidation --distribution-id "$$DIST" --paths "/*" >/dev/null && \
	echo "✓ deployed → $$($(TF) output -raw site_url)"

analytics:
	analytics/run-analytics.sh; tail -3 analytics/logs/analytics.log
