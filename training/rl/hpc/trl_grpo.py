#!/usr/bin/env python3
"""HPC single-turn GRPO with TRL's GRPOTrainer and the MIRA env reward (training/rl/reward.py). SKETCH: not runnable on the dev box
(no trl/peft/vllm there); written against trl >= 0.19 (GRPOConfig/GRPOTrainer, reward_funcs(prompts, completions, **columns)).

    accelerate launch --config_file training/sft/hpc/accelerate_zero3.yaml training/rl/hpc/trl_grpo.py \
        --model runs/sft_qwen3_14b_lora/merged --tasks data/rl/tasks_10_90.txt --out runs/grpo_qwen3_14b

The task list should hold only tasks whose current pass rate is 10-90% (measure with training/sft/evaluate.py harness, pass@8); a
task at 0% or 100% gives zero reward variance. Reward = training.rl.reward shaped reward (R2 weight 0, staged compile/rules credit).
Scoring is CPU work (sandboxed compile + Nucleation, ~0.1-1 s per sample): run it in the RewardPool (one per node, workers = CPUs/2).
Spot-check passes with the vanilla oracle (tools/oracle) before trusting reward curves (RL finds spec loopholes; round-2 audit).
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))


def build_dataset(task_ids, tokenizer=None):
    from datasets import Dataset
    from env.task import load_task
    from training.rl.reward import single_turn_messages
    rows = []
    for tid in task_ids:
        t = load_task(tid)
        rows.append({"prompt": single_turn_messages(t), "task_id": str(tid)})
    return Dataset.from_list(rows)


_POOL = None


def mira_reward(prompts, completions, task_id, **kw):
    """TRL reward function: conversational completions [[{"role": "assistant", "content": ...}], ...] -> shaped rewards."""
    global _POOL
    from training.rl.reward import RewardPool, extract_program
    if _POOL is None:
        _POOL = RewardPool(workers=int(os.environ.get("MIRA_REWARD_WORKERS", "16")), timeout=120)
    texts = [c[0]["content"] if isinstance(c, list) else c for c in completions]
    res = _POOL.score([(tid, extract_program(t)) for tid, t in zip(task_id, texts)])
    return [r.reward for r in res]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True)
    ap.add_argument("--tasks", required=True, help="file with one task id/path per line")
    ap.add_argument("--out", required=True)
    ap.add_argument("--group", type=int, default=8)
    ap.add_argument("--lr", type=float, default=1e-6)
    ap.add_argument("--beta", type=float, default=0.04)
    a = ap.parse_args(argv)
    from peft import LoraConfig
    from trl import GRPOConfig, GRPOTrainer
    tasks = [l.strip() for l in Path(a.tasks).read_text().splitlines() if l.strip()]
    ds = build_dataset(tasks)
    cfg = GRPOConfig(output_dir=a.out, num_generations=a.group, per_device_train_batch_size=a.group, gradient_accumulation_steps=4,
                     learning_rate=a.lr, beta=a.beta, max_prompt_length=8192, max_completion_length=2048, temperature=1.0,
                     bf16=True, gradient_checkpointing=True, logging_steps=1, save_steps=50, use_vllm=True,
                     vllm_mode="colocate", report_to="none")
    peft_cfg = LoraConfig(r=64, lora_alpha=128, target_modules="all-linear", task_type="CAUSAL_LM")
    trainer = GRPOTrainer(model=a.model, reward_funcs=[mira_reward], args=cfg, train_dataset=ds, peft_config=peft_cfg)
    trainer.train()
    trainer.save_model(str(Path(a.out) / "adapter"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
