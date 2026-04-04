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

    # Enable AGI mode (SOUL, DNA, Dream, Karma — agent learns across instances)
    python scripts/run_swebench.py --dataset lite --sample 20 --provider zai --model glm-5.1 --agi
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


def setup_agi(
    provider_config: object,
    client: object,
    model: str,
) -> dict:
    """Initialize AGI components for cross-instance learning.

    Returns a dict of shared AGI components that persist across all instances.
    """
    from redclaw.crypt.crypt import Crypt
    from redclaw.crypt.dna import DNAManager
    from redclaw.crypt.dream import DreamSynthesizer
    from redclaw.crypt.karma import KarmaObserver
    from redclaw.runtime.event_bus import EventBus, EventLogger
    from redclaw.runtime.soul import load_soul

    soul_text = load_soul()

    event_bus = EventBus()
    event_bus.subscribe(EventLogger())

    crypt = Crypt()
    dna_manager = DNAManager()
    dream = DreamSynthesizer(client, provider_config, model)

    crypt._dream_synthesizer = dream
    crypt._dna_manager = dna_manager

    karma = KarmaObserver(soul_text, event_bus)
    event_bus.subscribe(karma)

    logger.info("AGI mode enabled — SOUL loaded, Crypt/DNA/Dream/Karma wired")

    return {
        "soul_text": soul_text,
        "event_bus": event_bus,
        "crypt": crypt,
        "dna_manager": dna_manager,
        "dream": dream,
        "karma": karma,
    }


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


def _inject_counterfactual(
    agi_context: dict,
    instance_id: str,
    success: bool,
    tool_calls: int,
) -> None:
    """Inject counterfactual reasoning — compare success vs failure patterns."""
    if not success and tool_calls > 15:
        return  # Can't learn much from excessive tool calls
    if not agi_context:
        return
    try:
        from redclaw.runtime.subagent_types import SubagentType

        # Load recent successful instances for comparison
        results = json.load(open("swebench_results.json")) if Path("swebench_results.json").exists() else []
        successes = [r for r in results if r.get("has_patch")]
        if not successes:
            return

        avg_success_tools = sum(r["tool_calls"] for r in successes if "tool_calls" > 0) / len(successes)
        lesson = (
            f"Counterfactual: {instance_id} used {tool_calls} tool calls (failed). "
            f"Recent successes averaged {avg_success_tools:.0f} calls — "
            f"fewer, more targeted tool usage correlates to better outcomes."
        )
        agi_context["crypt"].append_bloodline_lesson(
            SubagentType.CODER, lesson, "Tool Insights",
        )
    except Exception as e:
        logger.debug("Counterfactual injection failed: %s", e)


