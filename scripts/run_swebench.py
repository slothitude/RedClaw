"""SWE-bench adapter — run RedClaw on SWE-bench instances.

Usage:
    # Run on a single instance
    python scripts/run_swebench.py --instance django__django-12345

    # Run on SWE-bench Lite (all 300 issues)
    python scripts/run_swebench.py --dataset lite

    # Run on a sample of N issues
    python scripts/run_swebench.py --dataset lite --sample 10

    # Custom provider/model
    python scripts/run_swebench.py --instance django__django-12345 --provider openai --model gpt-4o

    # Specify output file
    python scripts/run_swebench.py --dataset lite --sample 10 --output results.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DATASETS = {
    "lite": "princeton-nlp/SWE-bench_Lite",
    "classic": "princeton-nlp/SWE-bench",
    "verified": "princeton-nlp/SWE-bench_Verified",
}

SYSTEM_PROMPT = """\
You are an expert software engineer tasked with fixing a GitHub issue.

## Rules
1. Explore the repository to understand the codebase structure
2. Read the issue description carefully
3. Make the MINIMAL changes needed to fix the issue
4. Do NOT add tests, comments, or refactor unrelated code
5. When done, output ONLY the git diff of your changes, wrapped in:
   <<PATCH
   diff --git a/...
   ...
   PATCH
"""


def load_instances(dataset: str, instance_ids: list[str] | None = None, sample: int | None = None) -> list[dict]:
    """Load SWE-bench instances from HuggingFace datasets."""
    from datasets import load_dataset

    hf_name = DATASETS.get(dataset, dataset)
    logger.info("Loading dataset: %s", hf_name)
    ds = load_dataset(hf_name, split="test")

    if instance_ids:
        ds = ds.filter(lambda x: x["instance_id"] in instance_ids)

    instances = list(ds)
    if sample and sample < len(instances):
        import random
        random.seed(42)
        instances = random.sample(instances, sample)

    logger.info("Loaded %d instances", len(instances))
    return instances


def checkout_repo(instance: dict, workdir: str) -> str:
    """Clone the repo and checkout the base commit."""
    repo = instance["repo"]
    base_commit = instance["base_commit"]
    instance_id = instance["instance_id"]

    repo_url = f"https://github.com/{repo}.git"
    repo_dir = os.path.join(workdir, instance_id.replace("/", "__"))

    logger.info("Cloning %s @ %s", repo, base_commit[:8])
    subprocess.run(["git", "clone", "--quiet", repo_url, repo_dir], check=True, capture_output=True)
    subprocess.run(
        ["git", "checkout", "--quiet", base_commit],
        cwd=repo_dir, check=True, capture_output=True,
    )
    return repo_dir


def extract_patch(output: str) -> str | None:
    """Extract the git diff patch from RedClaw's output."""
    # Look for <<PATCH ... PATCH block
    if "<<PATCH" in output and "PATCH" in output.split("<<PATCH", 1)[1]:
        patch = output.split("<<PATCH", 1)[1].split("PATCH", 1)[0].strip()
        if patch.startswith("diff --git"):
            return patch

    # Fallback: look for any diff --git block
    lines = output.split("\n")
    in_diff = False
    diff_lines = []
    for line in lines:
        if line.startswith("diff --git"):
            in_diff = True
        if in_diff:
            diff_lines.append(line)
    if diff_lines:
        return "\n".join(diff_lines)

    return None


