"""Pack-local template/contract consistency. No host installation or vendor edits."""

import json
import unittest
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]


class ContractTests(unittest.TestCase):
    def test_canonical_entrypoints_reference_queue_and_proposal_boundary(self):
        for name in (
            "AGENTS.md",
            "README.md",
            "docs/agent-contract.md",
            "docs/agent-rules.md",
            "docs/validation.md",
        ):
            text = (PACK / name).read_text(encoding="utf-8").lower()
            with self.subTest(name=name):
                self.assertIn("work-queue.md", text)
                self.assertIn("proposal", text)
                self.assertNotIn("workers edit in parallel", text)
                self.assertNotIn("auto-commit per", text)

    def test_bridges_and_hosts_are_templates_not_direct_edit_workers(self):
        for folder in ("host-agents", "bridges"):
            for path in (PACK / folder).glob("*.md"):
                text = path.read_text(encoding="utf-8").lower()
                with self.subTest(path=path.name):
                    self.assertIn("template", text)
                    self.assertIn("proposal", text)
                    self.assertNotIn(
                        "worker reads only that file and nearby symbols, writes a failing test",
                        text,
                    )
                    self.assertNotIn("you are the executor", text)
                    self.assertNotRegex(text, r"tools:\s*\n\s*-")

    def test_worker_and_documenter_templates_exist_and_are_tool_free(self):
        for name in ("sonar-worker.md", "sonar-documenter.md"):
            path = PACK / "host-agents" / name
            self.assertTrue(path.is_file(), name)
            text = path.read_text().lower()
            self.assertIn("no tools", text)
            self.assertIn("template", text)

    def test_specialists_do_not_execute_or_load_vendor_skills(self):
        for path in (PACK / "prompts").glob("*.md"):
            text = path.read_text(encoding="utf-8").lower()
            with self.subTest(path=path.name):
                self.assertIn("proposal", text)
                self.assertIn("no tools", text)
                self.assertNotIn("must read", text)

    def test_vscode_task_example_is_manual_terminal_not_chat_transport(self):
        path = PACK / "examples/vscode-queue.tasks.json"
        self.assertTrue(path.is_file())
        value = json.loads(path.read_text())
        self.assertEqual(value["version"], "2.0.0")
        self.assertTrue(all(t["type"] == "process" for t in value["tasks"]))
        self.assertFalse(any("--execute" in t["args"] for t in value["tasks"]))

    def test_proposal_schema_documents_v2_phase_without_breaking_v1(self):
        schema = json.loads((PACK / "docs/debt-proposal.schema.json").read_text())
        self.assertEqual(schema["properties"]["version"].get("enum"), [1, 2])
        self.assertIn("phase", schema["properties"]["edits"]["items"]["properties"])
