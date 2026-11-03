#!/usr/bin/env python3
"""Minimal GRPO in pure torch (LoRA policy, frozen base as the KL reference) to prove the env-reward plumbing on one GPU.

    /usr/bin/python3 training/rl/grpo.py --model qwen3-1.7b --tasks t0 --group 4 --steps 6 \
        --out data/training_runs/grpo_smoke --time-budget-s 480          # rerun to resume

Per step: pick a task (round robin), sample G completions for the single-turn prompt (training/rl/reward.single_turn_messages)
with pure temperature sampling (top_p=1, no top-k, so the sampled distribution is the policy whose log-probs are trained), score
them in parallel through the harness path (training/rl/reward.RewardPool, hard timeout), compute group-normalised advantages
A_i = (r_i - mean) / (std + 1e-4), and minimise the token-mean over the group of
    -min(rho A, clip(rho, 1-eps, 1+eps) A) + beta * k3(ref || pi),   rho = exp(logp - logp_old),
    k3 = exp(ref - logp) - (ref - logp) - 1            (Schulman's unbiased low-variance KL estimator, as in GRPO/DeepSeekMath)
with one optimisation pass per batch (so rho == 1 at the update; the clip only matters with --inner-epochs > 1). A group whose
rewards are all equal carries no signal and is skipped (logged as `skipped`). `--mode multi` uses whole harness episodes
(training/rl/reward.rollout_episode): every turn's completion tokens get the trajectory advantage.
Logged per step (metrics.jsonl): rewards, statuses, pass count, loss, pg, kl, grad norm, finite checks, timings; samples.jsonl keeps
every completion with its reward.
"""
import argparse
import json
import math
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from training.local.paths import setup_paths  # noqa: E402

setup_paths()

import torch  # noqa: E402

from training.local import lora as L  # noqa: E402


# --------------------------------------------------------------------------- sampling
def batched_sample(model, prompt_ids, group: int, max_new: int, eos_ids, temperature: float = 1.0, pad_id: int = 0,
                   chunk: int = 512):
    """G samples of one prompt: prefill once, repeat the KV cache G times, decode the batch. Pure sampling at `temperature`.
    Returns a list of G completion id lists (each ends with an EOS id unless cut by max_new)."""
    from transformers import DynamicCache
    dev = next(model.parameters()).device
    ids = torch.tensor([list(prompt_ids)], device=dev)
    cache = DynamicCache(config=model.config)
    eos = torch.tensor(sorted(set(int(e) for e in eos_ids)), device=dev)
    with torch.inference_mode():
        for s in range(0, ids.shape[1], chunk):
            out = model(input_ids=ids[:, s:s + chunk], past_key_values=cache, use_cache=True, logits_to_keep=1)
        cache.batch_repeat_interleave(group)
        logits = out.logits[:, -1].float().repeat(group, 1)
        done = torch.zeros(group, dtype=torch.bool, device=dev)
        toks = []
        for _ in range(max_new):
            if temperature and temperature > 0:
                nxt = torch.multinomial(torch.softmax(logits / temperature, -1), 1).squeeze(1)
            else:
                nxt = logits.argmax(-1)
            nxt = torch.where(done, torch.full_like(nxt, pad_id), nxt)
            toks.append(nxt)
            done = done | torch.isin(nxt, eos)
            if bool(done.all()):
                break
            out = model(input_ids=nxt[:, None], past_key_values=cache, use_cache=True)
            logits = out.logits[:, -1].float()
    mat = torch.stack(toks, 1).tolist() if toks else [[] for _ in range(group)]
    res = []
    eos_set = set(eos.tolist())
    for row in mat:
        seq = []
        for t in row:
            seq.append(t)
            if t in eos_set:
                break
        res.append(seq)
    return res


# --------------------------------------------------------------------------- log-probs and loss
def token_logprobs(model, prompt_ids, completion_ids, chunk: int = 1024):
    """log pi(completion_t | prompt, completion_<t) for every completion token, [len(completion)] float32 tensor.
    Only the completion positions go through the LM head (chunked)."""
    from training.sft.train import decoder_of
    dev = next(model.parameters()).device
    ids = torch.tensor([list(prompt_ids) + list(completion_ids)], device=dev)
    h = decoder_of(model)(input_ids=ids, use_cache=False).last_hidden_state[0]
    p = len(prompt_ids)
    hs = h[p - 1:p - 1 + len(completion_ids)]
    tgt = ids[0, p:p + len(completion_ids)]
    outs = []
    for s in range(0, tgt.shape[0], chunk):
        lp = torch.log_softmax(model.lm_head(hs[s:s + chunk]).float(), -1)
        outs.append(lp.gather(-1, tgt[s:s + chunk, None]).squeeze(-1))
    return torch.cat(outs) if outs else h.new_zeros(0, dtype=torch.float32)


def advantages(rewards, eps: float = 1e-4):
    r = torch.tensor(rewards, dtype=torch.float32)
    std = r.std(unbiased=False) if len(rewards) > 1 else torch.tensor(0.0)
    return ((r - r.mean()) / (std + eps)).tolist(), float(std)


