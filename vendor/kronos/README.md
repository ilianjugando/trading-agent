# Vendored: shiyu-coder/Kronos (model code only)

Source: https://github.com/shiyu-coder/Kronos
Commit: `67b630e67f6a18c9e9be918d9b4337c960db1e9a` (master, as of 2026-09-08)
License: MIT (see `LICENSE` in this directory — original copyright ShiYu, 2025)

Only `model/__init__.py`, `model/kronos.py`, `model/module.py` are vendored
here — the architecture/inference code, unmodified. Pretrained weights are
NOT vendored; they're downloaded on demand from Hugging Face Hub
(`NeoQuasar/Kronos-*`) the first time `signals/kronos_forecast.py` runs
with `ENABLE_KRONOS_FORECAST=true`, and cached locally by `huggingface_hub`
after that.

Vendored (instead of a git submodule or `pip install`) because the
upstream repo has no PyPI package and no `setup.py`/`pyproject.toml` --
`model/kronos.py` imports as `from model.module import *`, which needs
`model/`'s *parent* directory on `sys.path`. `signals/kronos_forecast.py`
adds this directory (`vendor/kronos`) to `sys.path` lazily, only when
`ENABLE_KRONOS_FORECAST=true` and the feature is actually invoked.

Not modified from upstream. To update, re-fetch these 3 files from a newer
commit and update the commit hash above.
