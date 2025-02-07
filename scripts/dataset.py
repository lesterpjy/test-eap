# %%
from functools import partial
from typing import Optional

import pandas as pd
import numpy as np
import torch
from torch.utils.data import Dataset, DataLoader

from eap.utils import model2family

import os
cwd = os.getcwd()


def collate_EAP(xs, task):
    clean, corrupted, labels = zip(*xs)
    clean = list(clean)
    corrupted = list(corrupted)
    if "hypernymy" not in task:
        labels = torch.tensor(labels)
    return clean, corrupted, labels


class EAPDataset(Dataset):
    def __init__(self, task: str, model_name: str, filename: Optional[str] = None):
        if filename is None:
            print(cwd)
            self.df = pd.read_csv(f"/local/scripts/data/{task}/{model2family(model_name)}.csv")
        else:
            self.df = pd.read_csv(f"data/{task}/{filename}")

        self.task = task

    def __len__(self):
        return len(self.df)

    def shuffle(self):
        self.df = self.df.sample(frac=1)

    def head(self, n: int):
        self.df = self.df.head(n)

    def __getitem__(self, index):
        row = self.df.iloc[index]
        label = None
        if self.task == "ioi":
            label = [row["correct_idx"], row["incorrect_idx"]]
        elif "greater-than" in self.task:
            label = row["correct_idx"]
        elif "hypernymy" in self.task:
            answer = torch.tensor(eval(row["answers_idx"]))
            corrupted_answer = torch.tensor(eval(row["corrupted_answers_idx"]))
            label = [answer, corrupted_answer]
        elif "fact-retrieval" in self.task:
            label = [row["country_idx"], row["corrupted_country_idx"]]
        elif "gender" in self.task:
            label = [row["clean_answer_idx"], row["corrupted_answer_idx"]]
        elif self.task == "sva":
            label = row["plural"]
        elif self.task == "colored-objects":
            label = [row["correct_idx"], row["incorrect_idx"]]
        elif self.task in {"dummy-easy", "dummy-medium", "dummy-hard"}:
            label = 0
        else:
            raise ValueError(f"Got invalid task: {self.task}")
        return row["clean"], row["corrupted"], label

    def to_dataloader(self, batch_size: int):
        return DataLoader(
            self, batch_size=batch_size, collate_fn=partial(collate_EAP, task=self.task)
        )