def grpo_token_loss(logp, old_logp, ref_logp, adv: float, beta: float = 0.04, clip_eps: float = 0.2):
    """Per-token GRPO objective terms -> (loss_tokens, pg_tokens, kl_tokens), all [T]."""
    ratio = torch.exp(logp - old_logp)
    a = torch.as_tensor(adv, dtype=logp.dtype, device=logp.device)
    pg = -torch.minimum(ratio * a, torch.clamp(ratio, 1 - clip_eps, 1 + clip_eps) * a)
    d = ref_logp - logp
    kl = torch.exp(d) - d - 1
    return pg + beta * kl, pg, kl


def policy_update(model, opt, params, segments, beta: float, clip_eps: float, max_grad: float = 1.0):
    """segments: [(prompt_ids, completion_ids, advantage)]. One pass, token-mean over all segments. Returns stats."""
    n_tok = sum(len(c) for _, c, _ in segments)
    if n_tok == 0:
        return {"skipped": "no tokens"}
    model.train()
    opt.zero_grad(set_to_none=True)
    tot_loss = tot_pg = tot_kl = 0.0
    for p, c, a in segments:
        if not c:
            continue
        with torch.no_grad(), L.lora_disabled(model):
            ref = token_logprobs(model, p, c)
        logp = token_logprobs(model, p, c)
        old = logp.detach()
        loss_t, pg_t, kl_t = grpo_token_loss(logp, old, ref, a, beta, clip_eps)
        (loss_t.sum() / n_tok).backward()
        tot_loss += float(loss_t.detach().sum())
        tot_pg += float(pg_t.detach().sum())
        tot_kl += float(kl_t.detach().sum())
    gn = float(torch.nn.utils.clip_grad_norm_(params, max_grad))
    finite = math.isfinite(gn) and math.isfinite(tot_loss)
    if finite:
        opt.step()
    opt.zero_grad(set_to_none=True)
    return {"loss": tot_loss / n_tok, "pg": tot_pg / n_tok, "kl": tot_kl / n_tok, "grad_norm": gn, "finite": finite,
            "tokens": n_tok}


