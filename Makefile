PY = .venv/bin/python

.PHONY: install data synth test run dashboard lint figures
install:
	python3 -m venv .venv
	.venv/bin/pip install -e '.[dev]'
data:
	$(PY) -m continuous_audit_lab.cli download
	$(PY) -m continuous_audit_lab.cli prepare
synth:
	$(PY) -m continuous_audit_lab.cli synth
test:
	$(PY) -m pytest -q
lint:
	.venv/bin/ruff check .
run:
	$(PY) -m continuous_audit_lab.cli run
figures:
	.venv/bin/jupyter nbconvert --to notebook --execute --inplace notebooks/figures.ipynb
dashboard:
	.venv/bin/streamlit run continuous_audit_lab/dashboard/app.py
