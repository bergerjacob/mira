#!/bin/bash
# sbatch training/sft/hpc/slurm_sft.sh training/sft/hpc/sft_qwen3_14b_lora.yaml [accelerate config]
# Adapt partition/account/modules to the cluster (unknown at writing time). One node, 8 GPUs.
#SBATCH --job-name=mira-sft
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --cpus-per-task=64
#SBATCH --time=08:00:00
#SBATCH --output=logs/%x-%j.out
set -euo pipefail
CFG=${1:?config yaml}
ACC=${2:-training/sft/hpc/accelerate_zero3.yaml}
cd "${SLURM_SUBMIT_DIR:-.}"
source .venv-train/bin/activate              # python 3.11+ env built from training/sft/hpc/requirements-hpc.txt (+ nucleation for eval)
export HF_HUB_ENABLE_HF_TRANSFER=1 TOKENIZERS_PARALLELISM=false
accelerate launch --config_file "$ACC" training/sft/hpc/sft_trl.py --config "$CFG"
