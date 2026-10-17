#!/usr/bin/env python3
"""BM25 pur Python (zero dependance) sur les rapports/docs locaux, pour donner
au harnais une vraie recherche RAG avant d'interroger le LLM -- pas une
simulation, un index reel construit sur de vrais fichiers .md/.txt.

Usage:
  python bm25.py index --root "E:\\lama-tensorRT 1050-5070" --out index.json
  python bm25.py search --index index.json --query "qwen35 tokenizer" --top 5
"""
import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
IGNORE_DIRS = {".git", "node_modules", "__pycache__", "build", ".gradle", "venv", ".venv"}


def tokenize(text):
    return [t.lower() for t in TOKEN_RE.findall(text)]


CHUNK_CHARS = 1800
CHUNK_OVERLAP = 200
PARA_SPLIT_RE = re.compile(r"\n{2,}|\n(?=#{1,6}\s)")


def chunk_text(text):
    """BUG REEL TROUVE EN UTILISANT LE HARNAIS (2026-10-17) : un excerpt fixe
    de 300 caracteres au DEBUT du fichier donnait un extrait tronque (vu sur
    SORTIE_MTP.md). Premiere correction (decoupe aveugle a taille fixe) a
    introduit une LIMITE restante (notee a la demande de liste des manques) :
    une coupe pouvait tomber en plein milieu d'une phrase/section. Corrige
    ici : on decoupe d'abord sur les frontieres semantiques (paragraphes
    vides, titres Markdown), puis seulement si un paragraphe depasse encore
    CHUNK_CHARS on le sous-decoupe a taille fixe (cas des gros blocs de code
    ou JSON sans separateur naturel)."""
    paragraphs = [p for p in PARA_SPLIT_RE.split(text) if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    chunks = []
    buf = ""
    for para in paragraphs:
        if len(para) > CHUNK_CHARS:
            if buf:
                chunks.append(buf)
                buf = ""
            start = 0
            while start < len(para):
                end = start + CHUNK_CHARS
                chunks.append(para[start:end])
                if end >= len(para):
                    break
                start = end - CHUNK_OVERLAP
            continue
        if len(buf) + len(para) + 2 > CHUNK_CHARS:
            if buf:
                chunks.append(buf)
            buf = para
        else:
            buf = (buf + "\n\n" + para) if buf else para
    if buf:
        chunks.append(buf)
    return chunks or [text]


def build_index(root, extensions=(".md", ".txt", ".json")):
    root = Path(root)
    docs = []
    for p in root.rglob("*"):
        if p.is_dir() or p.suffix.lower() not in extensions:
            continue
        if any(part in IGNORE_DIRS for part in p.parts):
            continue
        if p.name == "bm25_index.json":
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not text.strip():
            continue
        rel = str(p.relative_to(root))
        chunks = chunk_text(text)
        for i, chunk in enumerate(chunks):
            label = rel if len(chunks) == 1 else f"{rel}#chunk{i}"
            docs.append({"path": label, "tokens": tokenize(chunk)[:20000],
                         "excerpt": chunk[:500].replace("\n", " ")})
    return docs


def bm25_search(docs, query, top=5, k1=1.5, b=0.75):
    q_tokens = tokenize(query)
    n = len(docs)
    if n == 0:
        return []
    avgdl = sum(len(d["tokens"]) for d in docs) / n
    df = Counter()
    for d in docs:
        for t in set(d["tokens"]):
            df[t] += 1
    scores = []
    for d in docs:
        tf = Counter(d["tokens"])
        dl = len(d["tokens"])
        score = 0.0
        for t in q_tokens:
            if t not in tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            freq = tf[t]
            score += idf * (freq * (k1 + 1)) / (freq + k1 * (1 - b + b * dl / avgdl))
        if score > 0:
            scores.append((score, d))
    scores.sort(key=lambda x: -x[0])
    return [{"path": d["path"], "score": round(s, 3), "excerpt": d["excerpt"]} for s, d in scores[:top]]


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("index")
    i.add_argument("--root", required=True)
    i.add_argument("--out", required=True)

    s = sub.add_parser("search")
    s.add_argument("--index", required=True)
    s.add_argument("--query", required=True)
    s.add_argument("--top", type=int, default=5)

    args = ap.parse_args(argv)
    if args.cmd == "index":
        docs = build_index(args.root)
        Path(args.out).write_text(json.dumps(docs, ensure_ascii=False), encoding="utf-8")
        print(f"indexe {len(docs)} document(s) -> {args.out}")
    else:
        docs = json.loads(Path(args.index).read_text(encoding="utf-8"))
        results = bm25_search(docs, args.query, args.top)
        print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
