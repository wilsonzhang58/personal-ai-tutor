import hashlib
from pathlib import Path
import io
import re
from uuid import uuid4
from fastapi import HTTPException
from pypdf import PdfReader
from sqlalchemy import select
from .models import Material, SourceChunk, CourseConcept, TopicSource

MAX_BYTES = 10 * 1024 * 1024

def parse_upload(filename, content):
    if len(content) > MAX_BYTES:
        raise HTTPException(413, 'File exceeds 10 MB')
    if filename.lower().endswith('.md'):
        pages = [content.decode('utf-8-sig')]
    elif filename.lower().endswith('.pdf'):
        try:
            reader = PdfReader(io.BytesIO(content))
            if len(reader.pages) > 100:
                raise HTTPException(413, 'PDF exceeds 100 pages')
            pages = [page.extract_text() or '' for page in reader.pages]
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(422, f'PDF parsing failed: {type(exc).__name__}') from exc
    else:
        raise HTTPException(422, 'Only Markdown and text PDF are supported')
    return pages

def preview(db, course_id, filename, content):
    pages = parse_upload(filename, content)
    material = Material(id=str(uuid4()), course_id=course_id, title=filename, sha256=hashlib.sha256(content).hexdigest(), pages=pages)
    upload_dir = Path(__file__).resolve().parents[2] / 'data' / 'uploads'
    upload_dir.mkdir(parents=True, exist_ok=True)
    suffix = '.pdf' if filename.lower().endswith('.pdf') else '.md'
    (upload_dir / (material.id + suffix)).write_bytes(content)
    db.add(material)
    db.commit()
    return material

def split_pages(pages, limit=1200):
    for page_no, page in enumerate(pages, 1):
        paragraphs = re.split(r'\n\s*\n', page)
        buffer = ''
        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            while len(paragraph) > limit:
                if buffer:
                    yield page_no, buffer
                    buffer = ''
                yield page_no, paragraph[:limit]
                paragraph = paragraph[limit:]
            if len(buffer) + len(paragraph) + 2 > limit and buffer:
                yield page_no, buffer
                buffer = ''
            buffer = (buffer + '\n\n' + paragraph).strip()
        if buffer:
            yield page_no, buffer

def confirm(db, material, pages):
    if material.confirmed and material.pages == pages:
        return material
    if not any(page.strip() for page in pages):
        raise HTTPException(422, 'No text found. Paste corrected page text before confirming.')
    old_chunks=db.scalars(select(SourceChunk).where(SourceChunk.material_id==material.id,SourceChunk.material_version==material.version)).all() if material.confirmed else []
    old_by_text={(r.page,r.text):r for r in old_chunks}
    if material.confirmed:
        material.version += 1
    material.pages = pages
    material.confirmed = True
    new_chunks=[]
    for number, (page, body) in enumerate(split_pages(pages), 1):
        chunk_id = f'{material.id}-v{material.version}-p{page}-c{number}'
        old=old_by_text.get((page,body))
        row=SourceChunk(id=chunk_id,material_id=material.id,course_id=material.course_id,material_version=material.version,page=page,text=body,
                        embedding=old.embedding if old else None,embedding_model=old.embedding_model if old else None)
        db.add(row);new_chunks.append(row)
    db.flush()
    # Retarget live retrieval anchors, while immutable lesson citations keep old chunks.
    for old in old_chunks:
        same=next((r for r in new_chunks if r.page==old.page and r.text==old.text),None)
        replacement=same or next((r for r in new_chunks if r.page==old.page),None)
        for link in db.scalars(select(CourseConcept).where(CourseConcept.source_chunk_id==old.id)).all():
            link.source_chunk_id=replacement.id if replacement else None
        for link in db.scalars(select(TopicSource).where(TopicSource.chunk_id==old.id)).all():
            if replacement and not db.get(TopicSource,(link.topic_id,replacement.id)):
                db.add(TopicSource(topic_id=link.topic_id,chunk_id=replacement.id));db.flush()
            db.delete(link)
    db.commit()
    return material
