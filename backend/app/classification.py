"""Local display taxonomy. Never changes concept identity or learner state."""
from sqlalchemy import select
from . import models as m

START = {'数学': ['基础', '线性代数', '微积分', '概率论', '统计', '最优化', '其他'],
         '人工智能': ['基础', '机器学习', '深度学习', '其他']}
PREFIX = {'linear_algebra': ('数学','线性代数'), 'optimization': ('数学','最优化'),
          'multivariable_calculus': ('数学','微积分'), 'calculus': ('数学','微积分'),
          'probability': ('数学','概率论'), 'statistics': ('数学','统计'),
          'machine_learning': ('人工智能','机器学习'), 'deep_learning': ('人工智能','深度学习')}

def classify(c):
    d=c.details or {}
    if d.get('subject') and d.get('direction'):return d['subject'],d['direction']
    category=d.get('category','')
    if category in {'数学/函数','数学/代数','数学/初等代数'}:return '数学','基础'
    if category=='数学/概率统计':
        name=(c.name+' '+d.get('english_name','')).lower()
        return '数学','统计' if any(x in name for x in ('统计','statistics')) else '概率论' if any(x in name for x in ('概率','probability')) else '其他'
    if '/' in category:
        subject,direction=category.split('/',1)
        if subject and subject!='待分类':return subject,direction or '其他'
    prefix=c.id.split('.')[0]
    if prefix in PREFIX:return PREFIX[prefix]
    name=(c.name+' '+d.get('english_name','')).lower()
    rules=[('人工智能','深度学习',('神经网络','深度学习','neural network','deep learning','反向传播')),
           ('人工智能','机器学习',('监督学习','机器学习','回归模型','分类器','machine learning','supervised learning')),
           ('数学','线性代数',('矩阵','行列式','特征值','向量空间','点积','内积','matrix','eigenvalue')),
           ('数学','微积分',('微分','积分','导数','偏导','极限','连续性','derivative','integral','calculus')),
           ('数学','概率论',('概率','随机变量','条件期望','probability','random variable')),
           ('数学','统计',('统计','假设检验','置信区间','statistics')),
           ('数学','最优化',('最优化','凸优化','梯度下降','optimization'))]
    for subject,direction,terms in rules:
        if any(x in name for x in terms):return subject,direction
    if prefix=='math' or category in {'数学','基础数学'}:return '数学','基础'
    if category in START:return category,'其他'
    # Unknown scope stays explicitly unclassified; never infer from course number.
    return '未分类','其他'

def ensure(db):
    changed=False
    for c in db.scalars(select(m.Concept)).all():
        if (c.details or {}).get('subject') and (c.details or {}).get('direction'):continue
        subject,direction=classify(c)
        c.details={**(c.details or {}),'subject':subject,'direction':direction}
        changed=True
    if changed:db.commit()

def taxonomy(db):
    ensure(db)
    result={k:list(v) for k,v in START.items()}
    for c in db.scalars(select(m.Concept)).all():
        if (c.details or {}).get('redirect'):continue
        subject,direction=classify(c)
        options=result.setdefault(subject,['基础','其他'])
        if direction not in options:options.insert(len(options)-1,direction)
    return result
