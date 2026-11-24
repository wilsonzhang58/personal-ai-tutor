import os
import hashlib
from functools import lru_cache
from sqlalchemy import select
from .models import SourceChunk, Material

MODEL_NAME = 'intfloat/multilingual-e5-small'

@lru_cache(maxsize=1)
def encoder():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(MODEL_NAME, device='cpu')

def encode(texts, prefix):
    import torch
    with torch.inference_mode():
        return encoder().encode([prefix + text for text in texts], convert_to_tensor=True, normalize_embeddings=True)

def top_k(query_vector, vectors, k):
    import torch
    if len(vectors) == 0 or k <= 0:
        return []
    scores = torch.matmul(vectors, query_vector)
    values, indices = torch.topk(scores, min(k, len(vectors)))
    return [(int(i), float(v)) for i, v in zip(indices, values)]

def search(db, query, course_id, k=4, allowed_course_ids=None):
    import torch
    allowed = allowed_course_ids or [course_id]
    rows = db.scalars(select(SourceChunk).join(Material, SourceChunk.material_id == Material.id).where(SourceChunk.course_id.in_(allowed), Material.confirmed == True, SourceChunk.material_version == Material.version)).all()
    if not rows:
        return []
    # Store vectors per chunk. Corrections copy unchanged vectors, so a changed page
    # never invalidates all courses' passage cache.
    missing=[r for r in rows if r.embedding is None or r.embedding_model!=MODEL_NAME]
    if missing:
        fresh=encode([r.text for r in missing],'passage: ')
        for row,vector in zip(missing,fresh):row.embedding=vector.tolist();row.embedding_model=MODEL_NAME
        db.commit()
    vectors=torch.tensor([row.embedding for row in rows])
    q = encode([query], 'query: ')[0]
    ranked = top_k(q, vectors, k)
    return [{'id': rows[i].id, 'course_id': rows[i].course_id, 'material_id': rows[i].material_id, 'page': rows[i].page, 'text': rows[i].text, 'score': score} for i, score in ranked]
