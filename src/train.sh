#!/bin/bash
#SBATCH --time=19:55:00
#SBATCH --constraint=1
#SBATCH --mem-per-cpu=12G
#SBATCH --job-name=lcarnn
#SBATCH --mail-type=begin        # send email when job begins
#SBATCH --mail-type=end          # send email when job ends
#SBATCH --mail-type=FAIL
#SBATCH --mail-user=codydong@princeton.edu
#SBATCH --output=slurm_log/lcarnn-%j.log

LOGROOT=/scratch/gpfs/cd6060/logs/when-to-recall
DT=$(date +%Y-%m-%d)

echo $(date)

srun python -u run_exp2_CD.py \
    --subj_id ${1} \
    --B ${2} \
    --penalty ${3} \
    --add_query_indicator ${4} \
    --add_condition_label ${5} \
    --gating_type ${6} \
    --n_hidden ${7} \
    --lr ${8} \
    --cmpt ${9} \
    --eta ${10} \
    --n_epochs ${11} \
    --sup_epoch ${12} \
    --test_mode ${13} \
    --dk_train_epoch ${14} \
    --p_lowD ${15} \
    --exp_name $DT \
    --log_root $LOGROOT

echo $(date)
