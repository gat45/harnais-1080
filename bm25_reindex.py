#!/usr/bin/env python3
"""Reconstruit bm25_index.json sur les deux racines reellement utilisees par
le harnais (workspace PC + archive_s3_release ou vit la doc du modele)."""
import json
from pathlib import Path

import bm25

HERE = Path(__file__).resolve().parent
ROOTS = [HERE, Path(r"F:\archive_s3_release")]


def main():
    all_docs = []
    for root in ROOTS:
        if not root.exists():
            continue
        docs = bm25.build_index(str(root))
        if root != HERE:
            prefix = root.name + "/"
            for d in docs:
                d["path"] = prefix + d["path"]
        all_docs.extend(docs)
    out = HERE / "bm25_index.json"
    out.write_text(json.dumps(all_docs, ensure_ascii=False), encoding="utf-8")
    print(f"indexe {len(all_docs)} chunks -> {out}")


if __name__ == "__main__":
    main()
