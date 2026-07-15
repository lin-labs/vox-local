# vox-local — lab-service verbs (systemd-supervised; see blin-lab-service).

SHELL := /bin/bash
UNIT := voice-local.service
NGROK_UNIT := voice-local-ngrok.service

.PHONY: serve test start stop restart status logs tail release deploy push-gems push-prompt

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
	uv sync && uv run python -m pytest tests -q && systemctl --user restart $(UNIT)

deploy:
	ssh labs 'cd ~/Experiments/voice/vox-local && git pull --ff-only && make release'

push-gems:
	git add data/gems.db && git commit -m "gems: data bag update" && git push

# The live agent runs whatever Vocal Bridge holds — every edit to
# docs-agent-prompt.txt must be pushed there in the same change (AGENTS.md rule 0).
push-prompt:
	vb prompt set -f docs-agent-prompt.txt
	@vb prompt show 2>/dev/null | sed -n '/--- System Prompt ---/,$$p' | tail -n +2 | \
	  diff -q - docs-agent-prompt.txt >/dev/null \
	  && echo "prompt in sync with Vocal Bridge" \
	  || { echo "WARNING: remote prompt still differs after push"; exit 1; }
