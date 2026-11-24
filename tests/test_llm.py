from types import SimpleNamespace
from backend.app import llm


def test_responses_tool_output_and_structured_parse(monkeypatch):
    calls = []
    class Responses:
        def create(self, **kwargs):
            calls.append(('create', kwargs))
            return SimpleNamespace(status='completed', id='response-1', output=[SimpleNamespace(type='function_call', name='search_course_materials', arguments='{"query":"主元行"}', call_id='call-1')], usage=SimpleNamespace(input_tokens=10, output_tokens=3))
        def parse(self, **kwargs):
            calls.append(('parse', kwargs))
            return SimpleNamespace(status='completed', output_parsed=llm.ModelOutput(blocks=[llm.BlockOutput(kind='main', markdown='解释', citation_ids=['chunk-1'])], pending_profile_updates=[]), usage=SimpleNamespace(input_tokens=12, output_tokens=5))
    monkeypatch.setattr(llm, 'client', lambda: SimpleNamespace(responses=Responses()))
    output, usage, trace, sources = llm.generate_with_tools('Teach', '{}', lambda name, args: [{'id':'chunk-1','text':'原文'}])
    assert output.blocks[0].citation_ids == ['chunk-1']
    assert usage == {'input_tokens':22, 'output_tokens':8, 'cached_tokens':None}
    assert trace[0]['tool'] == 'search_course_materials'
    assert sources[0]['id'] == 'chunk-1'
    assert calls[0][1]['tool_choice']['name'] == 'search_course_materials'
    assert calls[1][1]['previous_response_id'] == 'response-1'
    assert calls[1][1]['input'][0]['call_id'] == 'call-1'
    assert calls[1][1]['text_format'] is llm.ModelOutput
