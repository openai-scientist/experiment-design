from experiment_design.agents.benchmark.survey import evidence_resources

TEXT = """## H1: s

### Evidence

- Paper A (2024)
  - Data: Defects4J v2.0 and BugsInPy
  - Metrics: pass@1
  - Findings: ignored
- Paper B
  - Data: Defects4J v2.0 and BugsInPy
"""


def test_data_and_metrics_lines_are_collected_once():
    assert evidence_resources(TEXT) == ["Defects4J v2.0 and BugsInPy", "pass@1"]


def test_text_without_evidence_gives_nothing():
    assert evidence_resources("## H1: s\n\n- **falsification:** x\n") == []