def checkout_repo(instance: dict, workdir: str) -> str:
    """Clone the repo and checkout the base commit."""
    repo = instance["repo"]
    base_commit = instance["base_commit"]
    instance_id = instance["instance_id"]

    repo_url = f"https://github.com/{repo}.git"
    repo_dir = os.path.join(workdir, instance_id.replace("/", "__"))

    logger.info("Cloning %s @ %s", repo, base_commit[:8])
    subprocess.run(["git", "clone", "--quiet", repo_url, repo_dir], check=True, capture_output=True)

    # Enable long paths on Windows to prevent checkout failures
    if os.name == "nt":
        subprocess.run(
            ["git", "config", "core.longpaths", "true"],
            cwd=repo_dir, check=False, capture_output=True,
        )

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
    agi_context: dict | None = None,
) -> tuple[str, int]:
    """Run RedClaw on a single issue using the runtime directly.

    Returns (output_text, tool_call_count).
    """
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

    # AGI wiring
    soul_text = ""
    subagent_spawner = None
    system_prompt = SYSTEM_PROMPT
    effective_timeout = timeout
    max_rounds = 30

    if agi_context:
        soul_text = agi_context["soul_text"]

        # Load accumulated bloodline wisdom and inject into system prompt
        from redclaw.runtime.subagent_types import SubagentType
        bloodline_wisdom = agi_context["crypt"].load_bloodline_wisdom(SubagentType.CODER)
        if bloodline_wisdom.strip():
            system_prompt += f"\n\n## Accumulated Wisdom from Previous Runs\n{bloodline_wisdom}\n"
            logger.info("  AGI: Injected bloodline wisdom (%d bytes)", len(bloodline_wisdom))

        # Apply DNA-derived modifiers
        modifiers = agi_context["dna_manager"].get_modifiers(SubagentType.CODER)
        effective_timeout = int(timeout * modifiers.timeout_multiplier)
        max_rounds = max(10, 30 + modifiers.max_turns_modifier)
        dna_guidance = agi_context["dna_manager"].get_prompt_guidance(SubagentType.CODER)
        if dna_guidance:
            system_prompt += f"\n\n## Behavioral Guidance\n{dna_guidance}\n"
        logger.info(
            "  AGI: DNA modifiers — timeout=%ds (x%.2f), max_rounds=%d, style=%s",
            effective_timeout, modifiers.timeout_multiplier, max_rounds, modifiers.prompt_style,
        )

        from redclaw.runtime.subagent import SubagentSpawner
        subagent_spawner = SubagentSpawner(
            client=client,
            provider=provider,
            model=model,
            tools=tools,
            crypt=agi_context["crypt"],
        )
        subagent_spawner._dna_manager = agi_context["dna_manager"]

    rt = ConversationRuntime(
        client=client,
        provider=provider,
        model=model,
        session=session,
        tools=tools,
        permission_policy=policy,
        usage_tracker=tracker,
        working_dir=repo_dir,
        system_prompt=system_prompt,
        max_tool_rounds=max_rounds,
        soul_text=soul_text,
        subagent_spawner=subagent_spawner,
    )

    prompt = f"""\
## Issue to Fix

{problem_statement}

## Instructions

Fix this issue. Make minimal changes. Output the git diff when done.
"""

    collected_text = ""
    tool_call_count = 0

    async def on_text_delta(text: str) -> None:
        nonlocal collected_text
        collected_text += text

    async def on_tool_begin(tool_id: str, name: str, input_json: str) -> None:
        nonlocal tool_call_count
        tool_call_count += 1
        logger.info("  Tool [%d]: %s", tool_call_count, name)

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
            timeout=effective_timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("  Timed out after %ds", effective_timeout)
        collected_text += "\n\n[TIMEOUT]"
    except Exception as e:
        logger.error("  Exception: %s", e)
        collected_text += f"\n\n[ERROR: {e}]"

    await client.close()
    return collected_text, tool_call_count


