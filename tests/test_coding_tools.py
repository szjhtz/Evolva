from __future__ import annotations

import hashlib

from evolva.agent.core import EvolvaAgent


def test_search_and_read_file_range_are_bounded(temp_config) -> None:
    source = temp_config.root / "pkg" / "sample.py"
    source.parent.mkdir()
    source.write_text("first\nneedle 中文\nthird\nfourth\n", encoding="utf-8")
    agent = EvolvaAgent(temp_config, assume_yes=True)

    search = agent._call_tool("search_text", {"query": "中文", "path": "pkg", "glob": "*.py", "max_results": 10})
    lines = agent._call_tool("read_file_range", {"path": "pkg/sample.py", "start_line": 2, "end_line": 3, "max_chars": 200})

    assert search.ok and "pkg/sample.py:2:needle 中文" in search.output
    assert lines.ok and "2: needle 中文" in lines.output and "3: third" in lines.output
    assert "1: first" not in lines.output


def test_apply_patch_checks_hash_and_unique_preimage(temp_config) -> None:
    source = temp_config.root / "app.py"
    source.write_text("value = 1\n", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    agent = EvolvaAgent(temp_config, assume_yes=True)

    changed = agent._call_tool(
        "apply_patch",
        {"path": "app.py", "old_text": "value = 1", "new_text": "value = 2", "expected_sha256": digest},
    )
    stale = agent._call_tool(
        "apply_patch",
        {"path": "app.py", "old_text": "value = 2", "new_text": "value = 3", "expected_sha256": digest},
    )

    assert changed.ok and source.read_text(encoding="utf-8") == "value = 2\n"
    assert not stale.ok and "hash mismatch" in stale.output.lower()
    assert changed.data["artifact"]["path"] == "app.py"


def test_apply_patch_rejects_ambiguous_preimage(temp_config) -> None:
    source = temp_config.root / "dup.txt"
    source.write_text("same\nsame\n", encoding="utf-8")
    agent = EvolvaAgent(temp_config, assume_yes=True)

    result = agent._call_tool("apply_patch", {"path": "dup.txt", "old_text": "same", "new_text": "new"})

    assert not result.ok
    assert "2 matches" in result.output
    assert source.read_text(encoding="utf-8") == "same\nsame\n"


def test_git_diff_and_run_tests_use_governed_sandbox(temp_config) -> None:
    agent = EvolvaAgent(temp_config, assume_yes=True)
    init = agent._call_tool("shell", {"command": "git init", "cwd": ".", "timeout": 10})
    assert init.ok
    (temp_config.root / "tracked.txt").write_text("changed\n", encoding="utf-8")

    diff = agent._call_tool("git_diff", {"path": "tracked.txt", "staged": False, "max_chars": 2000})
    tests = agent._call_tool("run_tests", {"command": "python3 -c \"print('TEST_OK')\"", "cwd": ".", "timeout": 10})

    assert diff.ok
    assert tests.ok and "TEST_OK" in tests.output
