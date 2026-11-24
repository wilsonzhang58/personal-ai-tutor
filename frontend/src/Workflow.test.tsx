// @vitest-environment jsdom
import {act} from 'react'
import {createRoot,Root} from 'react-dom/client'
import {afterEach,beforeEach,describe,it,expect,vi} from 'vitest'
import {App} from './main'
import Library from './Library'
import Materials from './Materials'
let root:Root,host:HTMLDivElement
const calls:{path:string,method:string,body:any}[]=[]
const course={id:'c1',title:'Course A',folder:'course-a',concepts:[{id:'a',name:'主题 A'},{id:'b',name:'主题 B'}]}
const lesson={id:'lesson-a',blocks:[{block_id:'block',kind:'main',markdown:'保存的讲义',citation_ids:['s']}],citations:[{id:'s',title:'lecture.pdf',page:2}]}
function mock(handler:(path:string,method:string,body:any)=>any){vi.stubGlobal('fetch',vi.fn(async(path,init)=>{const p=String(path),method=init?.method||'GET',body=init?.body?JSON.parse(init.body):null;calls.push({path:p,method,body});const data=await handler(p,method,body);return {ok:true,json:async()=>data}}))}
async function flush(){for(let i=0;i<4;i++)await act(async()=>{await new Promise(r=>setTimeout(r,0))})}
async function render(element:any){await act(async()=>root.render(element));await flush()}
async function click(text:string,selector='button'){const button=Array.from(host.querySelectorAll<HTMLElement>(selector)).find(x=>x.textContent===text);expect(button,`missing ${text}`).toBeTruthy();await act(async()=>button!.click());await flush()}
async function choose(label:string,value:string){const select=host.querySelector<HTMLSelectElement>(`select[aria-label="${label}"]`)!;expect(select).toBeTruthy();await act(async()=>{select.value=value;select.dispatchEvent(new Event('change',{bubbles:true}))});await flush()}
async function type(label:string,value:string){const input=host.querySelector<HTMLInputElement>(`input[placeholder="${label}"]`)!;await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(input,value);input.dispatchEvent(new Event('input',{bubbles:true}))});await flush()}
beforeEach(()=>{(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;localStorage.clear();calls.length=0;host=document.createElement('div');document.body.append(host);root=createRoot(host);HTMLElement.prototype.scrollTo=vi.fn();vi.stubGlobal('requestAnimationFrame',(cb:any)=>{cb();return 1})})
afterEach(async()=>{await act(async()=>root.unmount());host.remove();vi.unstubAllGlobals();vi.restoreAllMocks()})
describe('explicit study workflow',()=>{
 it('opens blank for zero calls, starts explicitly, recovers running job and opens cached lesson with three actions',async()=>{
  let status='idle',saved=false
  mock((p,m)=>{
   if(p==='/v1/courses')return [course]
   if(p==='/v1/knowledge')return {states:[]}
   if(p==='/health')return {demo_mode:false}
   if(p.endsWith('/resume'))return {progress:null}
   if(p.endsWith('/progress'))return {status:'saved'}
   if(p.endsWith('/generation')){if(m==='POST')status='running';return {status:p.includes('/b/')?'idle':status,lesson:saved&&p.includes('/a/')?lesson:null}}
   if(p.endsWith('/notes'))return [{id:'n',block_id:'block',text:'原有便签'}]
   throw Error('unexpected '+p)
  })
  await render(<App/>);await click('1 主题 A','aside button')
  expect(host.querySelector('h1')?.textContent).toBe('主题 A')
  expect(host.querySelector('.reading')?.textContent).toBe('')
  expect(host.querySelector('.actions')?.textContent).toBe('开始学习')
  expect(calls.filter(x=>x.method==='POST')).toHaveLength(0)
  await click('开始学习');expect(host.querySelector('.reading')?.textContent).toContain('正在生成讲义……')
  expect((host.querySelector('.actions button') as HTMLButtonElement).disabled).toBe(true)
  await click('2 主题 B','aside button');await click('1 主题 A','aside button')
  expect(host.querySelector('.reading')?.textContent).toContain('正在生成讲义……')
  expect(calls.filter(x=>x.path.endsWith('/generation')&&x.method==='POST')).toHaveLength(1)
  status='completed';saved=true;await click('1 主题 A','aside button')
  expect(Array.from(host.querySelectorAll('.actions button')).map(x=>x.textContent)).toEqual(['完成学习','可选练习','下一知识点 →'])
  expect(host.querySelector('.reading')?.textContent).toContain('原有便签')
  expect(host.querySelector('.toolbar')?.textContent).not.toContain(course.title)
  await click('下一知识点 →');expect(host.querySelector('.actions')?.textContent).toBe('开始学习')
  expect(calls.filter(x=>x.path.includes('/runs')||x.path.includes('/exercise')||x.path.endsWith('/sync'))).toHaveLength(0)
  expect(calls.filter(x=>x.path.endsWith('/generation')&&x.method==='POST')).toHaveLength(1)
 })
 it('keeps failure understandable and retries through the original start button',async()=>{
  let attempts=0
  mock((p,m)=>{
   if(p==='/v1/courses')return [course]
   if(p==='/v1/knowledge')return {states:[]}
   if(p==='/health')return {demo_mode:false}
   if(p.endsWith('/resume'))return {progress:null}
   if(p.endsWith('/progress'))return {}
   if(p.endsWith('/generation')){if(m==='POST')attempts++;return attempts>=2?{status:'completed',lesson}:{status:'failed',error:'讲义生成失败，可点击开始学习重试。',lesson:null}}
   if(p.endsWith('/notes'))return []
   throw Error(p)
  })
  await render(<App/>);await click('1 主题 A','aside button');expect(host.querySelector('.error')?.textContent).toContain('生成失败')
  await click('开始学习');await click('1 主题 A','aside button');expect(host.querySelector('.actions')?.textContent).toBe('开始学习')
  await click('开始学习');expect(host.querySelector('.reading')?.textContent).toContain('保存的讲义');expect(attempts).toBe(2)
 })
})
it('library applies all filters through server queries, clears invalid direction and keeps setup/merge on demand',async()=>{
 mock((p,m)=>{
  if(p==='/v1/library/taxonomy')return {'数学':['基础','线性代数','其他'],'人工智能':['基础','机器学习','其他']}
  if(p.startsWith('/v1/library?'))return {items:[{concept_id:'v',name:'向量',mastery_level:2,state_version:1,courses:['course-a','course-b']}],total:61}
  if(p==='/v1/background')return m==='POST'?{coverage:'已初始化'}:{}
  if(p==='/v1/library/merge-options')return [{concept_id:'v',name:'向量',mastery_level:2},{concept_id:'w',name:'重复向量',mastery_level:2}]
  if(p==='/v1/knowledge/events'||p==='/v1/library/merge')return {}
  throw Error(p)
 })
 await render(<Library onClose={()=>{}} onChanged={async()=>{}}/>);
 expect(host.textContent).not.toMatch(/变化依据|最近变化|复习 0|掌握度是可纠正/)
 expect(calls.some(x=>x.path==='/v1/background'||x.path.includes('merge-options'))).toBe(false)
 await choose('一级学科','数学');await choose('二级方向','线性代数');await choose('掌握程度筛选','2');await type('搜索概念、英文或别名','vector');await click('下一页')
 let query=new URL('http://local'+calls.filter(x=>x.path.startsWith('/v1/library?')).at(-1)!.path).searchParams
 expect(Object.fromEntries(query)).toMatchObject({subject:'数学',direction:'线性代数',mastery:'2',q:'vector',page:'2'})
 await choose('一级学科','人工智能');expect((host.querySelector('[aria-label="二级方向"]') as HTMLSelectElement).value).toBe('')
 await choose('向量掌握程度','3');expect(calls.find(x=>x.path==='/v1/knowledge/events')?.body.mastery_level).toBe(3)
 await click('合并重复概念');await choose('待合并概念','w');await choose('保留概念','v');await choose('合并后的掌握程度','2');await click('按所选等级合并');expect(calls.find(x=>x.path==='/v1/library/merge')?.body).toMatchObject({source_id:'w',target_id:'v',level:2})
 await click('初始化/修改学习背景');expect(calls.some(x=>x.path==='/v1/background')).toBe(true)
 await click('保存背景，仅补齐未有的起点假设');expect(calls.find(x=>x.path==='/v1/background'&&x.method==='POST')?.body.starting).toBe('高中水平')
 expect(calls.some(x=>x.path.includes('history')||x.path.includes('revoke'))).toBe(false)
})
it('material course/file/page selectors reset correctly; saving never syncs a course',async()=>{
 const files1=[{id:'f1',title:'one.pdf',version:1,pages:['a1','a2']},{id:'f2',title:'two.pdf',version:1,pages:['b1']}]
 mock((p,m)=>{
  if(p==='/v1/courses')return [course,{id:'c2',title:'Course B',folder:'course-b',concepts:[]}]
  if(p==='/v1/courses/c1/materials')return files1
  if(p==='/v1/courses/c2/materials')return [{id:'f3',title:'three.pdf',version:1,pages:['c1','c2','c3']}]
  if(p==='/v1/materials/f3'&&m==='PATCH')return {}
  throw Error(p)
 })
 await render(<Materials courseId="c1" onClose={()=>{}}/>);
 expect(host.textContent).toContain('course-a');expect(host.textContent).toContain('course-b')
 await choose('页码','1');expect((host.querySelector('textarea') as HTMLTextAreaElement).value).toBe('a2')
 await choose('PDF 文件','f2');expect(host.querySelector('[aria-label="页码"]')?.children.length).toBe(1);expect((host.querySelector('textarea') as HTMLTextAreaElement).value).toBe('b1')
 await choose('课程文件夹','c2');expect((host.querySelector('[aria-label="PDF 文件"]') as HTMLSelectElement).value).toBe('f3');expect(host.querySelector('[aria-label="页码"]')?.children.length).toBe(3);expect((host.querySelector('textarea') as HTMLTextAreaElement).value).toBe('c1')
 const text=host.querySelector('textarea')!;await act(async()=>{Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value')!.set!.call(text,'corrected');text.dispatchEvent(new Event('input',{bubbles:true}))});await flush();await click('保存修正')
 expect(calls.find(x=>x.method==='PATCH')?.body).toMatchObject({pages:['corrected','c2','c3'],expected_version:1})
 expect(calls.some(x=>x.path.endsWith('/sync')||x.path.includes('/generation'))).toBe(false)
})
it('restores saved lesson, note and offset without generation, completion or a synthetic scroll save',async()=>{
 mock((p)=>{
  if(p==='/v1/courses')return [course]
  if(p==='/v1/knowledge')return {states:[]}
  if(p==='/health')return {demo_mode:false}
  if(p.endsWith('/resume'))return {progress:{concept_id:'a',offset:320,lesson_id:'lesson-a'}}
  if(p.endsWith('/generation'))return {status:'completed',lesson}
  if(p.endsWith('/notes'))return [{id:'n',block_id:'block',text:'保存便签'}]
  throw Error(p)
 })
 await render(<App/>);expect(HTMLElement.prototype.scrollTo).toHaveBeenCalledWith({top:320,behavior:'instant'})
 await act(async()=>{host.querySelector('.reading')!.dispatchEvent(new Event('scroll',{bubbles:true}))});await flush()
 expect(host.querySelector('.reading')?.textContent).toContain('保存便签')
 expect(calls.some(x=>x.method!=='GET')).toBe(false)
})
