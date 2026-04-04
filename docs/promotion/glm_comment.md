# GLM-5.1 Open Source Thread Comment

Copy-paste ready for HN, r/LocalLLaMA, r/MachineLearning threads when GLM-5.1 goes open source (est. April 6-7).

---

I ran RedClaw (my self-learning coding agent) against SWE-bench using GLM-5.1 the week it launched — 41% patch rate (7/17 resolvable instances) in 95 min at $0 cost. The agent accumulated 48 entombed records and reached DNA gen 49 during the run. Bloodline wisdom carries forward to the next session.

Key finding: successful patches used 3 tool calls (read→edit) vs 29 for failures (bash brute force). The agent's dream synthesis captured domain patterns but the efficiency meta-pattern had to be manually injected — the records summary wasn't feeding tool call counts to the synthesis prompt.

The Crypt system means model upgrades don't lose accumulated experience. Train on free GLM-5.1, switch to a larger model later, keep the wisdom.

github.com/slothitude/RedClaw
