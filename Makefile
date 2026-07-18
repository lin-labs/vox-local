# vox-local — lab-service verbs (systemd-supervised; see blin-lab-service).

SHELL := /bin/bash
UNIT := voice-local.service
NGROK_UNIT := voice-local-ngrok.service
RELEASE_CMD := uv sync && uv run python -m pytest tests -q && systemctl --user restart
DEPLOY_HOST ?= boyan@35.87.72.173
DEPLOY_PATH ?= /home/boyan/Projects/vox-local
DEPLOY_UNIT ?= vox-local.service
LABS_DEPLOY_HOST ?= labs
LABS_DEPLOY_PATH ?= ~/Experiments/voice/vox-local
LABS_DEPLOY_UNIT ?= voice-local.service
KOYUKI_AGENT_ID := 38281e63-2215-4b49-87c8-0f20d2492da3
MAYUKI_AGENT_ID := 23559d91-cd42-4cb9-be69-e7e48a059608
MIYUKI_AGENT_ID := b6703d1b-4fcb-4642-ab86-e6512848a4fd
LINDA_AGENT_ID := 95dabc72-81ba-44f5-b4f2-30a953116588

.PHONY: serve test start stop restart status logs tail release deploy deploy-amazon deploy-labs push-gems push-prompt push-mayuki-prompt push-miyuki-prompt push-linda-prompt meridian-setup meridian-dev meridian-check

serve:
	uv run vox-local serve

test:
	uv run python -m pytest tests -q

start:
	systemctl --user start $(UNIT) $(NGROK_UNIT)

stop:
	systemctl --user stop $(UNIT) $(NGROK_UNIT)

restart:
	systemctl --user restart $(UNIT)

status:
	systemctl --user status $(UNIT) $(NGROK_UNIT) --no-pager | head -30

logs:
	journalctl --user -u $(UNIT) --no-pager | less +G

tail:
	journalctl --user -u $(UNIT) -f

release:
	$(RELEASE_CMD) $(UNIT)

deploy deploy-amazon:
	ssh $(DEPLOY_HOST) 'cd $(DEPLOY_PATH) && git pull --ff-only && $(RELEASE_CMD) $(DEPLOY_UNIT)'

deploy-labs:
	ssh $(LABS_DEPLOY_HOST) 'cd $(LABS_DEPLOY_PATH) && git pull --ff-only && $(RELEASE_CMD) $(LABS_DEPLOY_UNIT)'

push-gems:
	git add data/gems.db && git commit -m "gems: data bag update" && git push

# Meridian lives as a normal app folder in this repo. These shortcuts let web
# contributors work from the repository root without knowing the Python stack.
meridian-setup:
	npm --prefix meridian ci

meridian-dev:
	npm --prefix meridian run dev

meridian-check:
	npm --prefix meridian run check

# The live agent runs whatever Vocal Bridge holds — every edit to
# docs-agent-prompt.txt must be pushed there in the same change (AGENTS.md rule 0).
push-prompt:
	vb prompt set -f docs-agent-prompt.txt
	@vb prompt show 2>/dev/null | sed -n '/--- System Prompt ---/,$$p' | tail -n +2 | \
	  diff -q - docs-agent-prompt.txt >/dev/null \
	  && echo "prompt in sync with Vocal Bridge" \
	  || { echo "WARNING: remote prompt still differs after push"; exit 1; }

# Mayuki is a separate VB agent. This target intentionally selects her only for
# the duration of the push, then restores Koyuki as the CLI default.
push-mayuki-prompt:
	@trap 'vb agent use $(KOYUKI_AGENT_ID) >/dev/null' EXIT; \
	  vb agent use $(MAYUKI_AGENT_ID) >/dev/null; \
	  vb prompt set -f docs-mayuki-agent-prompt.txt; \
	  vb prompt show 2>/dev/null | sed -n '/--- System Prompt ---/,$$p' | tail -n +2 | \
	  diff -q - docs-mayuki-agent-prompt.txt >/dev/null \
	  && echo "Mayuki prompt in sync with Vocal Bridge" \
	  || { echo "WARNING: Mayuki remote prompt still differs after push"; exit 1; }

# Miyuki is the outbound prober agent used by /api/outbound/calls
# (VB_OUTBOUND_AGENT_ID). Same select-push-restore pattern as Mayuki.
push-miyuki-prompt:
	@trap 'vb agent use $(KOYUKI_AGENT_ID) >/dev/null' EXIT; \
	  vb agent use $(MIYUKI_AGENT_ID) >/dev/null; \
	  vb prompt set -f docs-miyuki-agent-prompt.txt; \
	  vb prompt show 2>/dev/null | sed -n '/--- System Prompt ---/,$$p' | tail -n +2 | \
	  diff -q - docs-miyuki-agent-prompt.txt >/dev/null \
	  && echo "Miyuki prompt in sync with Vocal Bridge" \
	  || { echo "WARNING: Miyuki remote prompt still differs after push"; exit 1; }

# Linda is the fixed-case prober behind the outbound demo: unlike Miyuki she
# carries the whole case in her prompt instead of reading the goal block.
push-linda-prompt:
	@trap 'vb agent use $(KOYUKI_AGENT_ID) >/dev/null' EXIT; \
	  vb agent use $(LINDA_AGENT_ID) >/dev/null; \
	  vb prompt set -f docs-linda-agent-prompt.txt; \
	  vb prompt show 2>/dev/null | sed -n '/--- System Prompt ---/,$$p' | tail -n +2 | \
	  diff -q - docs-linda-agent-prompt.txt >/dev/null \
	  && echo "Linda prompt in sync with Vocal Bridge" \
	  || { echo "WARNING: Linda remote prompt still differs after push"; exit 1; }
