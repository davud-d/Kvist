# Kvist's own models

A series of small open models trained for one environment: Kvist. The aim is not a
general model that also works in Kvist, but a model that works best in Kvist: one
that knows its actions and their arguments, batches calls the way the runtime runs them,
reads tool results as Kvist returns them, keeps to its role and knows when the task is done.

Status: data collection is built ([trajectories.md](trajectories.md)); the dataset
exporter, the training and the evaluation are not.

## Why a model of our own

Large models work well in Kvist but cost money on every step and need the network.
Small open models run locally, but in an agent loop they fail mostly on the
environment, not on the task: a malformed call, an argument the action does not take,
a batch the runtime refuses, a final answer before the work is checked, a loop over the
same failing command. Each of these is something a model can learn from examples of
the same environment done right, because the environment does not change from task to
task.

So what the series learns is how Kvist works, from Kvist's own runs, not general coding
knowledge. A small model will not learn to reason like the model that taught it; it can
learn to stop failing on the protocol, and that is where small models lose most of
their runs today.

## The series

| Size | Role |
|---|---|
| ~4B | First and cheapest: proves that the data helps. Trained first. |
| ~8B | Trained only after the 4B beats its own base model on held-out tasks. |

The base models are current open instruct models of these sizes (Qwen3-4B / Qwen3-8B
class, or newer when training starts). Each one is compared with its own untrained base,
never only with large models.

**The first goal: a trained 4B that does the work of an untrained 8B.** On the same
held-out tasks, with the same Kvist commit, three models run: the base 4B, the trained
4B and the base 8B of the same family. The goal is met when the trained 4B solves at
least as many tasks as the base 8B and does better on the environment's measures
(invalid calls, refused batches, answers before the work was checked). This is the bet
of the whole series in one test: a small model loses mostly on the environment, and the
environment can be taught. The base 8B is only evaluated for this, not trained.

## Data

Every example is a real Kvist run recorded with `KVIST_TRAJECTORY_DIR`: each model call
exactly as sent and received, the reasoning, and the result of every tool call. Two
sources:

- **Benchmark runs.** Terminal-Bench tasks through the Harbor adapter
  ([benchmarks.md](benchmarks.md)). The verifier's reward says whether a run solved
  its task, so success is checked by tests, not by the model's own claim.
- **Everyday sessions.** Real work in Kvist, rated by hand with `/rate` as a good or a
  bad example.

The teacher is DeepSeek V4.1 Flash, the strongest model of its family on the API
(stronger than the Pro version, which the API no longer offers), cheap enough for
thousands of runs, and one teacher gives one consistent style. A task it fails is
attempted again, several times and at a higher reasoning effort, and the attempts that
pass the tests are kept, so the hard part of the dataset is not empty. Before data is
used for training, the teacher's terms of use must allow it: the terms of some providers
forbid training other models on their outputs.

Only runs that succeeded (reward, or rated good) become training examples. Bad runs are
kept for later: as preference pairs, and as a record of where the teacher itself fails.

## Training

- **Examples.** One example per step: the prompt the model saw at that step and the
  response it gave. The loss is on the response only. Kvist rebuilds its context at
  every step, so a run is not one long conversation.
- **Reasoning.** Kept apart in the data, so a dataset can be built with it or without
  it. The first version is trained without it or with a hard cap: long reasoning eats
  the context a small model has least of.
- **Context.** Trained on the prompts the model will really see, at a context budget
  that suits a small model (8–12K tokens), not at the teacher's.
- **Method.** LoRA / QLoRA on one 24 GB GPU (RTX 3090 class). The token count of the
  dataset is measured before the rent starts: prompts repeat at every step, and a
  thousand runs can reach hundreds of millions of tokens.

## Evaluation

- Held-out Terminal-Bench tasks the model never saw in training, through the same
  Harbor adapter.
- Every model is measured against its untrained base on the same tasks, with the same
  Kvist commit.
- Besides the solved share: invalid calls per run, refused batches, steps per task and
  answers given before the work was checked. These are what the training is for.

## Privacy

Trajectories hold the workspace's code and paths. Secrets are redacted on recording,
but a dataset made from everyday sessions is private data: it is checked before it
leaves the machine and never published as it is.
