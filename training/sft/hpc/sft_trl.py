#!/usr/bin/env python3
"""HPC SFT for 8B-32B models: TRL SFTTrainer + PEFT LoRA/QLoRA under accelerate (DeepSpeed ZeRO-2/3 or FSDP).

NOT RUNNABLE ON THE DEV BOX (no peft/trl/accelerate/deepspeed there, 12 GB GPU). Written against the documented APIs of
trl >= 0.19 (SFTConfig/SFTTrainer), peft >= 0.15, transformers >= 4.55, accelerate >= 1.6, deepspeed >= 0.16; pin the exact versions
of the cluster image in docs/TRAINING.md after the first run. The masking is NOT delegated to TRL: episodes are pre-tokenised by
training/sft/dataset.render_episode (the same function the local trainer uses, loss on assistant turns only, Qwen3 empty-think
prefix handled), and TRL is told to skip its own preparation. So local and HPC runs train on byte-identical token/label streams.

    accelerate launch --config_file training/sft/hpc/accelerate_zero3.yaml training/sft/hpc/sft_trl.py \
        --config training/sft/hpc/sft_qwen3_14b_lora.yaml
    # QLoRA (4-bit base, single node, ZeRO-2 or plain DDP; ZeRO-3 does not shard bnb 4-bit weights):
    accelerate launch --config_file training/sft/hpc/accelerate_zero2.yaml training/sft/hpc/sft_trl.py \
        --config training/sft/hpc/sft_qwen3_32b_qlora.yaml
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))


def load_config(path):
    import yaml
    return yaml.safe_load(Path(path).read_text())


def tokenised_dataset(cfg, tok):
    from datasets import Dataset
    from training.sft.dataset import build, split
    ex, rep = build(cfg["data"], tok, cfg["max_len"], enable_thinking=cfg.get("enable_thinking", False))
    train, evals = split(ex, cfg.get("eval_frac", 0.05), cfg.get("seed", 0))
    to = lambda xs: Dataset.from_list([{"input_ids": e.input_ids, "labels": e.labels,
                                        "attention_mask": [1] * len(e.input_ids)} for e in xs])
    print(json.dumps(rep))
    return to(train), (to(evals) if evals else None)


def collate(tok):
    import torch

    def f(batch):
        n = max(len(b["input_ids"]) for b in batch)
        pad = tok.pad_token_id
        ids = torch.full((len(batch), n), pad, dtype=torch.long)
        lab = torch.full((len(batch), n), -100, dtype=torch.long)
        att = torch.zeros((len(batch), n), dtype=torch.long)
        for i, b in enumerate(batch):
            k = len(b["input_ids"])
            ids[i, :k] = torch.tensor(b["input_ids"])
            lab[i, :k] = torch.tensor(b["labels"])
            att[i, :k] = 1
        return {"input_ids": ids, "labels": lab, "attention_mask": att}
    return f


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    import torch
    from peft import LoraConfig, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from trl import SFTConfig, SFTTrainer

    tok = AutoTokenizer.from_pretrained(cfg["model"])
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    train, evals = tokenised_dataset(cfg, tok)
    qlora = cfg.get("qlora", False)
    mkw = {"torch_dtype": torch.bfloat16, "attn_implementation": cfg.get("attn_implementation", "flash_attention_2")}
    if qlora:
        mkw["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
                                                        bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_quant_storage=torch.bfloat16)
    model = AutoModelForCausalLM.from_pretrained(cfg["model"], **mkw)
    if qlora:
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    peft_cfg = LoraConfig(r=cfg["lora"]["r"], lora_alpha=cfg["lora"]["alpha"], lora_dropout=cfg["lora"].get("dropout", 0.05),
                          target_modules=cfg["lora"].get("targets", ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj",
                                                                       "down_proj"]),
                          bias="none", task_type="CAUSAL_LM")
    t = cfg["train"]
    args = SFTConfig(
        output_dir=cfg["out"], num_train_epochs=t.get("epochs", 2), max_steps=t.get("max_steps", -1),
        per_device_train_batch_size=t.get("micro_batch", 1), gradient_accumulation_steps=t.get("grad_accum", 8),
        learning_rate=t.get("lr", 1e-4), lr_scheduler_type="cosine", warmup_ratio=t.get("warmup_ratio", 0.03),
        weight_decay=0.0, max_grad_norm=1.0, bf16=True, gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False}, logging_steps=5, save_steps=t.get("save_steps", 200),
        save_total_limit=3, eval_strategy="steps" if evals is not None else "no", eval_steps=t.get("eval_steps", 100),
        report_to=t.get("report_to", "none"), seed=cfg.get("seed", 0),
        max_length=cfg["max_len"], packing=False, dataset_kwargs={"skip_prepare_dataset": True}, remove_unused_columns=False)
    trainer = SFTTrainer(model=model, args=args, train_dataset=train, eval_dataset=evals, processing_class=tok,
                         peft_config=peft_cfg, data_collator=collate(tok))
    trainer.train(resume_from_checkpoint=t.get("resume", None) or None)
    trainer.save_model(str(Path(cfg["out"]) / "adapter"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
