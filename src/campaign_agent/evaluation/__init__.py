"""Evaluation and model selection (M10): a labelled benchmark, a harness, metrics and a report.

The harness runs the same prompts, schemas and deterministic pipeline the service uses, so what it measures is what
users get. The "oracle" mode answers every case with its gold patch instead of a model: it measures nothing about a
model, but it proves the labels agree with the merger and the gap engine, and it runs in CI.
"""
