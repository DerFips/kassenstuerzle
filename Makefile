# Kassenstürzle – Hilfsziele
#
# Befund I10 (Sicherheitsprüfung 2026-09-18): requirements.txt pinnt nur die
# obersten Pakete, die transitiven Abhängigkeiten wurden bei jedem Build neu
# aufgelöst. requirements.lock schreibt den vollständigen Baum inklusive
# Hashes fest und macht den Build damit reproduzierbar und überprüfbar.
#
# Das Erzeugen braucht Netzzugang und gehört deshalb bewusst in ein eigenes,
# von Hand angestoßenes Ziel — nicht in den Image-Bau.

.PHONY: lock test

# requirements.lock neu erzeugen. Bevorzugt `uv` (deutlich schneller), fällt
# auf pip-compile aus pip-tools zurück. Nach jeder Änderung an
# requirements.txt ausführen und das Ergebnis mit einchecken.
requirements.lock: requirements.txt
	@if command -v uv >/dev/null 2>&1; then \
		uv pip compile requirements.txt --generate-hashes -o requirements.lock; \
	elif command -v pip-compile >/dev/null 2>&1; then \
		pip-compile --generate-hashes --output-file=requirements.lock requirements.txt; \
	else \
		echo "Weder 'uv' noch 'pip-compile' gefunden. Installieren mit:"; \
		echo "  pip install uv        # oder"; \
		echo "  pip install pip-tools"; \
		exit 1; \
	fi

lock: requirements.lock

test:
	.venv-dev/bin/pytest tests/ -q
