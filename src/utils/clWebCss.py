"""The web dashboard's stylesheet: one small file per part of the UI in web/css/, joined in file-name order (the number prefix is the
cascade order) into the single /style.css the pages link. clWebUI serves the bundle through clSkin.recolor_css."""
import glob
import os


def css_files(web_dir):
    return sorted(glob.glob(os.path.join(web_dir, "css", "*.css")))


def bundle(web_dir):
    parts = []
    for path in css_files(web_dir):
        with open(path, "r", encoding="utf-8") as f:
            parts.append(f.read())
    return "\n".join(parts)
