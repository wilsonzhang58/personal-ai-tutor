"""Local exact/alias/cache lookup then cached E5 Top-3. Never calls a paid model."""
import hashlib,re,time,unicodedata
from datetime import datetime,timezone
from functools import lru_cache
from uuid import uuid4
from sqlalchemy import select
from . import models as m
POLICY='resolver-v1'

def category_for(name):
    if any(w in name for w in ('向量','矩阵','内积','点积','eigen','matrix','vector')):return '数学/线性代数'
    if any(w in name for w in ('概率','统计','probability','statistics')):return '数学/概率统计'
    if any(w in name for w in ('导数','积分','极限','连续','derivative','integral')):return '数学/微积分'
    if any(w in name for w in ('学习','神经','网络','learning','network')):return '人工智能/机器学习'
    return '待分类'
MATCHING_EXTRA_LLM_CALLS=0

def canonical(db,cid):
    seen=set()
    while cid not in seen:
        seen.add(cid);row=db.get(m.Concept,cid)
        target=(row.details or {}).get('redirect') if row else None
        if not target:return cid
        cid=target
    raise ValueError('Concept redirect cycle')

def normalize(value):
    value=unicodedata.normalize('NFKC',value).casefold().strip()
    return re.sub(r'\s+',' ',value).replace('（','(').replace('）',')')

def alias(db,cid,name,context='',source='confirmed'):
    key=(normalize(name),normalize(context),cid)
    existing=db.get(m.ConceptAlias,key)
    language='zh' if any('\u4e00'<=c<='\u9fff' for c in name) else 'en'
    if not existing:db.add(m.ConceptAlias(alias=key[0],context=key[1],concept_id=cid,source=source,language=language))
    elif existing.language=='auto':existing.language=language

def catalog(db):
    return [c for c in db.scalars(select(m.Concept)).all() if not (c.details or {}).get('redirect')]

@lru_cache(maxsize=512)
def embedded(text):
    from .retrieval import encode
    return encode([text],'passage: ')[0]

def candidates(db,name,definition,context):
    import torch
    rows=catalog(db)
    if not rows:return []
    from .retrieval import encode,top_k
    texts=[r.name+' '+r.scope+' '+(r.details or {}).get('category','') for r in rows]
    vectors=torch.stack([embedded(t) for t in texts])
    query=encode([name+' '+definition+' '+context],'query: ')[0]
    return [{'id':rows[i].id,'name':rows[i].name,'scope':rows[i].scope[:120],'score':score} for i,score in top_k(query,vectors,3)]

def resolve(db,name='',context='',definition='',concept_id=None,allow_embedding=True,create=True,prefetched=None):
    start=time.monotonic();context=normalize(context);name=normalize(name)
    def result(cid,path,status='same',options=None):return {'concept_id':cid,'path':path,'status':status,'candidates':options or [],'extra_llm_calls':0,'local_ms':round((time.monotonic()-start)*1000,2),'policy':POLICY}
    if concept_id:
        row=db.get(m.Concept,concept_id)
        if row:return result(canonical(db,row.id),'ID')
    key=hashlib.sha256((name+'|'+context+'|'+definition+'|'+POLICY).encode()).hexdigest()
    # Confirmed contextual aliases always take priority over provisional caches.
    matches=db.scalars(select(m.ConceptAlias).where(m.ConceptAlias.alias==name,m.ConceptAlias.context.in_([context,'']))).all()
    if definition:
        matches=[a for a in matches if a.source=='confirmed' or not db.get(m.Concept,a.concept_id).scope or normalize(db.get(m.Concept,a.concept_id).scope)==normalize(definition)]
    confirmed=[a for a in matches if a.source not in {'provisional','local_new'}]
    if confirmed:matches=confirmed
    exact_context=[a for a in matches if a.context==context]
    ids={a.concept_id for a in exact_context or matches}
    if len(ids)==1:return result(next(iter(ids)),'alias','uncertain' if all(a.source=='provisional' for a in exact_context or matches) else 'same')
    ambiguous_names={'函数','function','functions','内积','inner product','正则化','regularization','导数','derivative'}
    names=[r for r in catalog(db) if normalize(r.name)==name and (name not in ambiguous_names or ((r.details or {}).get('context')==context) or (definition and r.scope and normalize(r.scope)==normalize(definition))) and (not definition or not r.scope or normalize(r.scope)==normalize(definition)) and not (r.details or {}).get('redirect')]
    if len(names)==1:
        alias(db,names[0].id,name,context);return result(names[0].id,'alias')
    cached=db.get(m.MatchCache,key)
    if cached and db.get(m.Concept,cached.data['concept_id']):return {**cached.data,'path':'cache','local_ms':round((time.monotonic()-start)*1000,2)}
    options=[]
    if prefetched is not None:options=prefetched
    elif allow_embedding:
        try:options=candidates(db,name,definition,context)
        except (OSError,RuntimeError):options=[]
    if not create:return result(None,'local_embedding','uncertain',options)
    # Similarity only provides candidates, never silently merges different names/scopes.
    cid='provisional.'+hashlib.sha256((name+'|'+context+'|'+normalize(definition)).encode()).hexdigest()[:20]
    if not db.get(m.Concept,cid):
        db.add(m.Concept(id=cid,name=name or definition[:60],scope=definition,details={'category':category_for(name),'context':context,'provisional':bool(options),'candidates':options,'created_by':'local_resolver','created_at':datetime.now(timezone.utc).isoformat(),'version':1}));db.flush()
    alias(db,cid,name,context,'provisional' if options else 'local_new')
    data=result(cid,'local_embedding' if options else 'provisional','uncertain' if options else 'new',options)
    db.add(m.MatchCache(key=key,data=data));db.flush();return data

def seed_aliases(db):
    # Euclidean dot product is intentionally separate from general inner products.
    cid='linear_algebra.euclidean_dot_product'
    if not db.get(m.Concept,cid):db.add(m.Concept(id=cid,name='实欧氏空间点积',scope='实欧氏 R^n 标准数量积，非一般内积的全部范围',details={'category':'数学/线性代数','english_name':'Euclidean dot product'}));db.flush()
    for name in ('点积','dot product','数量积'):alias(db,cid,name,'real_euclidean')
    db.commit()

def resolve_batch(db,items,context=''):
    results=[resolve(db,concept_id=x.get('id'),name=x['name'],definition=x.get('definition',''),context=context,create=False,allow_embedding=False) for x in items]
    missing=[i for i,r in enumerate(results) if not r['concept_id']]
    options={i:[] for i in missing}
    rows=catalog(db)
    if missing and rows:
        import torch
        from .retrieval import encode,top_k
        texts=[r.name+' '+r.scope+' '+(r.details or {}).get('category','') for r in rows]
        vectors=torch.stack([embedded(t) for t in texts])
        queries=encode([items[i]['name']+' '+items[i].get('definition','')+' '+context for i in missing],'query: ')
        for i,q in zip(missing,queries):options[i]=[{'id':rows[n].id,'name':rows[n].name,'scope':rows[n].scope[:120],'score':score} for n,score in top_k(q,vectors,3)]
    for i in missing:results[i]=resolve(db,name=items[i]['name'],definition=items[i].get('definition',''),context=context,prefetched=options[i])
    return results