def run_instance(
    instance: dict,
    provider: str,
    model: str,
    base_url: str | None = None,
    workdir: str | None = None,
    keep_repos: bool = False,
    agi_context: dict | None = None,
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
        output, tool_calls = asyncio.run(run_redclaw(
            repo_dir=repo_dir,
            problem_statement=instance["problem_statement"],
            provider_name=provider,
            model=model,
            base_url=base_url,
            agi_context=agi_context,
        ))

        # Extract patch from LLM text output
        patch = extract_patch(output)

        # Fallback: if no patch in text, check actual file changes via git diff
        if not patch:
            diff_result = subprocess.run(
                ["git", "diff"],
                cwd=repo_dir, capture_output=True, text=True,
            )
            if diff_result.stdout.strip():
                patch = diff_result.stdout.strip()
                logger.info("  Patch from git diff (%d bytes)", len(patch))

        elapsed = time.time() - start
        logger.info("  Done in %.0fs — patch: %s", elapsed, "YES" if patch else "NO")

        result = {
            "instance_id": instance_id,
            "model_patch": patch or "",
            "model_name_or_path": f"RedClaw ({provider}/{model})",
            "elapsed_seconds": round(elapsed, 1),
            "has_patch": patch is not None,
        }

        # Entomb result for AGI wisdom inheritance
        if agi_context:
            from redclaw.runtime.subagent import SubagentResult
            from redclaw.runtime.subagent_types import SubagentType
            from redclaw.crypt.extractor import extract_lessons

            sub_result = SubagentResult(
                success=result["has_patch"],
                output=output[:2000],
                error=result.get("error"),
                tool_calls=tool_calls,
            )
            agi_context["crypt"].entomb(
                sub_result,
                instance["problem_statement"][:500],
                SubagentType.CODER,
            )
            logger.info("  Entombed: success=%s", result["has_patch"])

            # ── Immediate lesson injection (between instances) ──
            lessons = extract_lessons(sub_result, instance["problem_statement"][:500], SubagentType.CODER)
            for lesson in lessons:
                if lesson.category == "Warnings":
                    agi_context["crypt"].append_bloodline_lesson(SubagentType.CODER, lesson.text, lesson.category)
                    logger.info("  Immediate lesson: %s", lesson.text[:80])

            # ── Counterfactual reasoning ──
            _inject_counterfactual(agi_context, instance_id, result["has_patch"], tool_calls)

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

        # Entomb failure too
        if agi_context:
            from redclaw.runtime.subagent import SubagentResult
            from redclaw.runtime.subagent_types import SubagentType

            sub_result = SubagentResult(
                success=False,
                output="",
                error=str(e),
                tool_calls=0,  # checkout failed, no tool calls made
            )
            agi_context["crypt"].entomb(
                sub_result,
                instance["problem_statement"][:500],
                SubagentType.CODER,
            )
            logger.info("  Entombed failure: %s", str(e)[:100])

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
    parser.add_argument("--agi", action="store_true", help="Enable AGI mode (SOUL, DNA, Dream, Karma — agent learns across instances)")
    args = parser.parse_args()

    if not args.instance and not args.dataset:
        parser.error("Specify --instance or --dataset")

    # Load instances
    instances = load_instances(args.dataset, args.instance, args.sample)
    if not instances:
        logger.error("No instances to run")
        return

    # Initialize AGI components if requested
    agi_context = None
    if args.agi:
        from redclaw.api.client import LLMClient
        from redclaw.api.providers import get_provider

        provider_config = get_provider(args.provider, args.base_url)
        client = LLMClient(provider_config)
        agi_context = setup_agi(provider_config, client, args.model)

    logger.info("Running %d instances with %s/%s%s",
                len(instances), args.provider, args.model,
                " +AGI" if agi_context else "")

    # Run — save results incrementally after each instance
    results = []
    stats = {"total": 0, "patched": 0, "failed": 0, "total_time": 0.0}

    for i, instance in enumerate(instances):
        logger.info("[%d/%d]", i + 1, len(instances))
        result = run_instance(
            instance, args.provider, args.model, args.base_url,
            workdir=args.workdir, keep_repos=args.keep_repos,
            agi_context=agi_context,
        )
        results.append(result)
        stats["total"] += 1
        stats["patched"] += int(result.get("has_patch", False))
        stats["failed"] += int(not result.get("has_patch", False))
        stats["total_time"] += result.get("elapsed_seconds", 0)

        # Incremental save — prevents data loss on crash
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        logger.info("  Saved %d/%d results to %s", len(results), len(instances), args.output)

        # Trigger dream synthesis if conditions met (AGI mode)
        if agi_context and agi_context.get("dream"):
            dream = agi_context["dream"]
            crypt = agi_context["crypt"]
            total_entombed = len(list(crypt.entombed_dir.glob("sub-*.json")))
            if dream.should_dream(total_entombed):
                logger.info("  Dream synthesis triggered (%d entombed)...", total_entombed)
                try:
                    dream_result = asyncio.run(dream.dream(crypt))
                    logger.info("  Dream complete: %d records, %d insights",
                                dream_result.records_processed, dream_result.insights_generated)
                except Exception as e:
                    logger.warning("  Dream failed: %s", e)

    # Print summary
    logger.info("=== Summary ===")
    logger.info("Total: %d | Patched: %d (%.1f%%) | Failed: %d | Time: %.0fs",
                stats["total"], stats["patched"],
                100 * stats["patched"] / max(stats["total"], 1),
                stats["failed"], stats["total_time"])


if __name__ == "__main__":
    main()