# --------------------------------------------------------------------------- loop
def save_ckpt(out: Path, model, opt, state, base_id):
    tmp = out / "checkpoint.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    L.save_adapter(model, tmp, base_id, {"step": state["step"], "algo": "grpo"})
    torch.save({"opt": opt.state_dict(), "torch_rng": torch.get_rng_state(),
                "cuda_rng": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None}, tmp / "optim.pt")
    (tmp / "state.json").write_text(json.dumps(state))
    shutil.rmtree(out / "checkpoint", ignore_errors=True)
    tmp.rename(out / "checkpoint")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="qwen3-1.7b")
    ap.add_argument("--init-adapter", help="start the policy from an SFT adapter (its weights become the trainable LoRA)")
    ap.add_argument("--tasks", default="t0", help="env task spec (t0, t1, ids) for the round robin")
    ap.add_argument("--mode", choices=["single", "multi"], default="single")
    ap.add_argument("--group", type=int, default=4)
    ap.add_argument("--steps", type=int, default=6)
    ap.add_argument("--max-new", type=int, default=1024)
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--lr", type=float, default=1e-5)
    ap.add_argument("--beta", type=float, default=0.04)
    ap.add_argument("--clip-eps", type=float, default=0.2)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--alpha", type=float, default=32.0)
    ap.add_argument("--score-workers", type=int, default=4)
    ap.add_argument("--score-timeout", type=float, default=90.0)
    ap.add_argument("--multi-rounds", type=int, default=3)
    ap.add_argument("--multi-turns", type=int, default=6)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--time-budget-s", type=float)
    a = ap.parse_args(argv)
    t_start = time.time()
    deadline = t_start + a.time_budget_s if a.time_budget_s else None
    from env.task import load_task
    from harness.experiment import resolve_tasks
    from harness.llm_local import render_prompt
    from training.local.models import load_model
    from training.rl.reward import RewardPool, rollout_episode, score_completions, single_turn_messages
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tasks = resolve_tasks(a.tasks)
    model, tok, info = load_model(a.model)
    model.config.use_cache = True
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    if a.init_adapter:
        L.load_adapter(model, a.init_adapter)
    else:
        L.apply_lora(model, a.rank, a.alpha, 0.0)
    params = L.lora_parameters(model)
    for p in params:
        p.requires_grad_(True)
    opt = torch.optim.AdamW(params, lr=a.lr, betas=(0.9, 0.99), weight_decay=0.0)
    state = {"step": 0}
    ck = out / "checkpoint"
    if (ck / "state.json").exists():
        L.load_adapter(model, ck)
        o = torch.load(ck / "optim.pt", map_location="cpu", weights_only=False)
        opt.load_state_dict(o["opt"])
        torch.set_rng_state(o["torch_rng"])
        if o.get("cuda_rng") is not None and torch.cuda.is_available():
            torch.cuda.set_rng_state_all(o["cuda_rng"])
        state = json.loads((ck / "state.json").read_text())
        print(f"resumed at step {state['step']}")
    if not (out / "run.json").exists():
        (out / "run.json").write_text(json.dumps({**vars(a), "model_id": info["model_id"], "tasks_resolved": tasks}, indent=1))
    eos = sorted({tok.convert_tokens_to_ids("<|im_end|>"), tok.eos_token_id} - {None})
    step_times = []
    with RewardPool(a.score_workers, a.score_timeout) as pool:
        while state["step"] < a.steps:
            if deadline is not None and step_times and time.time() + 1.3 * max(step_times) + 20 > deadline:
                print(f"time budget reached before step {state['step'] + 1}")
                break
            t0 = time.time()
            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
            tid = tasks[state["step"] % len(tasks)]
            task = load_task(tid)
            torch.manual_seed(a.seed * 100003 + state["step"])
            model.eval()
            if a.mode == "single":
                msgs = single_turn_messages(task)
                prompt = tok(render_prompt(tok, msgs, None, enable_thinking=False), add_special_tokens=False)["input_ids"]
                comps = batched_sample(model, prompt, a.group, a.max_new, eos, a.temperature, pad_id=tok.pad_token_id)
                t_gen = time.time() - t0
                texts = [tok.decode(c, skip_special_tokens=True) for c in comps]
                t1 = time.time()
                res = score_completions(tid, texts, pool)
                t_score = time.time() - t1
                rewards = [r.reward for r in res]
                adv, std = advantages(rewards)
                segs = [(prompt, c, adv_i) for c, adv_i in zip(comps, adv)]
                samples = [{"step": state["step"] + 1, "task": tid, "i": i, "reward": r.reward, "status": r.status,
                            "detail": r.detail, "full_pass": r.full_pass, "tokens": len(c), "text": t}
                           for i, (c, r, t) in enumerate(zip(comps, res, texts))]
                statuses = [r.status for r in res]
                passes = sum(r.full_pass for r in res)
                n_prompt = len(prompt)
            else:
                from harness.agent import RunConfig
                from harness.llm_local import LocalLLM
                llm = LocalLLM(a.model, model_obj=model, tokenizer=tok, top_p=1.0, top_k=0, trace=True)
                trajs = []
                for g in range(a.group):
                    cfg = RunConfig(task=tid, model=a.model, arm="builder", rounds=a.multi_rounds, seed=state["step"] * 100 + g,
                                    max_turns=a.multi_turns, max_tokens=a.max_new, temperature=a.temperature)
                    trajs.append(rollout_episode(task, llm, cfg))
                t_gen = time.time() - t0
                t_score = 0.0
                rewards = [tr.reward for tr in trajs]
                adv, std = advantages(rewards)
                segs = [(t["prompt_ids"], t["completion_ids"], adv_i) for tr, adv_i in zip(trajs, adv) for t in tr.turns]
                samples = [{"step": state["step"] + 1, "task": tid, "i": i, "reward": tr.reward, "signals": tr.signals,
                            "tokens": tr.token_count()} for i, tr in enumerate(trajs)]
                statuses = [tr.final["ended_by"] for tr in trajs]
                passes = sum(bool(tr.final["full_pass"]) for tr in trajs)
                n_prompt = max(len(t["prompt_ids"]) for tr in trajs for t in tr.turns) if any(tr.turns for tr in trajs) else 0
            t2 = time.time()
            if std < 1e-6:
                upd = {"skipped": "zero reward variance"}
            else:
                upd = policy_update(model, opt, params, segs, a.beta, a.clip_eps)
            t_train = time.time() - t2
            state["step"] += 1
            dt = time.time() - t0
            step_times.append(dt)
            rec = {"step": state["step"], "task": tid, "rewards": rewards, "reward_mean": sum(rewards) / len(rewards),
                   "reward_std": std, "passes": passes, "statuses": statuses, "prompt_tokens": n_prompt,
                   "completion_tokens": [len(s[1]) for s in segs], **upd, "gen_s": round(t_gen, 1), "score_s": round(t_score, 1),
                   "train_s": round(t_train, 1), "step_s": round(dt, 1)}
            if torch.cuda.is_available():
                rec["peak_gb"] = round(torch.cuda.max_memory_allocated() / 1e9, 2)
            with open(out / "metrics.jsonl", "a") as fh:
                fh.write(json.dumps(rec) + "\n")
            with open(out / "samples.jsonl", "a") as fh:
                for s_ in samples:
                    fh.write(json.dumps(s_) + "\n")
            print(f"step {state['step']} {tid}: rewards {[round(r, 3) for r in rewards]} statuses {statuses} "
                  + (f"loss {upd['loss']:.4f} pg {upd['pg']:.4f} kl {upd['kl']:.5f} gnorm {upd['grad_norm']:.3f} finite {upd['finite']}"
                     if "loss" in upd else f"skipped ({upd['skipped']})")
                  + f" | gen {t_gen:.0f}s score {t_score:.1f}s train {t_train:.0f}s" + (f" peak {rec.get('peak_gb')} GB" if 'peak_gb' in rec else ""),
                  flush=True)
            save_ckpt(out, model, opt, state, info["model_id"])
    print(f"stopped at step {state['step']}/{a.steps} after {time.time() - t_start:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
