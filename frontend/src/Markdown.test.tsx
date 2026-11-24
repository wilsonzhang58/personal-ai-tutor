import {describe,it,expect} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import Markdown from './Markdown'
describe('readable lessons',()=>{it('renders escaped bold and punctuation next to Chinese text',()=>{const html=renderToStaticMarkup(<Markdown text={'\\*\\*补充练习：\\*\\*请计算 $x^2$。'}/>);expect(html).toContain('<strong>补充练习：</strong>');expect(html).toContain('katex');expect(html).not.toContain('**')});it('supports legacy math delimiters',()=>{expect(renderToStaticMarkup(<Markdown text={'例子 \\(x^2\\)'}/>)).toContain('katex')})})
it('normalizes a double-escaped matrix from model JSON',()=>{const html=renderToStaticMarkup(<Markdown text={String.raw`$A=\\begin{pmatrix}2&5&8\\1&4&7\\end{pmatrix}$`}/>);expect(html).not.toContain('katex-error');expect(html).toContain('mtable')})
