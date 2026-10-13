"""Stored scientific choices must be usable without reverse engineering JSON."""

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from experiment_design.cli import _parser, _status
from tests.support.presentation import choice, study


def test_cli_presents_option_consequences_and_exact_select_command(tmp_path, capsys):
    store, rid = study(tmp_path)
    try:
        decision = store.save_decision(choice(store, rid))
        _status(store, rid, Path("study.yaml"))
        text = capsys.readouterr().out
        assert "Retains randomized comparison" in text
        assert "/analysis/model" in text
        assert "--action SELECT_OPTION --option" in text
        assert f"decide {rid} {decision.id} --revision 3" in text
        assert "--action PROPOSE_ALTERNATIVE" in text
        assert "Recommendation (provisional)" in text
        assert not store.decision(rid, decision.id).responses
    finally:
        store.close()


@pytest.mark.parametrize(
    "option_id", ["itt", "student's", "student $(ignored)", "@choice", "--itt"]
)
def test_option_hint_round_trips_literal_id_in_operator_shell(tmp_path, capsys, option_id):
    store, rid = study(tmp_path)
    try:
        decision = choice(store, rid)
        decision.options[0].id = option_id
        store.save_decision(decision)
        _status(store, rid, Path("study.yaml"))
        command = next(
            line.strip()
            for line in capsys.readouterr().out.splitlines()
            if "--action SELECT_OPTION" in line
        )
        if os.name != "nt":
            args = shlex.split(command)
            assert _parser().parse_args(args[1:]).option == option_id
            return
        shell = shutil.which("powershell") or shutil.which("pwsh")
        assert shell is not None, "Repository requires PowerShell on Windows"
        script = r"""
$tokens = $null; $parseErrors = $null
$tree = [System.Management.Automation.Language.Parser]::ParseInput(
    [Console]::In.ReadToEnd(), [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { exit 1 }
$commands = $tree.FindAll({param($n)
    $n -is [System.Management.Automation.Language.CommandAst]}, $true)
$elements = $commands[0].CommandElements
$arguments = @($elements | ForEach-Object {
    if ($null -ne $_.Value) { [string]$_.Value } else { $_.Extent.Text }
})
ConvertTo-Json -InputObject $arguments -Compress
"""
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-Command", script],
            input=command,
            text=True,
            capture_output=True,
            check=True,
        )
        args = json.loads(result.stdout)
        assert _parser().parse_args(args[1:]).option == option_id
    finally:
        store.close()
