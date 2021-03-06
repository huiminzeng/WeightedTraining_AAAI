from __future__ import print_function
import os
import argparse
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torchvision import datasets, transforms

from models import *
from trades import trades_loss

# perform attack during training:
from PGD_on_the_fly import *
from utils import *

parser = argparse.ArgumentParser(description='PyTorch MNIST TRADES Adversarial Training')
parser.add_argument('--batch-size', type=int, default=128, metavar='N',
                    help='input batch size for training (default: 128)')
parser.add_argument('--test-batch-size', type=int, default=128, metavar='N',
                    help='input batch size for testing (default: 128)')
parser.add_argument('--epochs', type=int, default=100, metavar='N',
                    help='number of epochs to train')

parser.add_argument('--momentum', type=float, default=0.9, metavar='M',
                    help='SGD momentum')
parser.add_argument('--no-cuda', action='store_true', default=False,
                    help='disables CUDA training')

parser.add_argument('--log-interval', type=int, default=100, metavar='N',
                    help='how many batches to wait before logging training status')

parser.add_argument('--save-freq', '-s', default=5, type=int, metavar='N',
                    help='save frequency')

# save path
parser.add_argument('--model-dir', default='trained_models/mnist/',
                    help='directory of model for saving checkpoint')

# model size
parser.add_argument('--size', default='SmallCNN', type=str, help='MiniCNN or SmallCNN')

# adversarial parameters
parser.add_argument('--epsilon', default=0.3,
                    help='perturbation')
parser.add_argument('--num-steps', default=40,
                    help='perturb number of steps')
parser.add_argument('--step-size', default=0.01,
                    help='perturb step size')

# specify training
parser.add_argument('--mode', default='baseline', type=str, help='baseling/margin')
parser.add_argument('--lr', type=float, default=0.01, metavar='LR', help='learning rate')
parser.add_argument('--alpha', default=1, type=float, help='margin/margin_decor')
parser.add_argument('--seed', type=int, default=1, metavar='S', help='random seed (default: 1)')

## TRADES loss penalty term
parser.add_argument('--beta', default=6.0, type=float,
                    help='regularization, i.e., 1/lambda in TRADES')

# specify GPU id
parser.add_argument('--cuda', default=0, type=int)

# specify attack parameters
parser.add_argument('--epsilon_eval', default=0.3, type=float,
                    help='perturbation')
parser.add_argument('--num_steps_eval', default=40, type=int,
                    help='perturb number of steps')
parser.add_argument('--step_size_eval', default=0.01, type=float,
                    help='perturb step size')

# specify random data distribution for evaluation
parser.add_argument('--random_init',default=True, help='initialize perturbations with random numbers')

args = parser.parse_args()
####################################################################
############################ TRAINING ##############################
####################################################################

experiment_name = exp_name(args)
# settings
model_dir = args.model_dir
save_dir_root = os.path.join(model_dir, args.mode)
save_dir = os.path.join(save_dir_root, experiment_name)
if not os.path.exists(save_dir):
    os.makedirs(save_dir)
    
use_cuda = not args.no_cuda and torch.cuda.is_available()
seed = args.seed
device = torch.device('cuda:'+str(args.cuda))
kwargs = {'num_workers': 1, 'pin_memory': True} if use_cuda else {}

# setup data loader
train_loader = torch.utils.data.DataLoader(
    datasets.MNIST('../data', train=True, download=True,
                   transform=transforms.ToTensor()),
                    batch_size=args.batch_size, shuffle=True, **kwargs)

test_loader = torch.utils.data.DataLoader(
    datasets.MNIST('../data', train=False, download=True,
                   transform=transforms.ToTensor()),
                   batch_size=args.test_batch_size, shuffle=False, **kwargs)


def train(args, model, device, train_loader, optimizer, epoch):
    model.train()
    loss_his = []
    loss_robust_his = []
    for batch_idx, (data, target) in enumerate(train_loader):
        data, target = data.to(device), target.to(device)

        optimizer.zero_grad()

        # calculate robust loss
        loss, loss_robust = trades_loss(model=model,
                           x_natural=data,
                           y=target,
                           optimizer=optimizer,
                           args=args)
        loss.backward()
        optimizer.step()
        loss_his.append(loss.item())
        loss_robust_his.append(loss_robust.item())
        # print progress
        if batch_idx % args.log_interval == 0:
            print('Train Epoch: {} [{}/{} ({:.0f}%)]\tLoss: {:.6f}'.format(
                epoch, batch_idx * len(data), len(train_loader.dataset),
                100. * batch_idx / len(train_loader), loss.item()))

        break

    return np.mean(loss_his), np.mean(loss_robust_his)

def adjust_learning_rate(optimizer, epoch):
    """decrease the learning rate"""
    lr = args.lr
    if epoch >= 55:
        lr = args.lr * 0.1
    if epoch >= 75:
        lr = args.lr * 0.01
    if epoch >= 90:
        lr = args.lr * 0.001
    for param_group in optimizer.param_groups:
        param_group['lr'] = lr

def main():
    # init model, Net() can be also used here for training
    if args.size == 'SmallCNN':
        torch.manual_seed(seed)
        model = SmallCNN().to(device)

    optimizer = optim.SGD(model.parameters(), lr=args.lr, momentum=args.momentum)
    
    best_acc_adv = 0
    best_acc_clean = 0
    best_model = None
    best_epoch = 0
    best_optimizer = None
    
    for epoch in range(1, args.epochs + 1):
        # adjust learning rate for SGD
        adjust_learning_rate(optimizer, epoch)

        # adversarial training
        loss, loss_robust = train(args, model, device, train_loader, optimizer, epoch)

        # white-box attack
        print('================================================================')
        print('pgd white-box attack')
        acc_clean, acc_adv = eval_adv_test_whitebox(model, device, test_loader, args)
        print("** EPOCH[{}|{}], ACC_CLEAN:  {:.2f}%, ACC_ADV: {:.2f}%".format(epoch, args.epochs, acc_clean*100, acc_adv*100))
        print('================================================================')

        # save checkpoint
        if acc_adv > best_acc_adv:
            best_acc_adv = acc_adv
            best_acc_clean = acc_clean
            best_model = model.state_dict()
            best_epoch = epoch
            best_optimizer = optimizer.state_dict()

        file_name = os.path.join(save_dir, "last.checkpoint")
        checkpoint = {'epoch': epoch,
                    'state_dict': model.state_dict(),
                    'optimizer_state_dict': optimizer.state_dict()}

        if os.path.exists(file_name):
            print('Overwriting {}'.format(file_name))
        torch.save(checkpoint, file_name)

        break

    best_name = os.path.join(save_dir, "best.checkpoint")
    best_checkpoint = {'best_acc_adv': best_acc_adv,
                       'best_acc_clean': best_acc_clean,
                       'best_model':  best_model,
                       'best_epoch': best_epoch,
                       'best_optimizer': best_optimizer}
    
    torch.save(best_checkpoint, best_name)
    print("best acc adv: {:.2f}%, best acc clean: {:.2f}%".format(best_acc_adv*100, best_acc_clean*100))
    print("training is over!!!!")

if __name__ == '__main__':
    main()