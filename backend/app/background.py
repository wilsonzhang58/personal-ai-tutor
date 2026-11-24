import json
from pathlib import Path
from sqlalchemy import select
from . import models as m
from .resolver import resolve,alias
from .mastery import settle

SEED=json.loads((Path(__file__).resolve().parents[2]/'data/seeds/background_v1.json').read_text())

def initialize(db,operation_id,field,directions,starting):
    cached=db.get(m.AnalysisCache,'background:'+operation_id)
    if cached:return cached.data['result']
    profile=db.get(m.Profile,'local_user');profile.settings={'field':field,'directions':directions,'starting':starting,'seed_version':SEED['version']};db.commit()
    created=[]
    if starting not in {'高中水平','高中毕业基础'} or not any(word in (field+' '+' '.join(directions)).lower() for word in ('人工智能','ai','数学','概率','统计','math','probability','statistics')):return {'created':[],'coverage':'已保存背景；目前只有香港高中数学保守子集，其他起点尚未完整覆盖'}
    for cid,name,en,category,definition in SEED['concepts']:
        if not db.get(m.Concept,cid):db.add(m.Concept(id=cid,name=name,scope=definition,details={'english_name':en,'category':category,'source_url':SEED['source_url'],'seed_version':SEED['version']}));db.flush()
        alias(db,cid,name,'mathematics');alias(db,cid,en,'mathematics');db.commit()
        if not db.get(m.LearnerState,('local_user',cid)):
            settle(db,'background:'+cid,cid,'manual',{'level':2,'source':'background_assumption'})
            created.append(cid)
    result={'created':created,'coverage':SEED['scope'],'source_url':SEED['source_url']}
    db.add(m.AnalysisCache(key='background:'+operation_id,data={'result':result}));db.commit()
    return result
