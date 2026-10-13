from experiment_design.persistence.store import Store


def test_rebuttal_evidence_reaches_verifier_without_borrowing_computation(tmp_path):
    import json

    from experiment_design.config import RunConfig
    from experiment_design.workflow import Deps, advance, start_run
    from tests.support.providers import ScriptedJudge, ScriptedLLM, submit, tool_call
    from tests.support.scenarios import normal_responses, source_snapshot

    store = Store(tmp_path / "rebuttal.db")
    captured = {}

    def defend(messages, tools):
        observation = json.loads(messages[-1]["content"])
        captured["ref"] = observation["evidence_ref"]
        assert observation["result"][0]["status"] == "computed"
        return submit(
            "submit_revision",
            {
                "patch": {"ops": [], "rationale": "Defend the declared assumption"},
                "rebuttals": [
                    {
                        "issue_id": "I1",
                        "argument": "The sensitivity is disclosed",
                        "evidence_refs": [captured["ref"], "S1"],
                    }
                ],
            },
        )

    def inspect_support(messages, tools):
        focus = json.loads(messages[1]["content"].split("\nVerification focus: ")[1])
        defense = focus["rebuttals"][0]
        assert defense["issue_id"] == "I1" and defense["argument"] == "The sensitivity is disclosed"
        assert defense["evidence_refs"] == [captured["ref"], "S1"]
        evidence = defense["observations"][0]
        assert evidence["accepted"] and evidence["evidence_ref"] == captured["ref"]
        assert evidence["task_id"] == defense["task_id"]
        assert evidence["binding"] == defense["binding"]
        assert evidence["arguments"]["overrides"]["icc"]["value"] == 0.15
        assert evidence["result"]["value"][0]["counterfactual"] is True
        assert defense["sources"][0]["id"] == "S1"
        captured["defense_task"] = defense["task_id"]
        # Another task's computation is visible for critique, never an own-task attestation.
        return submit(
            "submit_verdicts",
            {
                "verdicts": [
                    {
                        "issue_id": "I1",
                        "verdict": "resolved",
                        "grounds": "Review sensitivity",
                        "evidence_refs": [captured["ref"]],
                    }
                ],
                "regressions": [],
            },
        )

    def fresh_read(messages, tools):
        assert "Unknown evidence ref" in messages[-1]["content"]
        return tool_call("read_design", {}, call_id="independent-inspection")

    def verdict(messages, tools):
        captured["verifier_ref"] = json.loads(messages[-1]["content"])["evidence_ref"]
        assert captured["verifier_ref"] != captured["ref"]
        return submit(
            "submit_verdicts",
            {
                "verdicts": [
                    {
                        "issue_id": "I1",
                        "verdict": "resolved",
                        "grounds": "Independently inspected assumptions and disclosed sensitivity",
                        "evidence_refs": [captured["verifier_ref"]],
                    }
                ],
                "regressions": [],
            },
        )

    responses = normal_responses()
    responses[2] = submit(
        "submit_findings",
        {
            "summary": "Challenge assumption",
            "findings": [
                {
                    "severity": "MAJOR",
                    "paths": ["/sampling/cluster/icc"],
                    "mechanism": "Sensitivity to the assumed dependence requires explanation",
                    "consequence": "The result may depend on the assumed correlation",
                    "basis": "reasoning",
                    "evidence_refs": ["S1"],
                }
            ],
        },
    )
    responses += [
        tool_call(
            "compute_power",
            {
                "calculator_id": "cluster_equal_size_t",
                "overrides": {"icc": {"value": 0.15, "provenance": "assumption", "unit": ""}},
            },
        ),
        defend,
        inspect_support,
        fresh_read,
        verdict,
    ]
    deps = Deps(
        store,
        ScriptedLLM(responses),
        ScriptedJudge(
            [
                {"next_action": "REVIEW"},
                {"next_action": "REVISE"},
                {"next_action": "VERIFY"},
                {"I1": "resolved"},
                {"next_action": "QUALIFY"},
            ]
        ),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    assert advance(run, deps) == "REVIEWABLE"
    assert "verifier_ref" in captured, "Verifier must reject borrowed evidence and obtain its own"
    verification = next(e for e in store.events(run) if e.type == "verification")
    assert verification.data["evidence_refs"] == [captured["verifier_ref"]]
    assert store.issues(run)[0].status == "resolved"
    assert store.assessment(run).plan_complete is True
    assert store.task(captured["defense_task"]).task.stage_id == "S4"
    assert store.version(run).version == 1 and store.approvals(run) == []
    store.close()
