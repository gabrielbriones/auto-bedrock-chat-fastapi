FRONTEND := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)

# Browser-visible Vite origin; change this once for a different test host.
TEST_ORIGIN ?= http://localhost:3000
export TEST_ORIGIN

.PHONY: help list test project bootstrap login-page login

help:
	@printf '%s\n' \
		'make list                                   List individual test files' \
		'make test FILE=e2e/chat-scroll.test.ts       Run one file (relative to tests/)' \
		'make test FILE=... NAME="test name"          Run one test case in a file' \
		'make test FILE=e2e/chat-scroll.test.ts HEADED=1  Watch a scripted browser test' \
		'make project PROJECT=unit                    Run a whole Jest project' \
		'make bootstrap                               Check the live chat shell' \
		'make login-page                              Watch Chrome reach Intel login (no password)' \
		'make login                                   Watch SSO login, send, and backend reply'

list:
	@cd "$(FRONTEND)" && rg --files tests | rg '\.(contract\.)?test\.tsx?$$|\.bench\.tsx$$' | sed 's|^tests/||' | sort

test:
	@file='$(FILE)'; \
	case "$$file" in \
		e2e-live/*.test.ts) project=e2e-live ;; \
		e2e/*.test.ts) project=e2e ;; \
		msw/*.contract.test.ts) project=contract ;; \
		lint/*.test.ts) project=lint ;; \
		*.bench.tsx) project=bench ;; \
		*.test.tsx) project=component ;; \
		*.test.ts) project=unit ;; \
		*) printf '%s\n' 'Set FILE to a test path relative to tests/ (see make list).' >&2; exit 2 ;; \
	esac; \
	if ! test -f "$(FRONTEND)/tests/$$file"; then printf 'No such test: %s\n' "$$file" >&2; exit 2; fi; \
	cd "$(FRONTEND)" && HEADED='$(HEADED)' npm run jest -- --selectProjects "$$project" --runInBand --runTestsByPath "tests/$$file" $(if $(NAME),-t '$(NAME)',)

project:
	@case '$(PROJECT)' in unit|component|contract|lint|bench|e2e|e2e-live) ;; \
		*) printf '%s\n' 'Set PROJECT to unit, component, contract, lint, bench, e2e, or e2e-live.' >&2; exit 2 ;; \
	esac; \
	cd "$(FRONTEND)" && HEADED='$(HEADED)' npm run jest -- --selectProjects '$(PROJECT)' --runInBand

bootstrap:
	@cd "$(FRONTEND)" && npm run test:e2e:live:bootstrap

login-page:
	@cd "$(FRONTEND)" && npm run test:e2e:live:login-page

login:
	@cd "$(FRONTEND)" && npm run test:e2e:live:headed -- -t 'logs in with SSO'
