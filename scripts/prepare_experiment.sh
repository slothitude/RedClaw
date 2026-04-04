#!/usr/bin/env bash
# Prepare isolated crypt states for the self-learning ablation experiment.
#
# Creates 4 directories in the current working directory:
#   crypt_empty/          — bare directories, no wisdom
#   crypt_bloodline_only/ — bloodline markdown + dharma, no DNA or entombed
#   crypt_dna_only/       — DNA trait files only
#   crypt_full/           — full copy of everything
#
# Source: ~/.redclaw/crypt/

set -euo pipefail

SOURCE="${HOME}/.redclaw/crypt"

if [ ! -d "$SOURCE" ]; then
    echo "ERROR: Source crypt directory not found: $SOURCE"
    echo "Run RedClaw with --agi first to populate the crypt."
    exit 1
fi

echo "Preparing crypt snapshots from: $SOURCE"

# ── Condition A: Empty ─────────────────────────────────────
echo "  Creating crypt_empty/ ..."
mkdir -p crypt_empty/bloodlines crypt_empty/entombed crypt_empty/dna
# Leave directories empty — no bloodlines, no DNA, no dharma, no entombed

# ── Condition B: Bloodline only ────────────────────────────
echo "  Creating crypt_bloodline_only/ ..."
mkdir -p crypt_bloodline_only/bloodlines crypt_bloodline_only/entombed crypt_bloodline_only/dna

# Copy bloodline markdown files
if [ -d "$SOURCE/bloodlines" ]; then
    cp "$SOURCE/bloodlines/"*.md crypt_bloodline_only/bloodlines/ 2>/dev/null || true
fi

# Copy dharma
if [ -f "$SOURCE/dharma.md" ]; then
    cp "$SOURCE/dharma.md" crypt_bloodline_only/dharma.md
fi

# No DNA, no entombed — leave those dirs empty

# ── Condition C: DNA only ─────────────────────────────────
echo "  Creating crypt_dna_only/ ..."
mkdir -p crypt_dna_only/bloodlines crypt_dna_only/entombed crypt_dna_only/dna

# Copy DNA trait files
if [ -d "$SOURCE/dna" ]; then
    cp "$SOURCE/dna/"*.json crypt_dna_only/dna/ 2>/dev/null || true
fi

# No bloodlines, no dharma, no entombed

# ── Condition D: Full Crypt ───────────────────────────────
echo "  Creating crypt_full/ ..."
cp -r "$SOURCE" crypt_full

# ── Summary ───────────────────────────────────────────────
echo ""
echo "Crypt snapshots ready:"
for dir in crypt_empty crypt_bloodline_only crypt_dna_only crypt_full; do
    bloodlines=$(find "$dir/bloodlines" -name "*.md" 2>/dev/null | wc -l)
    dna=$(find "$dir/dna" -name "*.json" 2>/dev/null | wc -l)
    entombed=$(find "$dir/entombed" -name "*.json" 2>/dev/null | wc -l)
    dharma="no"
    [ -f "$dir/dharma.md" ] && dharma="yes"
    printf "  %-25s  bloodlines=%d  dna=%d  entombed=%d  dharma=%s\n" "$dir/" "$bloodlines" "$dna" "$entombed" "$dharma"
done
echo ""
echo "Usage:"
echo "  python scripts/run_swebench.py --dataset lite --sample 20 --provider zai --model glm-5.1 --agi --crypt-dir crypt_empty --output docs/experiments/results_a.json"
