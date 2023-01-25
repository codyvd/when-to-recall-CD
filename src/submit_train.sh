#!/bin/bash

add_query_indicator=1
add_condition_label=0
gating_type=post
n_epochs=15000
sup_epoch=0
test_mode=1
eta=0.1
dk_train_epoch=0

for subj_id in {0..4}
do
  for lr in 1e-3
  do
    for B in 10
    do
      for penalty in 2 4 6 8 10
      do
        for n_hidden in 128
        do
          for cmpt in .8
          do
            for p_lowD in .2 .4 .5 .6 .8
            do
            sbatch train.sh \
                 ${subj_id} \
                 ${B} \
                 ${penalty} \
                 ${add_query_indicator} \
                 ${add_condition_label} \
                 ${gating_type} \
                 ${n_hidden} \
                 ${lr} \
                 ${cmpt} \
                 ${eta} \
                 ${n_epochs} \
                 ${sup_epoch} \
                 ${test_mode} \
                 ${dk_train_epoch} \
                 ${p_lowD}
            done
          done
        done
      done
    done
  done
done
