# GPU notebook boundary

Notebooks are thin execution surfaces, not sources of business logic. A Colab or Kaggle notebook should:

1. clone the pinned repository revision;
2. install from `uv.lock`;
3. download a versioned processed feature artifact;
4. call package training functions;
5. upload the model state, preprocessor, run manifest, and metrics.

All feature construction, model definitions, evaluation, and export code belongs in `src/edgar_moe` and must be covered by local tests.
