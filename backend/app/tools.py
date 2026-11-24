from .models import SourceChunk
from .retrieval import search

def search_course_materials(db, query, course_id, allowed_course_ids):
    return search(db, query, course_id, allowed_course_ids=allowed_course_ids)

def read_source_chunk(db, chunk_id, allowed_ids):
    if chunk_id not in allowed_ids:
        return {'error': 'chunk outside retrieved scope'}
    row = db.get(SourceChunk, chunk_id)
    return {'id': row.id, 'text': row.text, 'page': row.page, 'material_id': row.material_id} if row else {'error': 'chunk missing'}
