"""Runs piper.train with torch's safe loader allowlisting pathlib and our own checkpoint set (best 3 by val_mel, a snapshot every SNAP epochs, last.ckpt)."""
import os, pathlib, torch
from lightning.pytorch.callbacks import ModelCheckpoint
torch.serialization.add_safe_globals([pathlib.PosixPath, pathlib.PurePosixPath])
import piper.train.__main__ as m


def callbacks():
    return [
        ModelCheckpoint(monitor="val_mel", mode="min", save_top_k=3, save_last=False,
                        filename="epoch={epoch}-val_mel={val_mel:.4f}", auto_insert_metric_name=False),
        ModelCheckpoint(every_n_epochs=int(os.environ.get("SNAP", 50)), save_top_k=-1, save_last=False,
                        filename="snap-epoch={epoch}", auto_insert_metric_name=False),
        ModelCheckpoint(monitor=None, save_top_k=1, every_n_epochs=1, filename="last", enable_version_counter=False),   # last.ckpt every epoch
    ]


if hasattr(m, "_default_callbacks"):        # current piper1-gpl builds its callbacks in a function; older ones kept a module-level list
    m._default_callbacks = callbacks
else:
    m._DEFAULT_CALLBACKS[:] = callbacks()
m.main()
