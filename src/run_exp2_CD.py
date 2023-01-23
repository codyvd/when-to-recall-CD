import os
import torch
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
import torch.nn.functional as F
import argparse
# from torch.optim.lr_scheduler import ConstantLR
from task import SimpleExp2
from task import get_reward, ground_truth_mem_sig
# from models import LCALSTM as Agent
from models import LCALSTM_after as Agent
from utils import Parameters as P
from utils.stats import compute_stats, entropy
from utils.utils import to_sqnp, to_np, init_lll, save_ckpt, load_ckpt, \
    get_recall_info, get_max_epoch_saved, ckpt_exists

sns.set(style='white', palette='colorblind', context='poster')

# matplotlib.use('Agg')
parser = argparse.ArgumentParser()
parser.add_argument('--subj_id', default=0, type=int)
parser.add_argument('--B', default=10, type=int)
parser.add_argument('--penalty', default=3, type=int)
parser.add_argument('--add_query_indicator', default=1, type=int)
parser.add_argument('--add_condition_label', default=0, type=int)
parser.add_argument('--p_lowD', default=0.5, type=float)
parser.add_argument('--gating_type', default='post', type=str)
parser.add_argument('--n_hidden', default=128, type=int)
parser.add_argument('--lr', default=1e-3, type=float)
parser.add_argument('--cmpt', default=.8, type=float)
parser.add_argument('--eta', default=.1, type=float)
parser.add_argument('--n_epochs', default=100, type=int)
parser.add_argument('--sup_epoch', default=0, type=int)
parser.add_argument('--dk_train_epoch',default=0, type=int)
parser.add_argument('--test_mode', default=1, type=int)
parser.add_argument('--exp_name', default='testing', type=str)
parser.add_argument('--log_root', default='../log', type=str)
args = parser.parse_args()
print(args)

'''params for the model'''

# training param
subj_id = args.subj_id
B = args.B
penalty = args.penalty
add_query_indicator = bool(args.add_query_indicator)
add_condition_label = bool(args.add_condition_label)
p_lowD = args.p_lowD
gating_type = args.gating_type
n_hidden = args.n_hidden
lr = args.lr
cmpt = args.cmpt
eta = args.eta
n_epochs = args.n_epochs
dk_train_epoch = args.dk_train_epoch
sup_epoch = args.sup_epoch
test_mode = bool(args.test_mode)
exp_name = args.exp_name
log_root = args.log_root


final_epoch = n_epochs

# save all params
p = P(
    subj_id=subj_id, B = B, penalty = penalty, n_hidden = n_hidden, lr = lr, cmpt = cmpt,
    eta = eta, test_mode = test_mode, add_query_indicator = add_query_indicator,
    gating_type = gating_type, n_epochs = n_epochs, sup_epoch = sup_epoch, exp_name=exp_name, log_root=log_root
)
p.gen_log_dirs()
'''init model and task'''
np.random.seed(subj_id)
torch.manual_seed(subj_id)
exp = SimpleExp2(B)
x_dim = exp.x_dim
y_dim = exp.y_dim+1 # add 1 for the don't know unit
agent = Agent(
    input_dim=x_dim, output_dim=y_dim, rnn_hidden_dim=n_hidden,
    dec_hidden_dim=n_hidden//2, dict_len=2, cmpt=cmpt,
    add_query_indicator=add_query_indicator, add_condition_indicator=add_condition_label
)
# optimizer_sup = torch.optim.Adam(agent.parameters(), lr=lr)
optimizer = torch.optim.Adam(agent.parameters(), lr=lr)
# scheduler = ConstantLR(optimizer, factor=1/3, total_iters=3000, verbose=True)

'''parameters for keep training, will be skipped if the sim is new'''
if p.log_dir_exists() and ckpt_exists(p.log_dir):
    # keep training
    n_epochs_kt = 5000
    lr_kt = 1e-4
    learning = True
    # # just testing
    # n_epochs_kt = 300
    # lr_kt = 0
    # learning = False
    epoch_loaded = get_max_epoch_saved(p.log_dir)
    # epoch_loaded = 13999
    agent, optimizer = load_ckpt(epoch_loaded, p.log_dir, agent, optimizer)
    #optimizer = torch.optim.Adam(agent.parameters(), lr=lr_kt)
    epoch_loaded += 1
    n_epochs = n_epochs = n_epochs_kt - (epoch_loaded)
