# %%
import os
from argparse import ArgumentParser
from functools import partial
from pathlib import Path
import json

import pandas as pd
import numpy as np
import torch
from transformer_lens import HookedTransformer
import matplotlib.pyplot as plt
from tqdm import tqdm
from huggingface_hub import login

from eap.graph import Graph
from eap.attribute_mem import attribute
from eap.evaluate_graph import evaluate_graph, evaluate_baseline

from dataset import EAPDataset
from metrics import get_metric

os.environ["CUDA_LAUNCH_BLOCKING"] = "1"

if torch.cuda.is_available():
    device = torch.device("cuda")  # Use CUDA (NVIDIA GPU)
    print("Using CUDA device:", torch.cuda.get_device_name(0))
elif torch.backends.mps.is_available():
    device = torch.device("mps")  # Use MPS (Apple Silicon GPU)
    print("Using MPS device")
else:
    device = torch.device("cpu")  # Fallback to CPU
    print("Using CPU device")

# %%
parser = ArgumentParser()
parser.add_argument("--model", type=str, required=True)
parser.add_argument("--task", type=str, required=True)
parser.add_argument("--metric", type=str, required=True)
parser.add_argument("--end", type=int, default=1001)
parser.add_argument("--batch_size", type=int, required=True)
args = parser.parse_args()

model_name = args.model
model_name_noslash = model_name.split("/")[-1]
login(token="")
model = HookedTransformer.from_pretrained(
    model_name,
    center_writing_weights=False,
    center_unembed=False,
    fold_ln=False,
    device=device,
)
model.cfg.use_split_qkv_input = True
model.cfg.use_attn_result = True
model.cfg.use_hook_mlp_in = True
model.cfg.ungroup_grouped_query_attention = True  # use ungrouped query attention
print(model.cfg)
print("model name", model_name_noslash)

# %%
labels = ["EAP", "EAP-IG", "EAP-IG-KL"]
task = args.task
task_metric_name = args.metric
ds = EAPDataset(task, model_name)
ds.head(3000)

batch_size = args.batch_size
dataloader = ds.to_dataloader(batch_size)
task_metric = get_metric(task_metric_name, task, model=model)
kl_div = get_metric("kl_divergence", task, model=model)

# %%
print("Evaluating baseline")
baseline = (
    evaluate_baseline(model, dataloader, partial(task_metric, mean=False, loss=False))
    .mean()
    .item()
)
corrupted_baseline = (
    evaluate_baseline(
        model,
        dataloader,
        partial(task_metric, mean=False, loss=False),
        run_corrupted=True,
    )
    .mean()
    .item()
)
print("Baseline:", baseline)
print("Corrupted baseline:", corrupted_baseline)

# %%
# Instantiate a graph with a model
print("Creating graphs")
g1 = Graph.from_model(model)
# Attribute using the model, graph, clean / corrupted data (as lists of lists of strs), your metric, and your labels (batched)
attribute(model, g1, dataloader, partial(task_metric, mean=True, loss=True))
Path(f"graphs/{model_name_noslash}").mkdir(exist_ok=True, parents=True)
g1.to_json(f"graphs/{model_name_noslash}/{task}_vanilla.json")
print(f"graph saved to graphs/{model_name_noslash}/{task}_vanilla.json")
# %%
# Instantiate a graph with a model
g2 = Graph.from_model(model)
# Attribute using the model, graph, clean / corrupted data (as lists of lists of strs), your metric, and your labels (batched)
attribute(
    model,
    g2,
    dataloader,
    partial(task_metric, mean=True, loss=True),
    integrated_gradients=5,
)
g2.to_json(f"graphs/{model_name_noslash}/{task}_task.json")
print(f"graph saved to graphs/{model_name_noslash}/{task}_task.json")
# %%
# Instantiate a graph with a model
g3 = Graph.from_model(model)
# Attribute using the model, graph, clean / corrupted data (as lists of lists of strs), your metric, and your labels (batched)
attribute(
    model, g3, dataloader, partial(kl_div, mean=True, loss=True), integrated_gradients=5
)
g3.to_json(f"graphs/{model_name_noslash}/{task}_kl.json")
print(f"graph saved to graphs/{model_name_noslash}/{task}_kl.json")

# %%
gs = [g1, g2, g3]
n_edges = []
results = []
s = 500 
e = 10000 
step = 500
first_steps = list(range(250, 1000, 250))
later_steps = list(range(s, e, step))

steps = later_steps
print("begin steps", steps)
with tqdm(total=len(gs) * len(steps)) as pbar:
    for i in steps:
        n_edge = []
        result = []
        for graph, label in zip(gs, labels):
            graph.apply_greedy(i, absolute=True)
            graph.prune_dead_nodes(prune_childless=True, prune_parentless=True)
            n = graph.count_included_edges()
            graph.to_json(
                f"graphs/{model_name_noslash}/{task}_{label}_step{i}_{n}edges.json"
            )

            r = evaluate_graph(
                model,
                graph,
                dataloader,
                partial(task_metric, mean=False, loss=False),
                quiet=True,
            )
            n_edge.append(n)
            result.append(r.mean().item())
            pbar.update(1)
        n_edges.append(n_edge)
        results.append(result)
        # Save a temporary copy of n_edges and results
        temp_data = {
            "steps": steps,
            "n_edges": n_edges,
            "results": results,
        }

        with open(
            f"results/pareto/{model_name_noslash}/temp_{task}.json",
            "w",
        ) as fp:
            json.dump(temp_data, fp)

n_edges = np.array(n_edges)
results = np.array(results)
print(f"run complete, n_edges: {n_edges}, results: {results}")
# %%
d = {
    "baseline": [baseline] * len(steps),
    "corrupted_baseline": [corrupted_baseline] * len(steps),
    "edges": steps,
}

for i, label in enumerate(labels):
    d[f"edges_{label}"] = n_edges[:, i].tolist()
    d[f"loss_{label}"] = results[:, i].tolist()
df = pd.DataFrame.from_dict(d)
Path(f"results/pareto/{model_name_noslash}/csv").mkdir(exist_ok=True, parents=True)
print(f"results saved to results/pareto/{model_name_noslash}/csv/{task}.csv")
df.to_csv(f"results/pareto/{model_name_noslash}/csv/{task}.csv", index=False)