async def run_redclaw(
    repo_dir: str,
    problem_statement: str,
    provider_name: str,
    model: str,
    base_url: str | None = None,
    timeout: int = 600,
) -> str:
    """Run RedClaw on a single issue using the runtime directly."""
    import uuid

    from redclaw.api.client import LLMClient
    from redclaw.api.providers import get_provider
    from redclaw.runtime.conversation import ConversationCallbacks, ConversationRuntime
    from redclaw.runtime.permissions import PermissionMode, PermissionPolicy
    from redclaw.runtime.session import Session
    from redclaw.runtime.usage import UsageTracker
    from redclaw.tools.registry import ToolExecutor

    provider = get_provider(provider_name, base_url)
    client = LLMClient(provider)
    session = Session(id=uuid.uuid4().hex[:8])
    session.working_dir = repo_dir
    tools = ToolExecutor(working_dir=repo_dir)
    policy = PermissionPolicy(mode=PermissionMode.DANGER_FULL_ACCESS)
    tracker = UsageTracker()

    rt = ConversationRuntime(
        client=client,
        provider=provider,
        model=model,
        session=session,
        tools=tools,
        permission_policy=policy,
        usage_tracker=tracker,
        working_dir=repo_dir,
        system_prompt=SYSTEM_PROMPT,
        max_tool_rounds=30,
    )

    prompt = f"""\
## Issue to Fix

{problem_statement}

## Instructions

Fix this issue. Make minimal changes. Output the git diff when done.
"""

    collected_text = ""

    async def on_text_delta(text: str) -> None:
        nonlocal collected_text
        collected_text += text

    async def on_tool_begin(tool_id: str, name: str, input_json: str) -> None:
        logger.info("  Tool: %s", name)

    async def on_tool_result(tool_id: str, result: str, is_error: bool) -> None:
        if is_error:
            logger.warning("  Tool error: %s", result[:200])

    async def on_error(msg: str) -> None:
        logger.error("  Error: %s", msg)

    cb = ConversationCallbacks(
        on_text_delta=on_text_delta,
        on_tool_begin=on_tool_begin,
        on_tool_result=on_tool_result,
        on_error=on_error,
    )

    try:
        summary = await asyncio.wait_for(
            rt.run_turn(prompt, cb),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("  Timed out after %ds", timeout)
        collected_text += "\n\n[TIMEOUT]"
    except Exception as e:
        logger.error("  Exception: %s", e)
        collected_text += f"\n\n[ERROR: {e}]"

    await client.close()
    return collected_text


def run_instance(
    instance: dict,
    provider: str,
    model: str,
    base_url: str | None = None,
    workdir: str | None = None,
    keep_repos: bool = False,
) -> dict:
    """Run RedClaw on a single SWE-bench instance. Returns result dict."""
    instance_id = instance["instance_id"]
    logger.info("=== %s ===", instance_id)
    start = time.time()

    # Setup workdir
    if workdir is None:
        workdir = tempfile.mkdtemp(prefix="swebench_")

    try:
        # Checkout repo
        repo_dir = checkout_repo(instance, workdir)

        # Run RedClaw
        output = asyncio.run(run_redclaw(
            repo_dir=repo_dir,
            problem_statement=instance["problem_statement"],
            provider_name=provider,
            model=model,
            base_url=base_url,
        ))

        # Extract patch
        patch = extract_patch(output)

        elapsed = time.time() - start
        logger.info("  Done in %.0fs — patch: %s", elapsed, "YES" if patch else "NO")

        result = {
            "instance_id": instance_id,
            "model_patch": patch or "",
            "model_name_or_path": f"RedClaw ({provider}/{model})",
            "elapsed_seconds": round(elapsed, 1),
            "has_patch": patch is not None,
        }

    except Exception as e:
        elapsed = time.time() - start
        logger.error("  Failed: %s", e)
        result = {
            "instance_id": instance_id,
            "model_patch": "",
            "model_name_or_path": f"RedClaw ({provider}/{model})",
            "elapsed_seconds": round(elapsed, 1),
            "has_patch": False,
            "error": str(e),
        }

    finally:
        if not keep_repos:
            import shutil
            repo_dir = os.path.join(workdir, instance_id.replace("/", "__"))
            if os.path.exists(repo_dir):
                shutil.rmtree(repo_dir, ignore_errors=True)

    return result


def main():
    parser = argparse.ArgumentParser(description="Run RedClaw on SWE-bench")
    parser.add_argument("--instance", nargs="*", help="Specific instance IDs to run")
    parser.add_argument("--dataset", choices=list(DATASETS.keys()), default="lite", help="Dataset to use")
    parser.add_argument("--sample", type=int, help="Run on a random sample of N issues")
    parser.add_argument("--provider", default="openai", help="LLM provider")
    parser.add_argument("--model", default="gpt-4o", help="Model name")
    parser.add_argument("--base-url", default=None, help="Custom API base URL")
    parser.add_argument("--output", default="swebench_results.json", help="Output JSON file")
    parser.add_argument("--workdir", default=None, help="Working directory for repo clones")
    parser.add_argument("--keep-repos", action="store_true", help="Keep cloned repos after run")
    parser.add_argument("--timeout", type=int, default=600, help="Timeout per instance (seconds)")
    args = parser.parse_args()

    if not args.instance and not args.dataset:
        parser.error("Specify --instance or --dataset")

    # Load instances
    instances = load_instances(args.dataset, args.instance, args.sample)
    if not instances:
        logger.error("No instances to run")
        return

    logger.info("Running %d instances with %s/%s", len(instances), args.provider, args.model)

    # Run
    results = []
    stats = {"total": 0, "patched": 0, "failed": 0, "total_time": 0.0}

    for i, instance in enumerate(instances):
        logger.info("[%d/%d]", i + 1, len(instances))
        result = run_instance(
            instance, args.provider, args.model, args.base_url,
            workdir=args.workdir, keep_repos=args.keep_repos,
        )
        results.append(result)
        stats["total"] += 1
        stats["patched"] += int(result.get("has_patch", False))
        stats["failed"] += int(not result.get("has_patch", False))
        stats["total_time"] += result.get("elapsed_seconds", 0)

    # Save results
    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)
    logger.info("Results saved to %s", args.output)

    # Print summary
    logger.info("=== Summary ===")
    logger.info("Total: %d | Patched: %d (%.1f%%) | Failed: %d | Time: %.0fs",
                stats["total"], stats["patched"],
                100 * stats["patched"] / max(stats["total"], 1),
                stats["failed"], stats["total_time"])


if __name__ == "__main__":
    main()
