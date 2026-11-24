import json
import os
from functools import lru_cache
from dotenv import dotenv_values
from .config import ENV_PATH, KEY_IN_PROCESS_BEFORE_DOTENV
from pydantic import BaseModel, Field
from typing import Literal

class BlockOutput(BaseModel):
    kind: str
    markdown: str
    citation_ids: list[str]
    concept_ids: list[str] = Field(default_factory=list)

class QuestionAttribution(BaseModel):
    primary_concept: str | None
    name: str
    intent: str
    clear_basic: bool
    certain: bool

class MatchDecision(BaseModel):
    query:Literal['x0','x1','x2']
    candidate:Literal['c0','c1','c2'] | None
    relation:Literal['same','related','different','uncertain']

class ModelOutput(BaseModel):
    blocks: list[BlockOutput]
    attribution: QuestionAttribution | None = None
    thread_summary: str | None = None
    matches: list[MatchDecision] = Field(default_factory=list,max_length=3)

def key_status():
    value = os.getenv('OPENAI_API_KEY', '').strip()
    file_value = (dotenv_values(ENV_PATH).get('OPENAI_API_KEY') or '').strip() if ENV_PATH.exists() else ''
    placeholder = bool(value and (value.lower() in {'your_api_key', 'your-key-here', 'placeholder', 'changeme'} or '...' in value or '*' in value or '•' in value or '在本地' in value or '实际密钥' in value))
    source = 'process_environment' if KEY_IN_PROCESS_BEFORE_DOTENV else 'backend/.env' if file_value else 'missing'
    return {'present': bool(value), 'placeholder': placeholder, 'source': source}

@lru_cache(maxsize=1)
def client():
    from openai import OpenAI
    kwargs = {}
    if os.getenv('OPENAI_BASE_URL'):
        kwargs['base_url'] = os.environ['OPENAI_BASE_URL']
    return OpenAI(api_key=os.environ['OPENAI_API_KEY'], timeout=90, max_retries=1, **kwargs)

def demo_requested():
    return os.getenv('TUTOR_DEMO_MODE', '1')=='1'

def configured():
    status = key_status()
    return status['present'] and not status['placeholder'] and not demo_requested()

def model_name():
    return os.getenv('OPENAI_MODEL', 'gpt-6.2-sol')

TOOL_SCHEMAS = [
    {'type':'function','name':'search_course_materials','description':'Search permitted course material for relevant excerpts','parameters':{'type':'object','properties':{'query':{'type':'string'}},'required':['query'],'additionalProperties':False},'strict':True},
    {'type':'function','name':'read_source_chunk','description':'Read one already retrieved source chunk','parameters':{'type':'object','properties':{'chunk_id':{'type':'string'}},'required':['chunk_id'],'additionalProperties':False},'strict':True},
]

def generate_with_tools(prompt, context, tool_handler):
    first = client().responses.create(model=model_name(), instructions=prompt + ' First search the course materials using a tool.', input=context, tools=TOOL_SCHEMAS, tool_choice={'type':'function','name':'search_course_materials'})
    if first.status != 'completed':
        raise ValueError(f'Initial model response status: {first.status}')
    calls = [item for item in first.output if item.type == 'function_call']
    if not calls:
        raise ValueError('Model did not call the required search tool')
    if len(calls) > 3:
        raise ValueError('Model exceeded the three-tool-call limit')
    outputs, trace, tool_sources = [], [], []
    for item in calls:
        args = json.loads(item.arguments)
        result = tool_handler(item.name, args)
        if item.name == 'search_course_materials' and isinstance(result, list):
            tool_sources.extend(result)
        outputs.append({'type':'function_call_output','call_id':item.call_id,'output':json.dumps(result,ensure_ascii=False)})
        trace.append({'tool':item.name,'call_id':item.call_id,'output_call_id':item.call_id,'arguments':args,'result_ids':[x['id'] for x in result] if isinstance(result,list) else [result.get('id')]})
    final = client().responses.parse(model=model_name(), previous_response_id=first.id, instructions=prompt + ' Return structured teaching blocks. Use the tool response for factual claims.', input=outputs, text_format=ModelOutput)
    if final.status != 'completed' or final.output_parsed is None:
        raise ValueError(f'Final model response unavailable: {final.status}')
    def total(field):
        values=[getattr(r.usage,field,None) if r.usage else None for r in (first,final)]
        return sum(values) if all(v is not None for v in values) else None
    cached=[getattr(getattr(r.usage,'input_tokens_details',None),'cached_tokens',None) if r.usage else None for r in (first,final)]
    usage={'input_tokens':total('input_tokens'),'output_tokens':total('output_tokens'),'cached_tokens':sum(cached) if all(v is not None for v in cached) else None}
    return final.output_parsed, usage, trace, tool_sources
