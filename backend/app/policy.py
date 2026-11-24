"""Pure deterministic v2 policy. Dates are learner-local; no model writes grades."""
import os
from datetime import datetime
from zoneinfo import ZoneInfo
VERSION='mastery-v2'
REVIEW_THRESHOLD=3
DOUBT_THRESHOLD=3
ERROR_THRESHOLD=2
LABELS=['未知','知道但不熟悉','入门','熟悉','精通','完全掌握']

def now():return datetime.now(ZoneInfo(os.getenv('TUTOR_TIMEZONE','Asia/Hong_Kong')))
def local_date(moment):return moment.astimezone(ZoneInfo(os.getenv('TUTOR_TIMEZONE','Asia/Hong_Kong'))).date().isoformat()
def empty():return {'first_completed':False,'review_count':0,'doubt_count':0,'error_count':0,'seen':[],'source':'unknown'}

def reduce(level,data,kind,p,day):
    d={**empty(),**data};d['seen']=list(d['seen']);before=level;reason='已记录'
    identity=kind+':'+str(p.get('identity',''))
    if p.get('identity') and identity in d['seen']:return level,d,'该学习轮次或证据已记录'
    if p.get('identity'):d['seen'].append(identity)
    def change(delta,source):
        nonlocal level
        level=min(5,max(0 if level==0 else 1,level+delta))
        if level!=before or p.get('source')=='manual_merge':
            for key in ('review_count','doubt_count','error_count'):d[key]=0
            d['source']=source
            if delta>0:d['last_up']=day
            else:d['last_down']=day
    if kind=='manual':
        level=p['level'];d['first_completed']=d['first_completed'] or p.get('first_completed',False);d['source']=p.get('source','manual_self_report')
        if level!=before or p.get('source')=='manual_merge':
            for key in ('review_count','doubt_count','error_count'):d[key]=0
        if p.get('source')=='manual_merge':
            for key in ('first_date','last_up','last_down','review_date'):
                if p.get(key):d[key]=p[key]
        reason='合并重复概念，按用户选择保留等级' if p.get('source')=='manual_merge' else '用户手动纠正'
    elif kind=='complete':
        if not d['first_completed']:
            d['first_completed']=True;d['first_date']=day
            if level<5 and d.get('last_up')!=day:change(1,'reading_estimate');reason='全局首次完成，升一级'
            else:reason='首次完成已记录，今日不重复升级'
        elif day in {d.get('first_date'),d.get('review_date'),d.get('last_up')} or level==5:reason='已记录，今日不重复计数' if level<5 else '复习已记录，已达最高级'
        else:
            d['review_date']=day;d['review_count']+=1;reason=f"有效复习 {d['review_count']}/3"
            if d['review_count']>=REVIEW_THRESHOLD:change(1,'reading_estimate');reason='三个不同日期有效复习，升一级'
    elif kind=='question':
        valid=p.get('clear_basic') and not p.get('is_target') and p.get('resolved') and p.get('intent')=='basic_difficulty'
        if valid and d.get('last_down')!=day:
            d['doubt_count']+=1;reason='基础疑惑 '+str(d['doubt_count'])+'/3'
            if d['doubt_count']>=DOUBT_THRESHOLD:
                d['doubt_count']=0;change(-1,'question_evidence');reason='三个独立基础疑惑，降一级' if level<before else '疑惑已结算，当前等级不再下降'
        else:reason='只记录提问，不计降级信用'
    elif kind=='practice':
        valid=p.get('reliable') and not p.get('assisted')
        if p.get('correct') and valid and p.get('difficulty',0)>=before:
            if level<5 and d.get('last_up')!=day:change(1,'practice_evidence');reason='独立完成适当难度练习，升一级'
            else:d['doubt_count']=0;d['error_count']=0;reason='正向证据确认，今日不重复升级'
        elif p.get('correct') is False and valid and p.get('basic_error') and p.get('difficulty',99)<=max(1,before) and d.get('last_down')!=day:
            d['error_count']+=1;reason='有效练习错误 '+str(d['error_count'])+'/2'
            if d['error_count']>=ERROR_THRESHOLD:
                d['error_count']=0;change(-1,'practice_evidence');reason='两道独立可靠错误，降一级' if level<before else '错误已结算，当前等级不再下降'
        else:reason='练习证据已保存，本次不调整等级'
    return level,d,reason
