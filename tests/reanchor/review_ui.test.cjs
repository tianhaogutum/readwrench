// DOM tests for the offline review page (readwrench reanchor propose → review.html).
// jsdom is a DOM emulator, not a browser. No resource loading, server, or network.
'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM,VirtualConsole}=require('jsdom');

// test_review_ui.py builds both bundles in a temporary directory and passes their paths.
const bundle=process.env.REANCHOR_REVIEW_BUNDLE;
const emptyBundle=process.env.REANCHOR_REVIEW_EMPTY;
if(!bundle||!emptyBundle)throw Error('Run through tests/reanchor/test_review_ui.py');
const html=fs.readFileSync(path.join(bundle,'review.html'),'utf8');
const proposal=JSON.parse(fs.readFileSync(path.join(bundle,'proposal.json'),'utf8'));

function create(sourceHtml=html){
 const downloads=[];const errors=[];
 const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 const dom=new JSDOM(sourceHtml,{url:'file://'+path.join(bundle,'review.html'),runScripts:'dangerously',virtualConsole:vc,beforeParse(window){
  window.Blob=Blob;
  window.URL.createObjectURL=blob=>{downloads.push({blob});return 'blob:local-test';};
  window.URL.revokeObjectURL=()=>{};
  window.HTMLAnchorElement.prototype.click=function(){downloads[downloads.length-1].filename=this.download;};
 }});
 return {dom,window:dom.window,document:dom.window.document,downloads,errors};
}
function change(window,node){node.dispatchEvent(new window.Event('change',{bubbles:true}));}

test('all choices start unresolved and accepted export is gated',async()=>{
 const ui=create();const {document:d,window:w}=ui;
 assert.equal(ui.errors.length,0,ui.errors.join('\n'));
 assert.match(d.getElementById('stats').textContent,/0 accepted/);
 assert.equal(d.querySelectorAll('input[type=radio]:checked').length,proposal.annotations.length);
 assert.equal(d.getElementById('confirmed').checked,false);
 assert.equal(d.getElementById('export-confirmed').disabled,true);
 const accept=d.querySelector('.candidate input');accept.click();
 assert.match(d.getElementById('stats').textContent,/1 accepted/);
 assert.equal(d.getElementById('export-confirmed').disabled,true);
 d.getElementById('export').click();
 let output=JSON.parse(await ui.downloads[0].blob.text());
 assert.equal(output.confirmed,false);
 assert.equal(output.choices.filter(c=>c.action==='accept').length,1);
 d.getElementById('confirmed').click();
 assert.equal(d.getElementById('export-confirmed').disabled,false);
 d.getElementById('export-confirmed').click();
 output=JSON.parse(await ui.downloads[1].blob.text());
 assert.equal(output.confirmed,true);
 assert.equal(output.proposal_id,proposal.proposal_id);
 assert.equal(ui.downloads[1].filename,'decisions.json');
 const note=d.querySelector('textarea');note.value='Human follow-up';note.dispatchEvent(new w.Event('input',{bubbles:true}));
 assert.equal(d.getElementById('confirmed').checked,false);
 assert.equal(d.getElementById('export-confirmed').disabled,true);
 ui.dom.window.close();
});

test('search/filter, imported choices and invalid import recovery',async()=>{
 const ui=create();const {document:d,window:w}=ui;
 const search=d.getElementById('search');search.value='Xylophone';search.dispatchEvent(new w.Event('input'));
 assert.equal([...d.querySelectorAll('article')].filter(n=>!n.hidden).length,1);
 search.value='';search.dispatchEvent(new w.Event('input'));
 const filter=d.getElementById('filter');filter.value='no-candidates';change(w,filter);
 assert.equal([...d.querySelectorAll('article')].filter(n=>!n.hidden).length,2);
 filter.value='all';change(w,filter);
 const a=proposal.annotations.find(a=>a.candidates.length);
 const data={schema_version:1,proposal_id:proposal.proposal_id,confirmed:true,choices:[{annotation_id:a.id,action:'accept',candidate_id:a.candidates[0].id,note:'Reviewed separately'}]};
 const input=d.getElementById('import');
 async function importData(value){Object.defineProperty(input,'files',{configurable:true,value:[{size:100,text:async()=>JSON.stringify(value)}]});change(w,input);await new Promise(r=>setImmediate(r));}
 await importData(data);
 assert.match(d.getElementById('stats').textContent,/1 accepted/);
 assert.equal(d.querySelector('textarea').value,'Reviewed separately');
 assert.equal(d.getElementById('confirmed').checked,false);
 assert.match(d.getElementById('message').textContent,/Choices imported/);
 d.getElementById('confirmed').click();
 assert.equal(d.getElementById('confirmed').checked,true);
 await importData({...data,choices:[...data.choices,...data.choices]});
 assert.equal(d.getElementById('confirmed').checked,false);
 assert.match(d.getElementById('message').textContent,/Import failed/);
 assert.match(d.getElementById('stats').textContent,/1 accepted/);
 await importData({...data,proposal_id:'wrong'});
 assert.match(d.getElementById('message').textContent,/different proposal/);
 await importData({...data,choices:[{...data.choices[0],unknown_field:'not in the contract'}]});
 assert.match(d.getElementById('message').textContent,/Unknown decision field/);
 assert.equal(ui.errors.length,0,ui.errors.join('\n'));
 ui.dom.window.close();
});

test('empty annotation bundle explains the missing export and remains unconfirmed',()=>{
 const empty=fs.readFileSync(path.join(emptyBundle,'review.html'),'utf8');
 const ui=create(empty);const d=ui.document;
 assert.equal(ui.errors.length,0,ui.errors.join('\n'));
 assert.equal(d.querySelectorAll('article').length,0);
 assert.equal(d.getElementById('bundle-notes').open,true);
 assert.match(d.getElementById('warnings').textContent,/No embedded review annotations/);
 assert.equal(d.getElementById('confirmed').checked,false);
 assert.equal(d.getElementById('export-confirmed').disabled,true);
 ui.dom.window.close();
});
