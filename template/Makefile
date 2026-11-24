PRE_COMMIT_VERSION := 4.7.0
PRE_COMMIT := uvx --python 3.13 --exclude-newer 2026-11-21T00:00:00Z pre-commit@$(PRE_COMMIT_VERSION)
CHECK_TARGETS :=

.DEFAULT_GOAL := check
.PHONY: check hooks length

# Each language adds make/<language>.mk. It appends its targets to CHECK_TARGETS.
# The includes come before `check:` because make expands prerequisites when it reads the rule.
-include $(sort $(wildcard make/*.mk))
CHECK_TARGETS += length

check: $(CHECK_TARGETS)

hooks:
	$(PRE_COMMIT) install

# `make length` runs the `length` hook, because the hook installs scc. So the hook cannot call
# `make length`, which would recurse. List tracked and untracked files that git does not ignore,
# so a new long file fails before its commit. A failed `git ls-files` fails the target. The lists
# go through temp files, because a shell drops NUL bytes in `$(...)`. Each path gets a `./`
# prefix, so pre-commit never reads a name such as `-o` as an option.
length:
	@tmp=; list=; trap 'rm -f "$$tmp" "$$list"' EXIT; tmp=$$(mktemp) || exit 1; list=$$(mktemp) || exit 1; \
	git ls-files -z --cached --others --exclude-standard >"$$tmp" || exit 1; \
	if [ ! -s "$$tmp" ]; then echo "length: no files"; exit 0; fi; \
	xargs -0 printf './%s\0' <"$$tmp" >"$$list" || exit 1; \
	xargs -0 $(PRE_COMMIT) run length --files <"$$list"