else:
    n_epochs_kt, lr_kt = 0, None
    epoch_loaded = 0
    learning = True

scheduler_RL = torch.optim.lr_scheduler.StepLR(optimizer, 3000, gamma=0.333)

'''train the model'''
def run_exp2(n_epochs, epoch_loaded=0,dk_train_epoch = 0, learning=True):

    log_sf_ids = np.zeros((n_epochs, exp.n_trios))
    log_trial_types = [None] * n_epochs
    log_loss_s = torch.zeros((n_epochs, exp.n_trios, 3))
    log_loss_a = torch.zeros((n_epochs, exp.n_trios, 3))
    log_loss_c = torch.zeros((n_epochs, exp.n_trios, 3))
    log_return = torch.zeros((n_epochs, exp.n_trios, 3))
    log_acc = init_lll(n_epochs, exp.n_trios, 3)
    log_a = init_lll(n_epochs, exp.n_trios, 3)
    log_dk = init_lll(n_epochs, exp.n_trios, 3)
    log_tq_emg = torch.zeros((n_epochs, exp.n_trios, exp.T_test))
    log_tq_ma = torch.zeros((n_epochs, exp.n_trios, exp.T_test, 2))
    # i,j,k,t=0,0,0,0
    for i in range(n_epochs):
        # make data
        if i + epoch_loaded < dk_train_epoch:
            print("DK Training Epochs")
            X, Y, log_sf_ids[i], log_trial_types[i] = exp.make_data(to_torch=True, test_mode=False)
        else:
            print("Regular Training Epochs")
            X, Y, log_sf_ids[i], log_trial_types[i] = exp.make_data(to_torch=True, test_mode=True)
        permuted_tiro_ids = np.random.permutation(exp.n_trios)
        for j in permuted_tiro_ids:
            # go through the trio: event 1 (study) -> event 1' (study) -> event 1 (test)
            n_events = len(exp.stimuli_order)
            for k in range(n_events):
                # at the beginning of each event, flush WM
                hc_t = agent.get_init_states()
                # turn off recall during the study phase, turn it on during test
                if k in [0, 1]:
                    agent.retrieval_off()
                else:
                    agent.retrieval_on()
                # init loss
                loss_sup = 0
                agent.init_rvpe()
                for t in range(len(X[j][k])):
                    # print(i,j,k,t, len(agent.em.vals))
                    # encode if and only if at event end
                    if t == exp.T-1 and k in [0, 1]:
                        agent.encoding_on()
                    else:
                        agent.encoding_off()
                    # forward
                    pi_a_t, v_t, hc_t, cache_t = agent(
                        X[j][k][t], hc_t, mem_sig = ground_truth_mem_sig(log_trial_types[i][j], t, k)
                    )
                    a_t, p_a_t = agent.pick_action(pi_a_t)
                    r_t = get_reward(a_t, X[j][k][t], Y[j][k][t], penalty)
                    agent.append_rvpe(r_t, v_t, p_a_t, entropy(pi_a_t))
                    # sup loss
                    yhat_t = pi_a_t[:-1]
                    loss_sup += F.mse_loss(yhat_t, Y[j][k][t])
                    # loss_sup += criterion(yhat_t.view(1, -1), torch.argmax(Y[j][k][t]).view(-1,))
                    # log info
                    if not learning:
                        log_acc[i][j][k].append(int(torch.argmax(yhat_t) == torch.argmax(Y[j][k][t])))
                        log_a[i][j][k].append(int(a_t))
                        log_dk[i][j][k].append(int(a_t) == exp.B)
                        if k == 2:
                            log_tq_emg[i,j,t], log_tq_ma[i,j,t] = get_recall_info(cache_t)

                # at the end of one event
                loss_actor, loss_critic, pi_ent = agent.compute_a2c_loss(use_V=False)

                if learning:
                    # update weights
                    if torch.any(torch.isnan(pi_ent)):
                        print('WARNING ENTROPY!')
                        loss_rl = loss_actor + loss_critic
                        optimizer.zero_grad()
                        loss_rl.backward()
                        optimizer.step()
                    else:
                        loss_rl = loss_actor + loss_critic - pi_ent * eta
                        optimizer.zero_grad()
                        loss_rl.backward()
                        optimizer.step()

                # log info
                log_loss_s[i,j,k] = loss_sup.clone().detach()
                log_loss_a[i,j,k], log_loss_c[i,j,k] = loss_actor.clone().detach(), loss_critic.clone().detach()
                log_return[i,j,k] = torch.stack(agent.rewards).sum()

            # at the end of 3 trio
            agent.flush_episodic_memory()

        # at the end of an epoch
        # scheduler.step()
        info_i = (i + epoch_loaded, log_loss_a[i].mean(), log_loss_c[i].mean(), log_return[i].mean())
        if learning:
            print(f'%3d | L: a: %.2f, c: %.2f | R : %.2f' % info_i)
        else:
            print(f'Test %3d | L: a: %.2f, c: %.2f | R : %.2f' % (i, log_loss_a[i].mean(), log_loss_c[i].mean(), log_return[i].mean()))

        # save weights for every other 1000 epochs
        if (i+1) % 1000 == 0 and learning:
            save_ckpt(i + epoch_loaded, p.log_dir, agent, optimizer, verbose=True)

        if learning:
            scheduler_RL.step()

    # done training, pack results
    log_info = [
        log_sf_ids,
        log_trial_types,
        log_loss_s,
        log_loss_a,
        log_loss_c,
        log_return,
        log_acc,
        log_a,
        log_dk,
        log_tq_emg,
        log_tq_ma,
    ]
    return log_info

