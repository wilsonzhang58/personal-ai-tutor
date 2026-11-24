"""Persistent explicit-start jobs; polling and reopening never schedule generation."""
import hashlib
import threading
from sqlalchemy import select,text
from fastapi import HTTPException
from . import models as m
from .db import SessionLocal

ACTIVE={'queued','running'}
def key(course_id,concept_id):
    return hashlib.sha256((course_id+'\0'+concept_id).encode()).hexdigest()

def status(db,course_id,concept_id):
    from .main import cached_lesson
    lesson=cached_lesson(course_id,concept_id,db)['lesson']
    job=db.get(m.LessonGeneration,key(course_id,concept_id))
    return {'job_id':job.id if job else None,'status':'completed' if lesson else job.status if job else 'idle',
            'error':None if lesson else job.error if job else None,'lesson':lesson,
            'model_calls':0,'usage':{'input_tokens':0,'output_tokens':0},
            'generation_metrics':job.result if job else {}}

def request(db,course_id,concept_id):
    if not db.get(m.CourseConcept,(course_id,concept_id)):raise HTTPException(404,'知识点不属于本课程')
    current=status(db,course_id,concept_id)
    if current['lesson']:return current,False
    db.rollback();db.execute(text('BEGIN IMMEDIATE'));db.expire_all()
    job=db.get(m.LessonGeneration,key(course_id,concept_id))
    launch=not job or job.status not in ACTIVE
    if not job:
        job=m.LessonGeneration(id=key(course_id,concept_id),course_id=course_id,concept_id=concept_id,status='queued',result={});db.add(job)
    elif launch:job.status='queued';job.error=None
    db.commit()
    return status(db,course_id,concept_id),launch

def run(job_id):
    from .main import ensure_lesson
    from .locks import task_lock
    # Separate job lock prevents duplicate workers; lesson lock also covers legacy API.
    with task_lock('generation-job:'+job_id),SessionLocal() as db:
        job=db.get(m.LessonGeneration,job_id)
        if not job or job.status not in ACTIVE:return
        course_id,concept_id=job.course_id,job.concept_id
        job.status='running';db.commit()
        try:
            result=ensure_lesson(course_id,concept_id,db)
            lesson=result['lesson'];lesson_id=lesson.get('id') or lesson.get('lesson_id')
            turn=db.scalar(select(m.Turn).where(m.Turn.lesson_id==lesson_id,m.Turn.action=='teach'))
            metrics=turn.result if turn and turn.result else {}
            job=db.get(m.LessonGeneration,job_id);job.status='completed';job.error=None
            job.result={'model_calls':result.get('model_calls',0),'usage':metrics.get('usage',{}),'analysis':metrics.get('analysis',{}),'model':metrics.get('model'),'elapsed_seconds':metrics.get('elapsed_seconds'),'lesson_id':lesson_id}
            db.commit()
        except Exception as exc:
            db.rollback();job=db.get(m.LessonGeneration,job_id);job.status='failed'
            detail=exc.detail if isinstance(exc,HTTPException) else type(exc).__name__
            if isinstance(detail,dict):detail=detail.get('error','生成失败')
            code=str(detail)+' '+str(getattr(exc,'code',''))+' '+str(getattr(exc,'status_code',''))
            if 'insufficient_quota' in code:message='模型账号额度不足，请检查额度后重试'
            elif 'Authentication' in code or '401' in code:message='模型密钥或授权无效，请检查本地配置'
            elif 'Permission' in code or '403' in code:message='当前账号无权使用所选模型，请检查项目权限'
            elif '429' in code or 'RateLimit' in code:message='模型请求过于频繁，请稍后重试'
            elif 'Timeout' in code or 'Connection' in code:message='连接模型服务超时或网络中断，请稍后重试'
            elif '依据不足' in code:message='课件依据不足，请补充或修正资料后重试'
            else:message='生成未能完成（'+str(detail)+'）'
            job.error='讲义生成失败：'+message+'。可点击开始学习重试。'
            db.commit()

def launch(job_id):
    threading.Thread(target=run,args=(job_id,),daemon=True,name='lesson-'+job_id[:8]).start()
