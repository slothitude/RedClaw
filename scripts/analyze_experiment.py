"""Analyze SWE-bench self-learning experiment results.

Compares 2+ result JSON files with deep efficiency metrics:
  - Tool Efficiency Score (TES): success / tool_calls
  - Entropy Score: unique_tools / total_calls
  - Strategy Classification: surgical / exploratory / brute_force
  - Scatter plot: tool_calls vs success (Figure 1)

Usage:
    python scripts/analyze_experiment.py results_a.json results_d.json --labels "Empty" "Full"
    python scripts/analyze_experiment.py results_a.json results_d.json --plot scatter.png
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path


# ── Metric helpers ──────────────────────────────────────────

def classify_strategy(tool_calls: int) -> str:
    if tool_calls <= 7:
        return "surgical"
    elif tool_calls <= 20:
        return "exploratory"
    else:
        return "brute_force"


def compute_tes(patched: bool, tool_calls: int) -> float:
    """Tool Efficiency Score: 1/calls if success, 0 otherwise."""
    if not patched or tool_calls == 0:
        return 0.0
    return 1.0 / tool_calls


def compute_entropy(tool_names: list[str]) -> float:
    """Entropy: unique tools / total calls. Low = focused, High = chaotic."""
    if not tool_names:
        return 0.0
    return len(set(tool_names)) / len(tool_names)


def find_convergence_depth(tool_names: list[str]) -> int | None:
    """Step index (1-based) where first file-modifying tool is used.

    Returns None if no file-modifying tool was used.
    File-modifying: edit_file, write_file
    """
    for i, name in enumerate(tool_names, 1):
        if name in ("edit_file", "write_file"):
            return i
    return None


# ── Data loading ────────────────────────────────────────────

def load_results(path: str) -> list[dict]:
    with open(path) as f:
        return json.load(f)


def enrich_result(r: dict) -> dict:
    """Add computed metrics to a single result."""
    patched = r.get("has_patch", False)
    tc = r.get("tool_calls", 0)
    names = r.get("tool_names", [])

    # For legacy results without tool_names, synthesize from count
    if not names and tc > 0:
        names = ["unknown"] * tc

    r["_tes"] = compute_tes(patched, tc)
    r["_entropy"] = compute_entropy(names)
    r["_strategy"] = classify_strategy(tc)
    r["_convergence"] = find_convergence_depth(names)
    r["_unique_tools"] = len(set(names)) if names else 0
    return r


# ── Aggregate stats ─────────────────────────────────────────

def compute_stats(results: list[dict], label: str) -> dict:
    results = [enrich_result(r) for r in results]
    total = len(results)
    patched = [r for r in results if r.get("has_patch")]
    failed = [r for r in results if not r.get("has_patch") and not r.get("error")]
    errored = [r for r in results if r.get("error")]

    patch_rate = len(patched) / total if total else 0

    tc_success = [r.get("tool_calls", 0) for r in patched if r.get("tool_calls", 0) > 0]
    tc_fail = [r.get("tool_calls", 0) for r in failed if r.get("tool_calls", 0) > 0]
    tc_all = [r.get("tool_calls", 0) for r in results if r.get("tool_calls", 0) > 0]

    avg_tc_s = sum(tc_success) / len(tc_success) if tc_success else 0
    avg_tc_f = sum(tc_fail) / len(tc_fail) if tc_fail else 0
    avg_tc = sum(tc_all) / len(tc_all) if tc_all else 0

    # Variance in tool calls (success only)
    var_tc_s = (sum((x - avg_tc_s) ** 2 for x in tc_success) / len(tc_success)) if len(tc_success) > 1 else 0

    # TES
    tes_all = [r["_tes"] for r in results]
    tes_success = [r["_tes"] for r in patched]
    avg_tes = sum(tes_all) / len(tes_all) if tes_all else 0
    avg_tes_s = sum(tes_success) / len(tes_success) if tes_success else 0

    # Entropy
    ent_all = [r["_entropy"] for r in results if r.get("tool_calls", 0) > 0]
    avg_entropy = sum(ent_all) / len(ent_all) if ent_all else 0

    # Strategy distribution
    strategies = Counter(r["_strategy"] for r in results)
    surgical_rate = strategies.get("surgical", 0) / total if total else 0
    exploratory_rate = strategies.get("exploratory", 0) / total if total else 0
    brute_rate = strategies.get("brute_force", 0) / total if total else 0

    # Convergence depth (where first edit happens)
    conv_depths = [r["_convergence"] for r in results if r["_convergence"] is not None]
    avg_convergence = sum(conv_depths) / len(conv_depths) if conv_depths else None

    # Time
    times_s = [r.get("elapsed_seconds", 0) for r in patched]
    avg_time_s = sum(times_s) / len(times_s) if times_s else 0

    return {
        "label": label,
        "total": total,
        "patched": len(patched),
        "failed": len(failed),
        "errored": len(errored),
        "patch_rate": patch_rate,
        "avg_tools": avg_tc,
        "avg_tools_success": avg_tc_s,
        "avg_tools_fail": avg_tc_f,
        "var_tools_success": var_tc_s,
        "avg_tes": avg_tes,
        "avg_tes_success": avg_tes_s,
        "avg_entropy": avg_entropy,
        "surgical_rate": surgical_rate,
        "surgical_count": strategies.get("surgical", 0),
        "exploratory_rate": exploratory_rate,
        "exploratory_count": strategies.get("exploratory", 0),
        "brute_rate": brute_rate,
        "brute_count": strategies.get("brute_force", 0),
        "avg_convergence": avg_convergence,
        "avg_time_success": avg_time_s,
        "tool_calls_success": tc_success,
        "tool_calls_fail": tc_fail,
        "tool_calls_all": tc_all,
        "results": results,
    }


# ── Output ──────────────────────────────────────────────────

def print_summary_table(stats_list: list[dict]) -> None:
    print("\n## Results Summary\n")
    print(f"| {'Condition':<12} | {'Patched':<8} | {'Rate':>5} | {'Avg TC':>6} | {'Avg TC (ok)':>10} | {'Var TC':>6} | {'TES':>5} | {'Entropy':>7} | {'Surgical':>8} | {'Avg Conv':>8} |")
    print(f"|{'':-<14}|{'':-<10}|{'':-<7}|{'':-<8}|{'':-<12}|{'':-<8}|{'':-<7}|{'':-<9}|{'':-<10}|{'':-<10}|")
    for s in stats_list:
        conv = f"{s['avg_convergence']:.1f}" if s["avg_convergence"] is not None else "-"
        print(
            f"| {s['label']:<12} "
            f"| {s['patched']}/{s['total']:<4} "
            f"| {s['patch_rate']:.0%} "
            f"| {s['avg_tools']:.1f} "
            f"| {s['avg_tools_success']:.1f} "
            f"| {s['var_tools_success']:.1f} "
            f"| {s['avg_tes']:.3f} "
            f"| {s['avg_entropy']:.2f} "
            f"| {s['surgical_rate']:.0%} "
            f"| {conv} |"
        )

    # Strategy breakdown
    print("\n## Strategy Distribution\n")
    print(f"| {'Condition':<12} | {'Surgical (<=7)':>14} | {'Exploratory (8-20)':>18} | {'Brute force (>20)':>18} |")
    print(f"|{'':-<14}|{'':-<16}|{'':-<20}|{'':-<20}|")
    for s in stats_list:
        print(
            f"| {s['label']:<12} "
            f"| {s['surgical_count']}/{s['total']} ({s['surgical_rate']:.0%})  "
            f"| {s['exploratory_count']}/{s['total']} ({s['exploratory_rate']:.0%})  "
            f"| {s['brute_count']}/{s['total']} ({s['brute_rate']:.0%})   |"
        )


def print_per_instance(stats_list: list[dict]) -> None:
    """Print per-instance results with strategy tags."""
    if not stats_list:
        return

    labels = [s["label"] for s in stats_list]
    # Union of instance IDs
    all_ids = []
    seen = set()
    for s in stats_list:
        for r in s["results"]:
            iid = r["instance_id"]
            if iid not in seen:
                all_ids.append(iid)
                seen.add(iid)

    lookup = {}
    for s in stats_list:
        lookup[s["label"]] = {r["instance_id"]: r for r in s["results"]}

    def fmt(r: dict) -> str:
        if r.get("error"):
            return f"ERR({r['error'][:15]})"
        p = "Y" if r.get("has_patch") else "N"
        tc = r.get("tool_calls", 0)
        strat = r["_strategy"]
        ent = r["_entropy"]
        return f"{p} tc={tc:<3} {strat:<12} ent={ent:.2f}"

    print("\n## Per-Instance Detail\n")
    header = "| Instance | " + " | ".join(labels) + " |"
    sep = "|" + "|".join(["---"] * (1 + len(labels))) + "|"
    print(header)
    print(sep)

    for iid in all_ids:
        row = f"| {iid} "
        for label in labels:
            r = lookup[label].get(iid, {})
            if not r:
                row += f"| - "
            else:
                row += f"| {fmt(r)} "
        row += "|"
        print(row)


def print_trajectory_diff(stats_list: list[dict]) -> None:
    """Show trajectory diffs where outcomes differ between conditions."""
    if len(stats_list) < 2:
        return

    labels = [s["label"] for s in stats_list]
    lookup = {}
    for s in stats_list:
        lookup[s["label"]] = {r["instance_id"]: r for r in s["results"]}

    # Find instances where patch outcome flips
    print("\n## Trajectory Transforms (outcome flips)\n")
    base = stats_list[0]
    for s in stats_list[1:]:
        flips = []
        for r_base in base["results"]:
            iid = r_base["instance_id"]
            r_trt = lookup[s["label"]].get(iid)
            if not r_trt:
                continue
            base_ok = r_base.get("has_patch", False)
            trt_ok = r_trt.get("has_patch", False)
            if base_ok != trt_ok:
                flips.append((iid, r_base, r_trt))

        if not flips:
            print(f"No outcome flips between {base['label']} and {s['label']}.")
            continue

        print(f"### {base['label']} → {s['label']} ({len(flips)} flips)\n")
        for iid, r_base, r_trt in flips:
            base_strat = r_base["_strategy"]
            trt_strat = r_trt["_strategy"]
            direction = "GAINED" if r_trt.get("has_patch") else "LOST"
            print(f"**{iid}** ({direction})")
            print(f"  {base['label']}: tc={r_base['tool_calls']} {base_strat} {'PATCH' if r_base.get('has_patch') else 'FAIL'}")
            print(f"  {s['label']}: tc={r_trt['tool_calls']} {trt_strat} {'PATCH' if r_trt.get('has_patch') else 'FAIL'}")

            # Show tool sequence diff
            base_names = r_base.get("tool_names", [])
            trt_names = r_trt.get("tool_names", [])
            if base_names or trt_names:
                print(f"  {base['label']} sequence: {' -> '.join(base_names[:10])}{'...' if len(base_names) > 10 else ''}")
                print(f"  {s['label']} sequence: {' -> '.join(trt_names[:10])}{'...' if len(trt_names) > 10 else ''}")
            print()


def print_pairwise_tests(stats_list: list[dict]) -> None:
    """Run pairwise statistical tests: each vs first (control)."""
    try:
        from scipy.stats import fisher_exact, mannwhitneyu
    except ImportError:
        print("\n**Note:** Install scipy for statistical tests: `pip install scipy`\n")
        return

    control = stats_list[0]
    for treatment in stats_list[1:]:
        c = control
        t = treatment
        cl = c["label"]
        tl = t["label"]

        print(f"\n### {tl} vs {cl}\n")

        # H1: Patch rate (Fisher's exact)
        a, b = t["patched"], t["total"] - t["patched"]
        cc, d = c["patched"], c["total"] - c["patched"]
        _, p_patch = fisher_exact([[a, b], [cc, d]], alternative="greater")
        sig = "*" if p_patch < 0.05 else ""
        print(f"**H0 Patch rate:** {tl}={t['patch_rate']:.1%} vs {cl}={c['patch_rate']:.1%}")
        print(f"- Fisher's exact p={p_patch:.4f} {sig}")

        # H1: Tool calls lower in treatment (Mann-Whitney U)
        if t["tool_calls_all"] and c["tool_calls_all"]:
            _, p_tc = mannwhitneyu(t["tool_calls_all"], c["tool_calls_all"], alternative="less")
            sig = "*" if p_tc < 0.05 else ""
            print(f"\n**H1 Tool calls (all):** {tl}={t['avg_tools']:.1f} vs {cl}={c['avg_tools']:.1f}")
            print(f"- Mann-Whitney U p={p_tc:.4f} {sig}")

        if t["tool_calls_success"] and c["tool_calls_success"]:
            _, p_tcs = mannwhitneyu(t["tool_calls_success"], c["tool_calls_success"], alternative="less")
            sig = "*" if p_tcs < 0.05 else ""
            print(f"\n**H1 Tool calls (success):** {tl}={t['avg_tools_success']:.1f} vs {cl}={c['avg_tools_success']:.1f}")
            print(f"- Mann-Whitney U p={p_tcs:.4f} {sig}")

        # H2: Surgical rate higher (chi-squared / Fisher)
        # Simplified: compare surgical counts
        t_surg = t["surgical_count"]
        t_nonsurg = t["total"] - t_surg
        c_surg = c["surgical_count"]
        c_nonsurg = c["total"] - c_surg
        _, p_surg = fisher_exact([[t_surg, t_nonsurg], [c_surg, c_nonsurg]], alternative="greater")
        sig = "*" if p_surg < 0.05 else ""
        print(f"\n**H2 Surgical rate:** {tl}={t['surgical_rate']:.1%} vs {cl}={c['surgical_rate']:.1%}")
        print(f"- Fisher's exact p={p_surg:.4f} {sig}")

        # H3: Variance reduction (Levene-like: ratio of variances)
        if len(t["tool_calls_success"]) > 1 and len(c["tool_calls_success"]) > 1:
            var_t = t["var_tools_success"]
            var_c = c["var_tools_success"]
            ratio = var_c / var_t if var_t > 0 else float("inf")
            print(f"\n**H3 Variance (success):** {tl}={var_t:.1f} vs {cl}={var_c:.1f} (ratio={ratio:.2f})")


def generate_scatter(stats_list: list[dict], output_path: str) -> None:
    """Generate scatter plot: X=tool_calls, Y=success(0/1), colored by condition."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print(f"\n**Note:** Install matplotlib for scatter plot: `pip install matplotlib`")
        return

    fig, ax = plt.subplots(figsize=(10, 5))

    colors = ["#e74c3c", "#2ecc71", "#3498db", "#f39c12", "#9b59b6"]
    offsets = [0.05, -0.05, 0.10, -0.10, 0.15]

    for i, s in enumerate(stats_list):
        color = colors[i % len(colors)]
        y_offset = offsets[i % len(offsets)]
        xs = [r.get("tool_calls", 0) for r in s["results"] if r.get("tool_calls", 0) > 0]
        ys = [1 + y_offset if r.get("has_patch") else 0 + y_offset for r in s["results"] if r.get("tool_calls", 0) > 0]
        ax.scatter(xs, ys, c=color, label=s["label"], alpha=0.7, s=80, edgecolors="white", linewidth=0.5)

    ax.set_xlabel("Tool Calls", fontsize=12)
    ax.set_ylabel("Patch Success", fontsize=12)
    ax.set_yticks([0, 1])
    ax.set_yticklabels(["FAIL", "PATCH"])
    ax.set_title("Tool Calls vs Patch Success (Figure 1)", fontsize=14)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)

    # Add strategy zone shading
    ax.axvspan(0, 7, alpha=0.05, color="green", label="Surgical zone")
    ax.axvspan(7, 20, alpha=0.05, color="yellow")
    ax.axvspan(20, 35, alpha=0.05, color="red")
    ax.text(3.5, 1.15, "surgical", ha="center", fontsize=8, color="green", alpha=0.6)
    ax.text(13.5, 1.15, "exploratory", ha="center", fontsize=8, color="orange", alpha=0.6)
    ax.text(27, 1.15, "brute force", ha="center", fontsize=8, color="red", alpha=0.6)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    print(f"\nScatter plot saved to: {output_path}")