# run the training scirpts
# run the training scirpts
log_info_train = run_exp2(n_epochs=n_epochs, dk_train_epoch = dk_train_epoch, epoch_loaded=epoch_loaded, learning=learning)

epoch_fin = final_epoch
num_test_epochs = 200
log_info = run_exp2(n_epochs=num_test_epochs, dk_train_epoch=0, epoch_loaded=epoch_fin, learning=False)

[
    log_sf_ids_train,
    log_trial_types_train,
    log_loss_s,
    log_loss_a,
    log_loss_c,
    log_return,
    log_acc_train,
    log_a_train,
    log_dk_train,
    log_tq_emg_train,
    log_tq_ma_train,
] = log_info_train #Assigning training logged variables

[
    log_sf_ids,
    log_trial_types,
    log_loss_s_test,
    log_loss_a_test,
    log_loss_c_test,
    log_return_test,
    log_acc,
    log_a,
    log_dk,
    log_tq_emg,
    log_tq_ma,
] = log_info #Assigning test logged variables

'''preproc the results'''

def get_within_trial_query_mean(log_info, n_epochs):
    wtq_acc = np.zeros((n_epochs, exp.n_trios, 2))
    for i in range(n_epochs):
        for j in range(exp.n_trios):
            # get the last time point for the 1st two trios
            wtq_acc[i,j] = [log_info[i][j][k][-1] for k in range(2)]
    return np.mean(wtq_acc,axis=-1)

def get_test_query_mean(log_info, n_epochs):
    n_queries = 3
    tq_acc = np.zeros((n_epochs, exp.n_trios, n_queries))
    for i in range(n_epochs):
        for j in range(exp.n_trios):
            tq_acc[i,j] = [log_info[i][j][-1][ii] for ii in [2,4,6]]
    return tq_acc

def get_copy_mean(log_info, n_epochs):
    cp_acc = np.zeros((n_epochs, exp.n_trios, 2, exp.T))
    for i in range(n_epochs):
        for j in range(exp.n_trios):
            for k in range(2):
                cp_acc[i,j] = log_info[i][j][k][:exp.T]
    return np.mean(np.mean(cp_acc,axis=-1),axis=-1)


'''plot the results'''
def plot_learning_curve(title, data, n_epochs = n_epochs):
    f, ax = plt.subplots(1,1, figsize=(8,5))
    lc_data = [data[i].mean() for i in range(n_epochs)]
    ax.plot(lc_data)
    ax.set_xlabel('Epochs')
    ax.set_ylabel(title)
    f.tight_layout()
    sns.despine()
    if sup_epoch < n_epochs:
        ax.axvline(sup_epoch, linestyle='--', color='red', label='start RL training')
    return f, ax

epoch_save = n_epochs + epoch_loaded

