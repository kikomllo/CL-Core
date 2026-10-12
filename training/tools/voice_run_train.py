"""Runs piper.train with torch's safe loader allowlisting pathlib and a checkpoint set that doesn't need the MOS predictor."""
import os, pathlib, torch
from lightning.pytorch.callbacks import ModelCheckpoint
torch.serialization.add_safe_globals([pathlib.PosixPath, pathlib.PurePosixPath])
import piper.train.__main__ as m
m._DEFAULT_CALLBACKS[:] = [
    ModelCheckpoint(monitor="val_mel", mode="min", save_top_k=3, save_last=True,
                    filename="epoch={epoch}-val_mel={val_mel:.4f}", auto_insert_metric_name=False),
    ModelCheckpoint(every_n_epochs=int(os.environ.get("SNAP", 50)), save_top_k=-1, save_last=False,
                    filename="snap-epoch={epoch}", auto_insert_metric_name=False),
]
m.main()
