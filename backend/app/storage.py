"""Private course folders and non-overwriting file storage; legacy originals stay intact."""
import hashlib
import re
import threading
from pathlib import Path
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import select
from .models import Course, Material, CourseFolder, MaterialFile
from .ingestion import parse_upload

ROOT = Path(__file__).resolve().parents[2]
PRIVATE_ROOT = ROOT / 'private_materials'
UPLOAD_LOCK = threading.RLock()

def folder_path(name):
    if not name or name in {'.','..'} or len(name)>100 or any(c in name for c in '/\\\x00') or name.startswith('.'):
        raise HTTPException(422,'目录名称只能是一层普通文件夹名称')
    path=PRIVATE_ROOT/name
    if path.is_symlink() or path.resolve().parent!=PRIVATE_ROOT.resolve():
        raise HTTPException(422,'不能使用符号链接或工作区外目录')
    return path

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def discover(db):
    PRIVATE_ROOT.mkdir(parents=True,exist_ok=True)
    result=[]
    for path in sorted(PRIVATE_ROOT.iterdir()):
        if not path.is_dir() or path.is_symlink() or path.name.startswith('.'): continue
        files=[p for p in sorted(path.iterdir()) if p.is_file() and not p.is_symlink() and p.suffix.lower() in {'.pdf','.md'}]
        hashes={digest(p) for p in files}
        mapped=db.get(CourseFolder,path.name)
        matches={m.course_id for m in db.scalars(select(Material)).all() if m.sha256 in hashes and not m.course_id.endswith('_demo')}
        inferred=next(iter(matches)) if len(matches)==1 else None
        cid=mapped.course_id if mapped else inferred
        result.append({'name':path.name,'course_id':cid,'mapped':bool(mapped),'course_title':db.get(Course,cid).title if cid else None,'files':[p.name for p in files]})
    return result

def bind_folder(db,name,course_id=None):
    with UPLOAD_LOCK:
        path=folder_path(name)
        mapping=db.get(CourseFolder,name)
        if mapping:
            if course_id and course_id!=mapping.course_id: raise HTTPException(409,'该目录已关联另一门课')
            return mapping
        info=next((f for f in discover(db) if f['name']==name),None)
        inferred=info['course_id'] if info else None
        if inferred and course_id and inferred!=course_id: raise HTTPException(409,'目录中的已有资料属于另一门课程')
        cid=course_id or inferred
        if cid:
            if not db.get(Course,cid): raise HTTPException(404,'课程不存在')
            old=db.scalar(select(CourseFolder).where(CourseFolder.course_id==cid))
            if old: raise HTTPException(409,'课程已绑定目录：'+old.folder)
        else:
            cid=str(uuid4());db.add(Course(id=cid,title=name,description='本地私人课件目录：'+name));db.flush()
        path.mkdir(parents=True,exist_ok=True)
        mapping=CourseFolder(folder=name,course_id=cid);db.add(mapping)
        # Attach identical existing local files without changing material IDs or legacy copies.
        for p in path.iterdir():
            if not p.is_file() or p.is_symlink() or p.suffix.lower() not in {'.pdf','.md'}: continue
            sha=digest(p)
            for material in db.scalars(select(Material).where(Material.course_id==cid,Material.sha256==sha)).all():
                if not db.get(MaterialFile,material.id): db.add(MaterialFile(material_id=material.id,path=str(p.relative_to(ROOT))))
        db.commit()
        return mapping

def original_path(db,material):
    file=db.get(MaterialFile,material.id)
    if file:
        path=ROOT/file.path
        if path.resolve().is_relative_to(PRIVATE_ROOT.resolve()) and path.is_file() and not path.is_symlink(): return path
    legacy=ROOT/'data'/'uploads'/(material.id+Path(material.title).suffix.lower())
    return legacy

def upload_one(db,mapping,filename,content):
    with UPLOAD_LOCK:
        if not filename or Path(filename).name!=filename or '\\' in filename or '\x00' in filename or filename.startswith('.'):
            raise HTTPException(422,'文件名不能包含路径')
        sha=hashlib.sha256(content).hexdigest()
        existing=db.scalar(select(Material).where(Material.course_id==mapping.course_id,Material.sha256==sha))
        path=folder_path(mapping.folder)
        if existing and db.get(MaterialFile,existing.id) and original_path(db,existing).exists():
            return existing,'duplicate',original_path(db,existing)
        pages=parse_upload(filename,content)
        target=path/filename
        if target.is_symlink(): raise HTTPException(422,'目标文件不能是符号链接')
        if target.exists() and digest(target)!=sha:
            target=path/(Path(filename).stem+'--'+sha[:12]+Path(filename).suffix.lower())
        if target.exists() and (target.is_symlink() or digest(target)!=sha): raise HTTPException(409,'目标名称冲突，请重命名后重试')
        if not target.exists():
            with target.open('xb') as output: output.write(content)
        if existing:
            material=existing; status='duplicate'
        else:
            material=Material(id=str(uuid4()),course_id=mapping.course_id,title=filename,sha256=sha,pages=pages)
            db.add(material);db.flush();status='parsed'
        link=db.get(MaterialFile,material.id)
        if link: link.path=str(target.relative_to(ROOT))
        else: db.add(MaterialFile(material_id=material.id,path=str(target.relative_to(ROOT))))
        db.commit()
        return material,status,target
