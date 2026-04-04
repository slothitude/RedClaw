"""27-category workflow definitions for synthetic training data generation.

Each workflow represents a real-world coding/assistant pattern that RedClaw
encounters. These are used to generate augmented training sequences for both
the BinaryMLP predictor and BitNet fine-tuning.

Categories cover: file operations, search, git, debugging, refactoring,
web research, testing, deployment, configuration, and assistant tasks.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WorkflowStep:
    """A single step in a workflow — tool call + expected context."""
    tool: str
    description: str  # What the step accomplishes
    input_hint: str = ""  # Hint about tool input


@dataclass
class Workflow:
    """A complete workflow pattern."""
    category: str
    name: str
    description: str
    steps: list[WorkflowStep]
    success_rate: float = 0.9  # Estimated success rate for weighting
    is_surgical: bool = False  # Whether this is a minimal-tool-count pattern


# fmt: off
WORKFLOWS: list[Workflow] = [
    # ── 1. Bug Fix: Read & Edit ──
    Workflow(
        category="bug_fix", name="read_edit_fix",
        description="Read file to understand bug, then apply targeted fix",
        steps=[
            WorkflowStep("read_file", "Read the file containing the bug"),
            WorkflowStep("edit_file", "Apply the fix with exact string replacement"),
        ],
        success_rate=0.85, is_surgical=True,
    ),

    # ── 2. Bug Fix: Search → Read → Edit ──
    Workflow(
        category="bug_fix", name="search_read_edit",
        description="Search for error location, read context, then fix",
        steps=[
            WorkflowStep("grep_search", "Search for error string or pattern"),
            WorkflowStep("read_file", "Read the file at the matching line"),
            WorkflowStep("edit_file", "Apply the fix"),
        ],
        success_rate=0.80, is_surgical=True,
    ),

    # ── 3. Bug Fix: Multi-file ──
    Workflow(
        category="bug_fix", name="multi_file_fix",
        description="Search across files, read relevant ones, edit multiple",
        steps=[
            WorkflowStep("grep_search", "Find error across project"),
            WorkflowStep("read_file", "Read first file"),
            WorkflowStep("grep_search", "Find related code in other files"),
            WorkflowStep("read_file", "Read second file"),
            WorkflowStep("edit_file", "Fix in first file"),
            WorkflowStep("edit_file", "Fix in second file"),
        ],
        success_rate=0.70,
    ),

    # ── 4. Feature: New File ──
    Workflow(
        category="feature", name="new_file_feature",
        description="Search for patterns, then create a new file",
        steps=[
            WorkflowStep("glob_search", "Find similar existing files"),
            WorkflowStep("read_file", "Read a reference file for patterns"),
            WorkflowStep("write_file", "Create the new file"),
        ],
        success_rate=0.85,
    ),

    # ── 5. Feature: Modify Existing ──
    Workflow(
        category="feature", name="modify_feature",
        description="Add feature to existing file with search + edit",
        steps=[
            WorkflowStep("grep_search", "Find the insertion point"),
            WorkflowStep("read_file", "Read surrounding context"),
            WorkflowStep("edit_file", "Insert new feature code"),
            WorkflowStep("bash", "Run tests to verify"),
        ],
        success_rate=0.75,
    ),

    # ── 6. Refactor: Rename ──
    Workflow(
        category="refactor", name="rename_symbol",
        description="Rename a variable/function across the codebase",
        steps=[
            WorkflowStep("grep_search", "Find all occurrences of the symbol"),
            WorkflowStep("read_file", "Verify first occurrence"),
            WorkflowStep("edit_file", "Rename in first file"),
            WorkflowStep("edit_file", "Rename in second file"),
            WorkflowStep("bash", "Run tests after rename"),
        ],
        success_rate=0.80,
    ),

    # ── 7. Refactor: Extract Function ──
    Workflow(
        category="refactor", name="extract_function",
        description="Extract code into a new function",
        steps=[
            WorkflowStep("read_file", "Read the file to refactor"),
            WorkflowStep("edit_file", "Extract function and replace inline code"),
            WorkflowStep("bash", "Run tests"),
        ],
        success_rate=0.75,
    ),

    # ── 8. Exploration: Code Understanding ──
    Workflow(
        category="exploration", name="understand_code",
        description="Explore a codebase to understand structure",
        steps=[
            WorkflowStep("glob_search", "Find relevant files"),
            WorkflowStep("read_file", "Read main file"),
            WorkflowStep("grep_search", "Find key functions"),
            WorkflowStep("read_file", "Read related file"),
        ],
        success_rate=0.90,
    ),

    # ── 9. Exploration: Find Definition ──
    Workflow(
        category="exploration", name="find_definition",
        description="Find where something is defined",
        steps=[
            WorkflowStep("grep_search", "Search for the definition"),
            WorkflowStep("read_file", "Read the file with the definition"),
        ],
        success_rate=0.90, is_surgical=True,
    ),

    # ── 10. Debugging: Test Failure ──
    Workflow(
        category="debugging", name="test_failure",
        description="Debug a failing test",
        steps=[
            WorkflowStep("bash", "Run the failing test"),
            WorkflowStep("read_file", "Read the test file"),
            WorkflowStep("read_file", "Read the source file being tested"),
            WorkflowStep("edit_file", "Fix the bug"),
            WorkflowStep("bash", "Re-run the test"),
        ],
        success_rate=0.70,
    ),

    # ── 11. Debugging: Log Analysis ──
    Workflow(
        category="debugging", name="log_analysis",
        description="Analyze error logs to find root cause",
        steps=[
            WorkflowStep("bash", "Run the program to reproduce error"),
            WorkflowStep("grep_search", "Search for error pattern in code"),
            WorkflowStep("read_file", "Read the file with the error"),
            WorkflowStep("edit_file", "Fix the issue"),
        ],
        success_rate=0.70,
    ),

    # ── 12. Git: Branch & Commit ──
    Workflow(
        category="git", name="branch_commit",
        description="Create branch, make changes, commit",
        steps=[
            WorkflowStep("bash", "Create and checkout new branch"),
            WorkflowStep("edit_file", "Make the changes"),
            WorkflowStep("bash", "Stage and commit changes"),
        ],
        success_rate=0.85,
    ),

    # ── 13. Git: Conflict Resolution ──
    Workflow(
        category="git", name="resolve_conflict",
        description="Resolve merge conflicts",
        steps=[
            WorkflowStep("bash", "Attempt merge"),
            WorkflowStep("read_file", "Read file with conflicts"),
            WorkflowStep("edit_file", "Resolve the conflict"),
            WorkflowStep("bash", "Complete the merge"),
        ],
        success_rate=0.75,
    ),

    # ── 14. Web: Research & Apply ──
    Workflow(
        category="web", name="research_apply",
        description="Research a solution online, then apply it",
        steps=[
            WorkflowStep("web_search", "Search for the solution"),
            WorkflowStep("web_reader", "Read the most relevant result"),
            WorkflowStep("edit_file", "Apply the solution"),
        ],
        success_rate=0.65,
    ),

    # ── 15. Web: API Documentation ──
    Workflow(
        category="web", name="api_docs_lookup",
        description="Look up API docs and implement",
        steps=[
            WorkflowStep("web_search", "Search for API documentation"),
            WorkflowStep("web_reader", "Read the documentation page"),
            WorkflowStep("read_file", "Read current implementation"),
            WorkflowStep("edit_file", "Update to use correct API"),
        ],
        success_rate=0.70,
    ),

    # ── 16. Config: Setup Project ──
    Workflow(
        category="config", name="setup_project",
        description="Configure project dependencies and settings",
        steps=[
            WorkflowStep("glob_search", "Find config files"),
            WorkflowStep("read_file", "Read current config"),
            WorkflowStep("edit_file", "Update configuration"),
            WorkflowStep("bash", "Install dependencies"),
        ],
        success_rate=0.80,
    ),

    # ── 17. Config: Environment ──
    Workflow(
        category="config", name="env_setup",
        description="Set up environment variables and secrets",
        steps=[
            WorkflowStep("glob_search", "Find .env or config files"),
            WorkflowStep("read_file", "Read current environment config"),
            WorkflowStep("write_file", "Create/update .env file"),
        ],
        success_rate=0.85,
    ),

    # ── 18. Test: Write New Tests ──
    Workflow(
        category="testing", name="write_tests",
        description="Write tests for existing code",
        steps=[
            WorkflowStep("read_file", "Read the source file"),
            WorkflowStep("glob_search", "Find existing test files for patterns"),
            WorkflowStep("read_file", "Read an existing test file"),
            WorkflowStep("write_file", "Create new test file"),
            WorkflowStep("bash", "Run the new tests"),
        ],
        success_rate=0.80,
    ),

    # ── 19. Test: Fix Flaky Test ──
    Workflow(
        category="testing", name="fix_flaky_test",
        description="Fix a test that fails intermittently",
        steps=[
            WorkflowStep("bash", "Run test multiple times"),
            WorkflowStep("read_file", "Read the test"),
            WorkflowStep("grep_search", "Find the code it tests"),
            WorkflowStep("read_file", "Read source code"),
            WorkflowStep("edit_file", "Fix the test"),
        ],
        success_rate=0.65,
    ),

    # ── 20. Deploy: Build & Push ──
    Workflow(
        category="deploy", name="build_push",
        description="Build project and deploy",
        steps=[
            WorkflowStep("bash", "Run build"),
            WorkflowStep("bash", "Run tests"),
            WorkflowStep("bash", "Deploy to target"),
        ],
        success_rate=0.80,
    ),

    # ── 21. Deploy: CI Fix ──
    Workflow(
        category="deploy", name="fix_ci",
        description="Fix a failing CI pipeline",
        steps=[
            WorkflowStep("bash", "Check CI status"),
            WorkflowStep("read_file", "Read CI config file"),
            WorkflowStep("edit_file", "Fix the CI configuration"),
            WorkflowStep("bash", "Push and verify CI passes"),
        ],
        success_rate=0.70,
    ),

    # ── 22. Assistant: Task Management ──
    Workflow(
        category="assistant", name="add_task",
        description="Add a task to the task list",
        steps=[
            WorkflowStep("task", "Add a new task"),
        ],
        success_rate=0.95, is_surgical=True,
    ),

    # ── 23. Assistant: Multi-step Task ──
    Workflow(
        category="assistant", name="complex_task",
        description="Break down complex request into tasks and reminders",
        steps=[
            WorkflowStep("task", "Create main task"),
            WorkflowStep("task", "Create subtask"),
            WorkflowStep("note", "Save relevant notes"),
            WorkflowStep("reminder", "Set reminder for follow-up"),
        ],
        success_rate=0.85,
    ),

    # ── 24. Assistant: Information Retrieval ──
    Workflow(
        category="assistant", name="info_lookup",
        description="Look up information and save as note",
        steps=[
            WorkflowStep("web_search", "Search for information"),
            WorkflowStep("web_reader", "Read relevant page"),
            WorkflowStep("note", "Save key information as note"),
        ],
        success_rate=0.80,
    ),

    # ── 25. Performance: Optimization ──
    Workflow(
        category="performance", name="optimize_code",
        description="Profile and optimize slow code",
        steps=[
            WorkflowStep("bash", "Run profiler"),
            WorkflowStep("read_file", "Read the slow function"),
            WorkflowStep("grep_search", "Find all call sites"),
            WorkflowStep("edit_file", "Optimize the function"),
            WorkflowStep("bash", "Re-run profiler to verify"),
        ],
        success_rate=0.65,
    ),

    # ── 26. Security: Vulnerability Fix ──
    Workflow(
        category="security", name="fix_vulnerability",
        description="Fix a security vulnerability",
        steps=[
            WorkflowStep("grep_search", "Find vulnerable pattern"),
            WorkflowStep("read_file", "Read the vulnerable code"),
            WorkflowStep("edit_file", "Apply security fix"),
            WorkflowStep("bash", "Run security scanner"),
        ],
        success_rate=0.75,
    ),

    # ── 27. Documentation: Update Docs ──
    Workflow(
        category="documentation", name="update_docs",
        description="Update documentation after code changes",
        steps=[
            WorkflowStep("bash", "Check git diff for recent changes"),
            WorkflowStep("glob_search", "Find documentation files"),
            WorkflowStep("read_file", "Read current docs"),
            WorkflowStep("edit_file", "Update documentation"),
        ],
        success_rate=0.80,
    ),
]
# fmt: on


def get_workflows_by_category(category: str) -> list[Workflow]:
    """Get all workflows in a category."""
    return [w for w in WORKFLOWS if w.category == category]


def get_surgical_workflows() -> list[Workflow]:
    """Get workflows marked as surgical (few tool calls)."""
    return [w for w in WORKFLOWS if w.is_surgical]


def get_all_categories() -> list[str]:
    """Get unique category names."""
    return sorted(set(w.category for w in WORKFLOWS))
