PYTHON ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)
export PYTHONPATH := src

.PHONY: test replay sample dashboard analyst record check-data

test:
	$(PYTHON) -m pytest -q

record:
	$(PYTHON) -m jev_paper.data.record --config config.yaml --data-dir data

record-daemon:
	bash scripts/ensure_record.sh

record-status:
	$(PYTHON) scripts/check_data.py --data-dir data; \
	tmux has-session -t jev-record 2>/dev/null && echo "tmux: jev-record up" || echo "tmux: jev-record down"; \
	pgrep -af 'jev_paper.data.record' || echo "recorder process: none"

check-data:
	$(PYTHON) scripts/check_data.py --data-dir data

replay:
	$(PYTHON) scripts/replay.py --config config.yaml --tape tests/fixtures/sample_events.jsonl --log-dir logs/replay

sample:
	$(PYTHON) scripts/make_fixture.py --out tests/fixtures/session_events.jsonl --minutes 40
	$(PYTHON) -m jev_paper.loop --config config.yaml --replay tests/fixtures/session_events.jsonl --log-dir logs/sample
	$(PYTHON) -m jev_paper.analyze --log-dir logs/sample --out-dir reports/sample
	$(PYTHON) -m jev_paper.agent.analyst --day sample --log-dir logs/sample --report-dir reports --proposal-dir proposals --config config.yaml

dashboard:
	$(PYTHON) -m jev_paper.dashboard.app --log-dir logs/sample --report-dir reports --port 8081

live:
	$(PYTHON) -m jev_paper.loop --config config.yaml --log-dir logs/live