f, ax = plot_learning_curve('Cumulative R', log_return)
fig_path = os.path.join(p.log_dir, f'lr-r-ep-{epoch_save}.png')
f.savefig(fig_path, dpi=100)
f, ax = plot_learning_curve('Loss - actor', log_loss_a)
fig_path = os.path.join(p.log_dir, f'lr-a-ep-{epoch_save}.png')
f.savefig(fig_path, dpi=100)
f, ax = plot_learning_curve('Loss - critic', log_loss_c)
fig_path = os.path.join(p.log_dir, f'lr-c-ep-{epoch_save}.png')
f.savefig(fig_path, dpi=100)


chance = 1 / exp.B
wtq_acc = get_within_trial_query_mean(log_acc, num_test_epochs)
f, ax = plot_learning_curve('Within trial query acc', wtq_acc, num_test_epochs)
ax.axhline(chance, linestyle='--', color='grey', label='chance')
ax.legend()
fig_path = os.path.join(p.log_dir, f'lr-wtq-acc-ep-{epoch_save}.png')
f.savefig(fig_path, dpi=100)


tq_acc = get_test_query_mean(log_acc, num_test_epochs)
f, ax = plot_learning_curve('Test query acc', np.mean(tq_acc,axis=-1), num_test_epochs)
ax.axhline(chance, linestyle='--', color='grey', label='chance')
ax.legend()
fig_path = os.path.join(p.log_dir, f'lr-tq-acc-ep-{epoch_save}.png')
f.savefig(fig_path, dpi=100)

cp_acc = get_copy_mean(log_acc, num_test_epochs)
f, ax = plot_learning_curve('Copy acc', np.mean(cp_acc,axis=-1), num_test_epochs)
ax.axhline(chance, linestyle='--', color='grey', label='chance')
ax.legend()
fig_path = os.path.join(p.log_dir, f'lr-cp-acc-ep-{epoch_save}.png')
f.savefig(fig_path, dpi=100)



wtq_dk = get_within_trial_query_mean(log_dk, num_test_epochs)
f, ax = plot_learning_curve('Within trial query, p(dk)', wtq_dk, num_test_epochs)
ax.set_ylim([-.05,1.05])
ax.legend()
fig_path = os.path.join(p.log_dir, f'pdk-wtq-ep-{epoch_save}.png')
f.savefig(fig_path, dpi=100)


tq_dk = get_test_query_mean(log_dk, num_test_epochs)
f, ax = plot_learning_curve('Test query, p(dk)', np.mean(tq_dk,axis=-1), num_test_epochs)
ax.set_ylim([-.05,1.05])
ax.legend()
fig_path = os.path.join(p.log_dir, f'pdk-tq-ep-{epoch_save}.png')
f.savefig(fig_path, dpi=100)


cp_dk = get_copy_mean(log_dk, num_test_epochs)
f, ax = plot_learning_curve('Copy, p(dk)', np.mean(cp_dk,axis=-1), num_test_epochs)
ax.set_ylim([-.05,1.05])
ax.legend()
fig_path = os.path.join(p.log_dir, f'pdk-cp-ep-{epoch_save}.png')
f.savefig(fig_path, dpi=100)

'''analyze the results at the end of training '''
# num of epochs to analyze
npa = 200
name_str = f'penalty_{penalty}_plowd_{round(p_lowD*100)}_dktrain_{dk_train_epoch}_subj_{subj_id}_'

# reformat data
tq_dk_rs = np.reshape(tq_dk[-npa:], (-1, 3))
tq_acc_rs = np.reshape(tq_acc[-npa:], (-1, 3))
log_trial_types_rs = np.reshape(log_trial_types[-npa:], (-1))
# split data according to condition
hd = log_trial_types_rs == 'high d'
ld = log_trial_types_rs == 'low d'
tq_acc_rs_hd_mu, tq_acc_rs_hd_se = compute_stats(tq_acc_rs[hd],axis=0)
tq_acc_rs_ld_mu, tq_acc_rs_ld_se = compute_stats(tq_acc_rs[ld],axis=0)
tq_dk_rs_hd_mu, tq_dk_rs_hd_se = compute_stats(tq_dk_rs[hd],axis=0)
tq_dk_rs_ld_mu, tq_dk_rs_ld_se = compute_stats(tq_dk_rs[ld],axis=0)
tq_er_rs = np.logical_and(np.logical_not(tq_acc_rs), np.logical_not(tq_dk_rs))
tq_er_rs_hd_mu, tq_er_rs_hd_se = compute_stats(tq_er_rs[hd],axis=0)
tq_er_rs_ld_mu, tq_er_rs_ld_se = compute_stats(tq_er_rs[ld],axis=0)

