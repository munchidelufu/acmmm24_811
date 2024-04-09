import os

import torch
import argparse
import importlib
import torch.nn.functional as F
from transformers import BertTokenizer
from torch.utils.data import DataLoader

import utils
from models_arc import Bert
from models_arc import TextCNN
from models_arc import TextRCNN
from models_arc import TextRNN
from dataset import TextDataSet


def collate_fn(batch):
    encode_inputs, labels = zip(*batch)
    input_ids = torch.stack([s["input_ids"][0] for s in list(encode_inputs)], dim=0).to(
        device
    )
    token_type_ids = torch.stack(
        [s["token_type_ids"][0] for s in list(encode_inputs)], dim=0
    ).to(device)
    attention_mask = torch.stack(
        [s["attention_mask"][0] for s in list(encode_inputs)], dim=0
    ).to(device)
    encode_inputs = {
        "input_ids": input_ids,
        "token_type_ids": token_type_ids,
        "attention_mask": attention_mask,
    }
    labels = torch.tensor(list(labels)).to(device)
    return encode_inputs, labels


def train(num, model, train_loader, dev_loader, optimizer):
    model.train()
    model.to(device)
    total_batch = 0
    dev_best_loss = float("inf")
    last_improve = 0
    flag = False
    save_dir = f"./model/THUCNews/{args.model_type}/"
    os.makedirs(save_dir, exist_ok=True)
    for epoch in range(args.epochs):
        for i, (datas, labels) in enumerate(train_loader):
            outputs = model(datas)
            loss = F.cross_entropy(outputs, labels)
            # Clear and update gradient
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            if total_batch % 100 == 0:
                train_acc = (outputs.argmax(1) == labels).sum().item() / len(labels)
                dev_acc, dev_loss = test(model, dev_loader)
                if dev_loss < dev_best_loss:
                    dev_best_loss = dev_loss
                    torch.save(
                        model.state_dict(),
                        os.path.join(save_dir, f"{args.model_name}_{num}.ckpt"),
                    )
                    last_improve = total_batch
                model.train()
            total_batch += 1
            #
            if total_batch - last_improve > 1000:
                flag = True
                break
        if flag:
            break


def test(model, data_iter):
    model.eval()
    correct_num, total_num, total_loss = 0, 0, 0
    with torch.no_grad():
        for datas, labels in data_iter:
            outputs = model(datas)
            loss = F.cross_entropy(outputs, labels)
            total_loss += loss.item()
            correct_num += (outputs.argmax(1) == labels).sum().item()
            total_num += len(labels)
    acc = correct_num / total_num
    loss = total_loss / len(data_iter)
    return acc, loss


def fit(num, model, train_loader, dev_loader, test_loader):
    optimizer = torch.optim.Adam(model.parameters(), lr=args.learning_rate)
    train(num, model, train_loader, dev_loader, optimizer)
    model.load_state_dict(
        torch.load(
            f"./model/THUCNews/{args.model_type}/{args.model_name}_{num}.ckpt",
            map_location=device,
        )
    )
    acc, loss = test(model, test_loader)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model_name",
        type=str,
        choices=["TextCNN", "TextRNN", "DPCNN"],
        help="choose a model",
    )
    parser.add_argument("--model_type", type=str, default="irrelevant")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--gpu", type=int, default=1)
    parser.add_argument("--learning_rate", type=float, default=5e-5)
    parser.add_argument("--batch_size", type=int, default=128)

    args = parser.parse_args()

    utils.seed_everything(2023)
    if torch.cuda.is_available():
        device = torch.device("cuda", args.gpu)
    # tokenizer
    tokenizer = BertTokenizer.from_pretrained("bert-base-chinese")

    # preprocess data
    train_path = f"./data/THUCNews/source.txt"
    dev_path = f"./data/THUCNews/dev.txt"
    test_path = f"./data/THUCNews/test.txt"
    train_data = TextDataSet(train_path, tokenizer)
    dev_data = TextDataSet(dev_path, tokenizer)
    test_data = TextDataSet(test_path, tokenizer)

    train_loader = DataLoader(
        dataset=train_data,
        batch_size=args.batch_size,
        shuffle=True,
        collate_fn=collate_fn,
    )
    dev_loader = DataLoader(
        dataset=dev_data,
        batch_size=args.batch_size,
        shuffle=False,
        collate_fn=collate_fn,
    )
    test_loader = DataLoader(
        dataset=test_data,
        batch_size=args.batch_size,
        shuffle=False,
        collate_fn=collate_fn,
    )

    for num in range(5):
        model = importlib.import_module(f"models.{args.model_name}")
        model = TextRNN().to(device)
