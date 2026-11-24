import ReactMarkdown from 'react-markdown'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
// Recover literal bold markers when punctuation next to Chinese text prevented delimiter parsing.
function literalBold(){return (tree:any)=>{function visit(node:any){if(!node.children||['code','inlineCode','math','inlineMath'].includes(node.type))return;node.children=node.children.flatMap((child:any)=>{if(child.type!=='text')return [child];const parts=[];const pattern=/\*\*([^*\n]+)\*\*/g;let match,index=0;while((match=pattern.exec(child.value))){if(match.index>index)parts.push({type:'text',value:child.value.slice(index,match.index)});parts.push({type:'strong',children:[{type:'text',value:match[1]}]});index=pattern.lastIndex}if(index<child.value.length)parts.push({type:'text',value:child.value.slice(index)});return parts.length?parts:[child]});node.children.forEach(visit)}visit(tree)}}
export function cleanMarkdown(text:string){return text.replace(/\\([*#])/g,'$1').split('\\(').join('$').split('\\)').join('$').split('\\[').join('$$').split('\\]').join('$$').replace(/\$\$[\s\S]*?\$\$|\$[^$\n]*?\$/g,math=>math.includes('\\\\begin')?math.replace(/\\\\/g,'\\'):math)}
export default function Markdown({text}:{text:string}){return <ReactMarkdown remarkPlugins={[remarkMath,literalBold]} rehypePlugins={[rehypeKatex]}>{cleanMarkdown(text)}</ReactMarkdown>}