# plot behavioral performance
c_pal = sns.color_palette('colorblind', n_colors=4)
alpha = .3
x_ = np.arange(3) # there are 3 query time points
ones = np.ones_like(x_)
f, axes = plt.subplots(1, 3, figsize=(14, 5), sharey=True)

data1 = {'query_pos': x_, 'prop': tq_acc_rs_hd_mu, 'se': tq_acc_rs_hd_se,'answer': 'correct', 'diag': 'high'}
pd_1 = pd.DataFrame(data1)
data2 = {'query_pos': x_, 'prop': tq_acc_rs_ld_mu, 'se': tq_acc_rs_ld_se,'answer': 'correct', 'diag': 'low'}
pd_2 = pd.DataFrame(data2)
data3 = {'query_pos': x_, 'prop': tq_dk_rs_hd_mu, 'se': tq_dk_rs_hd_se,'answer': 'dont_know', 'diag': 'high'}
pd_3 = pd.DataFrame(data3)
data4 = {'query_pos': x_, 'prop': tq_dk_rs_ld_mu, 'se': tq_dk_rs_ld_se,'answer': 'dont_know', 'diag': 'low'}
pd_4 = pd.DataFrame(data4)
data5 = {'query_pos': x_, 'prop': tq_er_rs_hd_mu, 'se': tq_er_rs_hd_se,'answer': 'incorrect', 'diag': 'high'}
pd_5 = pd.DataFrame(data5)
data6 = {'query_pos': x_, 'prop': tq_er_rs_ld_mu, 'se': tq_er_rs_ld_se,'answer': 'incorrect', 'diag': 'low'}
pd_6 = pd.DataFrame(data6)

pd_perf = pd.concat([pd_1,pd_2,pd_3,pd_4,pd_5,pd_6])
csv_perf_path = os.path.join(p.log_dir, name_str + 'perf.csv')
pd_perf.to_csv(path_or_buf=csv_perf_path, index=False)


axes[0].errorbar(x=x_, y=tq_acc_rs_hd_mu, yerr=tq_acc_rs_hd_se, color=c_pal[3])
axes[0].errorbar(x=x_, y=tq_acc_rs_ld_mu, yerr=tq_acc_rs_ld_se, color=c_pal[0])
axes[0].set_title('correct')
axes[1].errorbar(x=x_, y=tq_dk_rs_hd_mu, yerr=tq_dk_rs_hd_se, color=c_pal[3])
axes[1].errorbar(x=x_, y=tq_dk_rs_ld_mu, yerr=tq_dk_rs_ld_se, color=c_pal[0])
axes[1].set_title('dont know')
axes[2].errorbar(x=x_, y=tq_er_rs_hd_mu, yerr=tq_er_rs_hd_se, color=c_pal[3])
axes[2].errorbar(x=x_, y=tq_er_rs_ld_mu, yerr=tq_er_rs_ld_se, color=c_pal[0])
axes[2].set_title('incorrect')

for ax in axes:
    ax.set_ylabel('Frequency')
    ax.set_xlabel('Test query position')
    ax.set_xticks(x_)
    ax.set_xticklabels(x_+2)
    ax.set_ylim([-.05, 1.05])
axes[0].axhline(chance, ls='--', color='grey')
f.legend(['chance', 'high d', 'low d'], loc=(.51,.5))
sns.despine()
f.tight_layout()
fig_path = os.path.join(p.log_dir, f'performance-ep-{epoch_save}.png')
f.savefig(fig_path, dpi=100)


'''analyze recall'''
# re-format
log_tq_emg = to_sqnp(log_tq_emg)
log_tq_ma = to_sqnp(log_tq_ma)
# split data according to condition
log_tq_emg_ld = log_tq_emg[np.array(log_trial_types)=='low d']
log_tq_emg_hd = log_tq_emg[np.array(log_trial_types)=='high d']
log_tq_ma_ld = log_tq_ma[np.array(log_trial_types)=='low d']
log_tq_ma_hd = log_tq_ma[np.array(log_trial_types)=='high d']

