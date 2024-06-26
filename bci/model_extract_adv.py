import os
import argparse

import torch
from copy import deepcopy
from torch.nn import Module
from torch.optim import Adam
import torch.nn.functional as F
from torch.nn import CrossEntropyLoss
from torch.utils.data import Dataset
from torch.utils.data import DataLoader
from torch.utils.tensorboard import SummaryWriter
from torcheeg.model_selection import KFoldGroupbyTrial

import utils
import data_loader
from acmmm24_811.models_arc import DeepConvNet
from acmmm24_811.models_arc import ShadowNet
from model_loader import ModelLoader


def PGD(model, b_x, b_y):
    label = b_y.to(device)
    b_x_attack = deepcopy(b_x)
    b_x_attack = b_x_attack.to(device)
    b_x_attack.requires_grad = True
    alpha = 0.001
    for _ in range(30):
        b_x_attack.requires_grad = True
        output = model(b_x_attack)
        loss = -1 * F.cross_entropy(output, b_y)
        loss.backward()
        grad = b_x_attack.grad.detach().sign()
        b_x_attack = b_x_attack.detach()
        b_x_attack -= alpha * grad
    pred_prob = output.detach()
    acc_n = (torch.argmax(pred_prob, dim=1) == b_y).sum().item()
    acc = round(acc_n / len(label), 2)
    return b_x_attack.detach(), acc


def train(
    teacher: Module,
    model: Module,
    data_loader: DataLoader,
    loss_fn: CrossEntropyLoss,
    optimizer: Adam,
) -> float:
    total_batches = len(data_loader)
    loss_record = []
    model.to(device)
    model.train()
    for batch_idx, batch_data in enumerate(data_loader):
        b_x = batch_data[0].to(device)
        b_y = batch_data[1].to(device)
        t_output = teacher(b_x)
        pred = torch.argmax(t_output, dim=1)
        x_adv, acc = PGD(model, b_x, pred)
        output = model(b_x)
        output_adv = model(x_adv)
        loss = loss_fn(output, pred) + loss_fn(output_adv, pred)
        #
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        #
        loss = loss.detach().item()
        loss_record.append(loss)
    mean_train_loss = sum(loss_record) / total_batches
    return mean_train_loss


def test(model: Module, data_loader: DataLoader, loss_fn: CrossEntropyLoss):
    total_sample_num = len(data_loader.dataset)
    num_batches = len(data_loader)
    model.eval()
    test_loss, correct_sample_num = 0, 0
    with torch.no_grad():
        for batch_data in data_loader:
            b_x = batch_data[0].to(device)
            b_y = batch_data[1].to(device)
            output = model(b_x)
            test_loss += loss_fn(output, b_y).item()

            correct_sample_num += (
                (output.argmax(1) == b_y).type(torch.float).sum().item()
            )
    #
    test_loss /= num_batches
    #
    test_accuracy = correct_sample_num / total_sample_num
    return test_loss, test_accuracy


def fit(
    teacher: Module,
    model: Module,
    kfold: KFoldGroupbyTrial,
    attack_dataset: Dataset,
    i: int,
) -> float:
    teacher.to(device)
    teacher.eval()
    model.train()
    optimizer = Adam(model.parameters(), lr=1e-3)
    loss_fn = CrossEntropyLoss()

    save_dir = f"./model/model_extract_adv/{args.teacher_name}_{args.student_name}/model_extract_{i}/"
    if not os.path.exists(save_dir):
        os.makedirs(save_dir, exist_ok=True)
    writer = SummaryWriter(save_dir.replace("/model/", "/result/"))
    best_acc = 0.0
    for split_idx, (train_data, test_data) in enumerate(kfold.split(attack_dataset)):
        train_loader = DataLoader(
            dataset=train_data, batch_size=args.batch_size, shuffle=True
        )
        test_loader = DataLoader(
            dataset=test_data, batch_size=args.batch_size, shuffle=False
        )
        #
        best_test_acc = 0
        for epoch_id in range(args.epochs):
            train_loss = train(
                teacher=teacher,
                model=model,
                data_loader=train_loader,
                loss_fn=loss_fn,
                optimizer=optimizer,
            )
            writer.add_scalar(
                "Loss/Train", train_loss, (split_idx * args.epochs) + epoch_id
            )
            test_loss, test_acc = test(
                model=model, data_loader=test_loader, loss_fn=loss_fn
            )
            writer.add_scalar(
                "Loss/Test", test_loss, (split_idx * args.epochs) + epoch_id
            )
            #
            if test_acc > best_test_acc:
                best_test_acc = test_acc

                torch.save(
                    model.state_dict(), os.path.join(save_dir, f"model_{split_idx}.pth")
                )
        #
        model.load_state_dict(
            torch.load(os.path.join(save_dir, f"model_{split_idx}.pth"))
        )
        _, test_acc = test(model, test_loader, loss_fn)
        writer.add_scalar("Acc/Test", test_acc, split_idx + 1)
        if test_acc > best_acc:
            best_acc = test_acc
            torch.save(model.state_dict(), os.path.join(save_dir, "model_best.pth"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=str, default="eegconformer")
    parser.add_argument(
        "--student_name",
        type=str,
        choices=["deepnet", "shallownet"],
    )
    parser.add_argument("--gpu", type=int)
    parser.add_argument(
        "--label", type=str, default="valence", choices=["valence", "arousal"]
    )
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--batch_size", type=int, default=64)
    parser.add_argument("--weight_decay", type=float, default=1e-4)
    args = parser.parse_args()

    utils.seed_everything(2023)
    #
    if torch.cuda.is_available():
        device = torch.device("cuda", args.gpu)

    source_model = ModelLoader.domain_ada("DEAP").load_model(mode="source")
    #
    train_dataset, attack_dataset = data_loader.load_split_dataset(
        model_name=args.model_name, label=args.label
    )
    #
    kfold = KFoldGroupbyTrial(
        n_splits=5, shuffle=True, split_path=f"./data/{args.teacher_name}/attack"
    )
    for i in range(4, 5):
        model = DeepConvNet(num_classes=2)
        acc = fit(source_model, model, kfold, attack_dataset, i)
