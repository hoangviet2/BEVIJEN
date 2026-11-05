"""Agent knowledge base: repo docs -> chunks -> Titan embeddings -> S3 Vectors (re-kb-vectors/re-kb-index).

Read by the `search_knowledge` Gateway tool (agents/tools/web_tools.py), which embeds the
query the same way (Titan v2, 512 dims, normalized; see app.ai.embed).
"""

from __future__ import annotations

import re
from pathlib import Path

from .ai import embed
from .config import Settings

DOCS = Path(__file__).resolve().parents[2] / "docs"
SOURCES = ["hsds", "one-degree", "SystemDesign/SKILL.md"]
VECTOR_BUCKET, INDEX = "re-kb-vectors", "re-kb-index"


def chunks(text: str, size: int = 1500) -> list[str]:
    """Split markdown at headings / blank lines into pieces of about `size` chars (never mid-paragraph,
    except a single paragraph longer than `size`, which is hard-split)."""
    out, cur = [], ""
    for part in re.split(r"\n(?=#)|\n\s*\n", text):
        part = part.strip()
        while len(part) > size:
            out.append(part[:size])
            part = part[size:]
        if cur and len(cur) + len(part) + 2 > size:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n\n{part}" if cur else part
    if cur.strip():
        out.append(cur)
    return [c for c in out if c.strip()]


def files() -> list[Path]:
    found = []
    for s in SOURCES:
        p = DOCS / s
        found += sorted(p.rglob("*.md")) if p.is_dir() else [p]
    return found


def load(settings: Settings) -> dict:
    # ponytail: keys are path#n and overwritten on reload; chunks of a doc that shrank stay until the index is recreated
    from . import aws
    s3v = aws.client("s3vectors", settings)
    batch, total = [], 0
    for f in files():
        rel = str(f.relative_to(DOCS))
        for i, c in enumerate(chunks(f.read_text())):
            batch.append({"key": f"{rel}#{i}", "data": {"float32": embed(settings, c)},
                          "metadata": {"source": rel, "text": c}})
            if len(batch) == 100:
                s3v.put_vectors(vectorBucketName=VECTOR_BUCKET, indexName=INDEX, vectors=batch)
                total, batch = total + len(batch), []
    if batch:
        s3v.put_vectors(vectorBucketName=VECTOR_BUCKET, indexName=INDEX, vectors=batch)
        total += len(batch)
    return {"files": len(files()), "chunks": total}
