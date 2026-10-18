# ml/iris-training

**Teaching example. Not a production MLOps template.**

This Plan intentionally does one thing: select `ml/iris-train` and expose its deterministic training/evaluation inputs at the run boundary. It contains no executable Steps.

Use it to learn the Oh Goon authoring model for ML work:

```text
Plan ml/iris-training
  → Goon ml/iris-train
      → lock check
      → explicit dependency cache acquisition
      → offline deterministic train/evaluate
```

The Plan intentionally leaves CPU/memory/process caps unspecified; effective capacity comes from the admitted executor and remains visible in resolved Plan/admission evidence. See the Goon README for repository requirements, outputs, and the production ML concerns intentionally omitted from this example.