# ── Main ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Analyze SWE-bench experiment results with efficiency metrics")
    parser.add_argument("results", nargs="+", help="Result JSON files (at least 2)")
    parser.add_argument("--labels", nargs="+", help="Labels for each file (default: A, B, C, ...)")
    parser.add_argument("--plot", default=None, help="Output path for scatter plot PNG")
    args = parser.parse_args()

    if len(args.results) < 1:
        parser.error("Need at least 1 result file")

    default_labels = [chr(65 + i) for i in range(len(args.results))]
    labels = args.labels or default_labels

    if len(labels) != len(args.results):
        parser.error(f"Expected {len(args.results)} labels, got {len(labels)}")

    # Load and compute
    results_list = [load_results(p) for p in args.results]
    stats_list = [compute_stats(r, l) for r, l in zip(results_list, labels)]

    # Output
    print_summary_table(stats_list)
    print_per_instance(stats_list)
    print_trajectory_diff(stats_list)

    if len(stats_list) >= 2:
        print_pairwise_tests(stats_list)

    if args.plot:
        generate_scatter(stats_list, args.plot)
    else:
        # Default: try to generate if matplotlib is available
        try:
            import matplotlib  # noqa: F401
            default_path = str(Path(args.results[0]).parent / "scatter_plot.png")
            generate_scatter(stats_list, default_path)
        except ImportError:
            pass

    # Print the core claim
    if len(stats_list) >= 2:
        print("\n## Core Comparison: Entropy Reduction\n")
        c = stats_list[0]
        t = stats_list[-1]
        delta_entropy = c["avg_entropy"] - t["avg_entropy"]
        delta_tools = c["avg_tools_success"] - t["avg_tools_success"]
        delta_surgical = t["surgical_rate"] - c["surgical_rate"]
        print(f"Entropy: {c['label']}={c['avg_entropy']:.2f} -> {t['label']}={t['avg_entropy']:.2f} (delta={delta_entropy:+.2f})")
        print(f"Avg tool calls (success): {c['label']}={c['avg_tools_success']:.1f} -> {t['label']}={t['avg_tools_success']:.1f} (delta={delta_tools:+.1f})")
        print(f"Surgical rate: {c['label']}={c['surgical_rate']:.0%} -> {t['label']}={t['surgical_rate']:.0%} (delta={delta_surgical:+.0%})")
        print()
        if delta_entropy > 0.05 or delta_tools > 2:
            print(">> Evidence of entropy reduction: Crypt lowers search entropy and focuses execution.")
        elif delta_entropy < -0.05 or delta_tools < -2:
            print(">> No evidence — control condition was more focused.")
        else:
            print(">> Inconclusive — differences within noise range.")

    print()


if __name__ == "__main__":
    main()