# plot em gate
log_tq_emg_ld_mu, log_tq_emg_ld_se = compute_stats(log_tq_emg_ld,axis=0)
log_tq_emg_hd_mu, log_tq_emg_hd_se = compute_stats(log_tq_emg_hd,axis=0)

f, ax = plt.subplots(1, 1, figsize=(8, 5), sharey=True)
x_ = np.arange(exp.T_test)
x_ticklabels = ['0', '1', '2-q', '2-o', '3-q', '3-o', '4-q', '4-o']

data1_em = pd.DataFrame({'pos': x_, 'prop': log_tq_emg_ld_mu, 'se': log_tq_emg_ld_se,'diag': 'low'})
data2_em = pd.DataFrame({'pos': x_, 'prop': log_tq_emg_hd_mu, 'se': log_tq_emg_hd_se,'diag': 'high'})
pd_em = pd.concat([data1_em, data2_em])
csv_em_path = os.path.join(p.log_dir, name_str + 'em.csv')
pd_em.to_csv(path_or_buf=csv_em_path, index=False)

ax.errorbar(x=x_, y=log_tq_emg_ld_mu, yerr=log_tq_emg_ld_se, color=c_pal[3])
ax.errorbar(x=x_, y=log_tq_emg_hd_mu, yerr=log_tq_emg_hd_se, color=c_pal[0])
ax.set_ylabel('EM gate')
ax.set_xticks(x_)
ax.set_xticklabels(x_ticklabels)
f.legend(['low d', 'high d'])
sns.despine()
f.tight_layout()
fig_path = os.path.join(p.log_dir, f'emgate-ep-{epoch_save}.png')
f.savefig(fig_path, dpi=100)

# plot memory activation
log_tq_ma_ld_mu, log_tq_ma_ld_se = compute_stats(log_tq_ma_ld, axis=0)
log_tq_ma_hd_mu, log_tq_ma_hd_se = compute_stats(log_tq_ma_hd, axis=0)
data1_mem = pd.DataFrame({'pos': x_, 'mem_act': log_tq_ma_ld_mu[:,0], 'se': log_tq_ma_ld_se[:,0],'diag': 'low', 'type': 'target'})
data2_mem = pd.DataFrame({'pos': x_, 'mem_act': log_tq_ma_ld_mu[:,1], 'se': log_tq_ma_ld_se[:,1],'diag': 'low', 'type': 'lure'})
data3_mem = pd.DataFrame({'pos': x_, 'mem_act': log_tq_ma_hd_mu[:,0], 'se': log_tq_ma_hd_se[:,0],'diag': 'high', 'type': 'target'})
data4_mem = pd.DataFrame({'pos': x_, 'mem_act': log_tq_ma_hd_mu[:,1], 'se': log_tq_ma_hd_se[:,1],'diag': 'high', 'type': 'lure'})
pd_mem = pd.concat([data1_mem, data2_mem, data3_mem, data4_mem])
csv_mem_path = os.path.join(p.log_dir, name_str + 'mem.csv')
pd_mem.to_csv(path_or_buf=csv_mem_path, index=False)
f, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=True)
axes[0].errorbar(x=x_, y=log_tq_ma_ld_mu[:,0], yerr=log_tq_ma_ld_se[:,0], color=c_pal[3], label = 'targ')
axes[0].errorbar(x=x_, y=log_tq_ma_ld_mu[:,1], yerr=log_tq_ma_ld_se[:,1], color=c_pal[2], label = 'lure')
axes[0].set_title('low d')
f.legend()
axes[1].errorbar(x=x_, y=log_tq_ma_hd_mu[:,0], yerr=log_tq_ma_hd_se[:,0], color=c_pal[3], label = 'targ')
axes[1].errorbar(x=x_, y=log_tq_ma_hd_mu[:,1], yerr=log_tq_ma_hd_se[:,1], color=c_pal[2], label = 'lure')
axes[1].set_title('high d')
axes[0].set_ylabel('Memory activation')
for ax in axes:
    ax.set_xticks(x_)
    ax.set_xticklabels(x_ticklabels)
sns.despine()
f.tight_layout()
fig_path = os.path.join(p.log_dir, f'mem-act-ep-{epoch_save}.png')
f.savefig(fig_path, dpi=100)



