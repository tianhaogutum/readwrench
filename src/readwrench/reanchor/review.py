"""A self-contained, network-free review UI. Opening it requires no server."""

from __future__ import annotations

import json
from pathlib import Path

from .models import Proposal

_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self' file: data:; style-src 'unsafe-inline'; script-src 'nonce-pdf-reanchor-local'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
<title>readwrench reanchor · Review proposals</title>
<style>
:root{color-scheme:light;--ink:#182e34;--muted:#506970;--line:#cbd9d8;--accent:#166456;--paper:#f6f8f6;--warning:#825510}
*{box-sizing:border-box}body{margin:0;font:16px/1.5 system-ui,sans-serif;color:var(--ink);background:var(--paper)}
header{padding:28px max(24px,calc((100vw - 1240px)/2));background:#183b38;color:white}header h1{font-size:29px;margin:0 0 8px}header p{margin:5px 0;max-width:1000px}header small{color:#c7ddda}
main{max-width:1290px;margin:auto;padding:24px}.toolbar{position:sticky;top:0;z-index:2;border:1px solid var(--line);background:#fff;padding:14px;box-shadow:0 5px 16px #183b3810;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
input[type=search],select,button,.file-label{font:inherit;padding:9px 12px;border:1px solid #8ca4a4;border-radius:5px;background:white;color:var(--ink)}button{cursor:pointer}button.primary{background:var(--accent);color:white;border-color:var(--accent)}button:disabled{background:#758c87;cursor:not-allowed}input[type=radio],input[type=checkbox]{accent-color:var(--accent);width:18px;height:18px;flex-shrink:0}
.file-label{cursor:pointer}.file-label input{width:1px;height:1px;position:absolute;opacity:0}.stats{font-weight:600;color:var(--accent)}.confirm{display:flex;align-items:center;gap:9px;flex-basis:100%;font-size:14px}.notice{padding:16px 0;color:var(--muted)}
article{background:white;border:1px solid var(--line);border-radius:8px;margin:20px 0;overflow:hidden}article>h2{font-size:20px;margin:0;padding:16px 20px;background:#eef4f1}.card-body{padding:20px}.columns{display:grid;grid-template-columns:minmax(260px,.9fr) minmax(340px,1.3fr);gap:24px}.source{border-right:1px solid var(--line);padding-right:24px}.kicker{font-size:12px;letter-spacing:.09em;text-transform:uppercase;font-weight:700;color:var(--muted)}
blockquote{margin:12px 0;padding:12px 15px;border-left:3px solid var(--accent);background:#f0f5f2;overflow-wrap:anywhere}.comment{white-space:pre-wrap;overflow-wrap:anywhere}.muted{color:var(--muted);font-size:14px}.warning{color:var(--warning);background:#fff7e7;padding:8px 12px;margin:8px 0;font-size:14px}
img{display:block;max-width:100%;height:auto;border:1px solid var(--line);border-radius:4px;margin:10px 0}details>summary{cursor:pointer;color:var(--accent);padding:6px 0}.candidate{border:1px solid var(--line);border-radius:6px;padding:13px;margin:12px 0}.candidate:has(input:checked){border:2px solid var(--accent);padding:12px;background:#f6fbf7}.option{display:flex;gap:9px;align-items:flex-start;cursor:pointer}.candidate h3{font-size:16px;margin:0;display:inline}.snippet{font-size:14px;overflow-wrap:anywhere}mark{background:#c4ecf4;color:var(--ink)}table{font-size:12px;width:100%;border-collapse:collapse;margin:10px 0}th,td{text-align:left;border-bottom:1px solid var(--line);padding:4px 7px}td{font-variant-numeric:tabular-nums}.decision-note{width:100%;font:inherit;min-height:48px;margin-top:12px;padding:8px;border:1px solid var(--line);border-radius:4px}footer{padding:10px 0 40px;color:var(--muted);font-size:14px}#message{flex-basis:100%;color:var(--warning);font-size:14px}#empty{padding:25px;color:var(--muted)}
@media(max-width:760px){main{padding:12px}.columns{grid-template-columns:1fr}.source{border-right:0;border-bottom:1px solid var(--line);padding:0 0 16px}.toolbar{position:static}.card-body{padding:14px}header{padding:20px}.candidate{padding:10px}}
</style></head><body>
<header><small>PDFANNO EXTENSION · OFFLINE REVIEW</small><h1>Keep the comment. Confirm its new home.</h1><p id="files"></p><p>Compare the old annotation with proposed locations. Every choice starts unresolved. A score ranks candidates; it does not approve a migration.</p><small>Open this file directly. No server, network requests, accounts, or automatic approval. Export decisions to save your progress; import them to resume.</small></header>
<main><div class="toolbar"><label>Find <input id="search" type="search" placeholder="Quote, comment, or author"></label><label>Show <select id="filter"><option value="all">All comments</option><option value="unresolved">Unresolved</option><option value="accept">Accepted</option><option value="reject">Rejected</option><option value="no-candidates">No candidates</option></select></label><span id="stats" class="stats" aria-live="polite"></span><label class="file-label">Import decisions<input id="import" type="file" accept=".json,application/json"></label><button id="export">Save progress</button><label class="confirm"><input id="confirmed" type="checkbox">I reviewed the selected locations and authorize only the accepted choices to be migrated.</label><button id="export-confirmed" class="primary" disabled>Export confirmed decisions</button><span id="message" role="status"></span></div>
<div class="notice">Cyan marks identify proposed target text. Old screenshots retain original annotations. Page numbers here start at 1; JSON page indexes start at 0. Unsupported, rejected and unresolved comments remain in the unmatched package, including the full original PDF.</div><details id="bundle-notes"><summary>Bundle notes and limitations</summary><div id="warnings"></div></details><div id="cards"></div><p id="empty" hidden>No comments match this filter.</p><footer>Apply the exported JSON with <code>readwrench reanchor apply BUNDLE decisions.json --out RESULT</code>. The CLI checks PDF bytes, proposal fingerprint, image hashes, candidate geometry and confirmation before writing a new copy.</footer></main>
<script type="application/json" id="proposal">__PROPOSAL_JSON__</script>
<script nonce="pdf-reanchor-local">
'use strict';
const proposal=JSON.parse(document.getElementById('proposal').textContent);
const choices=new Map(proposal.annotations.map(a=>[a.id,{annotation_id:a.id,action:'unresolved',candidate_id:null,note:''}]));
const cards=new Map();
const $=id=>document.getElementById(id);
function el(tag,text,cls){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;}
function picture(asset,alt){if(!asset)return el('p','Image unavailable','warning');const img=el('img');img.src=asset.path;img.alt=alt;img.loading='lazy';return img;}
function basename(path){return path.split(/[\\/]/).pop();}
$('files').textContent=basename(proposal.old_pdf.path)+' → '+basename(proposal.new_pdf.path);
for(const warning of proposal.warnings){$('warnings').append(el('p',warning,'warning'));}
$('bundle-notes').open=proposal.warnings.some(w=>w.startsWith('No embedded')||w.startsWith('The new PDF has no'));
function changed(){ $('confirmed').checked=false; $('export-confirmed').disabled=true; $('message').textContent='Choices changed. Save progress or reconfirm before applying.';refresh();}
function radio(annotation,action,candidate){const label=el('label',undefined,'option');const input=el('input');input.type='radio';input.name=annotation.id;input.value=candidate?candidate.id:action;input.checked=action==='unresolved';input.addEventListener('change',()=>{const choice=choices.get(annotation.id);choice.action=action;choice.candidate_id=candidate?candidate.id:null;changed();});label.append(input);return label;}
function snippet(candidate){const node=el('p',undefined,'snippet');node.append(document.createTextNode(candidate.context_before.slice(-160)),el('mark',candidate.matched_text),document.createTextNode(candidate.context_after.slice(0,160)));return node;}
for(const [i,a] of proposal.annotations.entries()){
 const article=el('article');article.dataset.annotation=a.id;article.append(el('h2',(i+1)+'. '+a.kind+' · old page '+(a.page_index+1)));
 const body=el('div',undefined,'card-body');const columns=el('div',undefined,'columns');const source=el('section',undefined,'source');source.append(el('div','Original annotation','kicker'));
 source.append(el('p',(a.info.title||'Unknown author')+(a.info.subject?' · '+a.info.subject:''),'muted'));
 source.append(el('p',a.info.content||((a.info.rich_content||a.info.rich_content_reference)?'(Rich-text-only comment is retained in the original PDF.)':'(No attached comment)'),'comment'));
 source.append(el('div','Anchor: '+a.anchor_basis,'kicker'));source.append(el('blockquote',a.selected_text||'(No extractable anchor text)'));
 if(a.context_before||a.context_after){const context=el('details');context.append(el('summary','Surrounding source text'),el('p',a.context_before+' ['+a.selected_text+'] '+a.context_after,'snippet'));source.append(context);}
 source.append(picture(a.image,'Old annotation on page '+(a.page_index+1)));const full=el('details');full.append(el('summary','Full old page'),picture(a.page_image,'Full original page '+(a.page_index+1)));source.append(full);
 const target=el('section');target.append(el('div','Proposed anchors · choose one or leave unmatched','kicker'));
 for(const warning of a.warnings)target.append(el('p',warning,'warning'));
 const unresolved=radio(a,'unresolved');unresolved.append(el('span','Unresolved — keep for later'));target.append(unresolved);
 const reject=radio(a,'reject');reject.append(el('span','Reject candidates — preserve unmatched'));target.append(reject);
 if(!a.candidates.length)target.append(el('p','No usable candidate. The original comment will be retained in the unmatched package.','warning'));
 for(const [j,c] of a.candidates.entries()){
  const box=el('div',undefined,'candidate');const accept=radio(a,'accept',c);accept.append(el('h3','Accept candidate '+(j+1)+' · new page '+(c.page_index+1)+' · score '+c.score.toFixed(3)));box.append(accept,snippet(c));
  box.append(el('p',c.evidence.method+(a.anchor_basis==='nearby text'?' · nearby-text anchor':''),'muted'));
  const table=el('table');const head=el('tr');for(const name of ['Text','Context','Layout','Page proximity','Length'])head.append(el('th',name));table.append(head);const values=el('tr');for(const key of ['text','context','layout','page_proximity','length'])values.append(el('td',c.evidence[key].toFixed(3)));table.append(values);box.append(table,picture(c.image,'Proposed target text on new page '+(c.page_index+1)));target.append(box);
 }
 const note=el('textarea',undefined,'decision-note');note.placeholder='Optional review note (saved with decisions)';note.setAttribute('aria-label','Review note for annotation '+(i+1));note.addEventListener('input',()=>{choices.get(a.id).note=note.value;changed();});target.append(note);columns.append(source,target);body.append(columns);article.append(body);$('cards').append(article);cards.set(a.id,{article,note,search:(a.selected_text+' '+Object.values(a.info).join(' ')).toLocaleLowerCase()});
}
function refresh(){let accepted=0,rejected=0,shown=0;const term=$('search').value.toLocaleLowerCase();const filter=$('filter').value;for(const a of proposal.annotations){const choice=choices.get(a.id);if(choice.action==='accept')accepted++;if(choice.action==='reject')rejected++;const card=cards.get(a.id);const visible=card.search.includes(term)&&(filter==='all'||choice.action===filter||(filter==='no-candidates'&&!a.candidates.length));card.article.hidden=!visible;if(visible)shown++;}$('stats').textContent=accepted+' accepted · '+rejected+' rejected · '+(choices.size-accepted-rejected)+' unresolved';$('empty').hidden=shown>0;}
$('search').addEventListener('input',refresh);$('filter').addEventListener('change',refresh);
$('confirmed').addEventListener('change',()=>{$('export-confirmed').disabled=!$('confirmed').checked;});
function download(confirmed){const data={schema_version:1,proposal_id:proposal.proposal_id,confirmed,choices:[...choices.values()]};const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)+'\n'],{type:'application/json'}));const link=el('a');link.href=url;link.download=confirmed?'decisions.json':'decisions.progress.json';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);$('message').textContent=confirmed?'Confirmed decisions exported. Run the apply command to create the migration copy.':'Progress saved. This unconfirmed file cannot be applied until reviewed and confirmed.';}
$('export').addEventListener('click',()=>download(false));$('export-confirmed').addEventListener('click',()=>{if($('confirmed').checked)download(true);});
$('import').addEventListener('change',async event=>{try{const file=event.target.files[0];if(!file)return;$('confirmed').checked=false;$('export-confirmed').disabled=true;if(file.size>64*1024*1024)throw Error('Decision file exceeds 64 MiB');const data=JSON.parse(await file.text());if(data.schema_version!==1||data.proposal_id!==proposal.proposal_id||!Array.isArray(data.choices))throw Error('This file belongs to a different proposal or schema');const pending=new Map();for(const choice of data.choices){const a=proposal.annotations.find(a=>a.id===choice.annotation_id);if(!a||pending.has(a.id)||!['accept','reject','unresolved'].includes(choice.action)||typeof choice.note!=='string')throw Error('Unknown or duplicate decision');if(Object.keys(choice).some(k=>!['annotation_id','action','candidate_id','note'].includes(k)))throw Error('Unknown decision field');if(choice.action==='accept'&&!a.candidates.some(c=>c.id===choice.candidate_id))throw Error('Unknown candidate');if(choice.action!=='accept'&&choice.candidate_id!==null)throw Error('Unexpected candidate');pending.set(a.id,choice);}for(const a of proposal.annotations){const choice=pending.get(a.id)||{annotation_id:a.id,action:'unresolved',candidate_id:null,note:''};choices.set(a.id,choice);const card=cards.get(a.id);for(const input of card.article.querySelectorAll('input[type=radio]'))input.checked=input.value===(choice.candidate_id||choice.action);card.note.value=choice.note;}changed();$('message').textContent='Choices imported. Confirmation must be given again on this page.';}catch(error){$('message').textContent='Import failed: '+error.message;}finally{event.target.value='';}});
refresh();
</script></body></html>"""


def write_review(path: Path, proposal: Proposal) -> None:
    # Escape HTML parser delimiters even though data is in a non-executable script.
    data = (
        json.dumps(proposal.model_dump(), ensure_ascii=True)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    path.write_text(_HTML.replace("__PROPOSAL_JSON__", data), encoding="utf-8")
