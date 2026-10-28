import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import crypto from 'node:crypto';
const ROOT=path.dirname(fileURLToPath(import.meta.url));
const DB=path.join(ROOT,'data','state.json');
const PORT=Number(process.env.PORT||3000);
const HOST=process.env.HOST||'127.0.0.1';
const LIVE=process.env.PROVIDER_MODE==='live';
const now=()=>new Date().toISOString();
const uid=(prefix)=>`${prefix}_${crypto.randomUUID().slice(0,12)}`;
const delay=(ms)=>new Promise(r=>setTimeout(r,ms));
async function readState(){try{return JSON.parse(await fs.readFile(DB,'utf8'))}catch{return {campaigns:[],events:[]}}}
let queue=Promise.resolve();
function update(fn){const job=queue.then(async()=>{const state=await readState();const result=await fn(state);await fs.mkdir(path.dirname(DB),{recursive:true});await fs.writeFile(DB,JSON.stringify(state,null,2));return result});queue=job.catch(()=>{});return job}
async function event(id,agent,action,status,detail={},evidence=null){return update(s=>{const e={id:uid('evt'),campaignId:id,at:now(),agent,action,status,detail,evidence};s.events.unshift(e);return e})}
async function patch(id,changes){return update(s=>{const c=s.campaigns.find(x=>x.id===id);if(!c)throw Error('Unknown campaign');Object.assign(c,changes,{updatedAt:now()});return c})}
async function get(id){const s=await readState();return s.campaigns.find(c=>c.id===id)}
function validateUrl(value){const u=new URL(value);if(!['https:','http:'].includes(u.protocol))throw Error('Website must be HTTP(S)');if(u.username||u.password)throw Error('Credentials in URL not allowed');return u.toString()}
function validateCampaign(b){if(!b||typeof b!=='object')throw Error('Invalid payload');const website=validateUrl(String(b.website||''));const task=String(b.task||'').trim().slice(0,500);if(task.length<12)throw Error('Testing task must have at least 12 characters');const name=String(b.name||'').trim().slice(0,80);if(!name)throw Error('Startup name required');const participants=Number(b.participants||3);if(!Number.isInteger(participants)||participants<1||participants>10)throw Error('Participants must be 1–10');return {website,task,name,participants}}
function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
// Adapter boundary: actual provider SDK/MCP calls belong here, not in the browser.
const adapters={
 async solari(c){if(LIVE)throw Error('Solari live adapter not configured. See ADAPTERS.md');await delay(650);return {mode:'demo',status:'simulated',website:c.website,checks:[{name:'URL syntax',result:'pass'},{name:'Browser opened',result:'demo-only'},{name:'Screenshot',result:'not captured'}],screenshot:null,sessionId:uid('solari_demo'),note:'No real website was visited.'}},
 async terac(c){if(LIVE)throw Error('Terac live adapter not configured. See ADAPTERS.md');await delay(650);return {mode:'demo',studyId:uid('terac_demo'),status:'simulated',participantTarget:c.participants,note:'No humans recruited; no rewards issued.'}},
 async payment(c){if(LIVE)throw Error('Whop live adapter not configured. See ADAPTERS.md');return {mode:'demo',status:'simulated',paymentId:uid('whop_demo'),amount:20,note:'No money charged.'}}
};
async function run(id){let c=await get(id);if(!c)return;try{
 await patch(id,{status:'inspecting'});await event(id,'SolariAgent','inspect_website','started',{website:c.website});
 const browser=await adapters.solari(c);await patch(id,{browser,status:'inspected'});await event(id,'SolariAgent','inspect_website','simulated',browser,browser.sessionId);
 await event(id,'CampaignAgent','payment_check','started');const payment=await adapters.payment(c);await patch(id,{payment});await event(id,'FinanceAgent','payment_check','simulated',payment,payment.paymentId);
 await patch(id,{status:'recruiting'});await event(id,'TeracAgent','launch_study','started',{participants:c.participants});const study=await adapters.terac(c);await patch(id,{study,status:'awaiting_feedback'});await event(id,'TeracAgent','launch_study','simulated',study,study.studyId);
 await event(id,'CampaignAgent','await_human_responses','waiting',{note:'In demo, submit clearly labeled sample feedback using the tester panel.'});
 }catch(e){await patch(id,{status:'blocked',error:e.message});await event(id,'Orchestrator','provider_failure','failed',{error:e.message})}}
async function report(id){const c=await get(id);if(!c)throw Error('Not found');const responses=c.responses||[];if(!responses.length)throw Error('No responses');const ratings=responses.map(r=>r.rating);const average=+(ratings.reduce((a,b)=>a+b,0)/ratings.length).toFixed(1);const successes=responses.filter(r=>r.completed).length;const issues=responses.filter(r=>r.issue.trim()).map(r=>({responseId:r.id,issue:r.issue}));const result={mode:'demo',generatedAt:now(),total:responses.length,goal:c.participants,averageRating:average,taskCompletion:successes,issues,summary:`${successes} of ${responses.length} submitted testers marked the task completed. Average ease rating: ${average}/10.`,warning:'DEMO DATA: responses were manually entered in the simulator. Not a Terac study.'};await patch(id,{report:result,status:'report_ready'});await event(id,'AnalysisAgent','generate_report','completed_demo',{total:responses.length,averageRating:average},null);return result}
const json=(res,status,obj)=>{res.writeHead(status,{'content-type':'application/json; charset=utf-8','cache-control':'no-store'});res.end(JSON.stringify(obj))};
async function body(req){let data='';for await(const part of req){data+=part;if(data.length>100000)throw Error('Payload too large')}return JSON.parse(data||'{}')}
const app=http.createServer(async(req,res)=>{try{const u=new URL(req.url,'http://localhost');const method=req.method;const parts=u.pathname.split('/').filter(Boolean);
 if(method==='GET'&&u.pathname==='/api/state'){const s=await readState();return json(res,200,{...s,providerMode:LIVE?'live-unconfigured':'demo'})}
 if(method==='POST'&&u.pathname==='/api/campaigns'){const input=validateCampaign(await body(req));const c={id:uid('cmp'),...input,status:'created',createdAt:now(),updatedAt:now(),responses:[],report:null};await update(s=>s.campaigns.unshift(c));await event(c.id,'CampaignAgent','campaign_created','completed_demo',{name:c.name});return json(res,201,c)}
 if(parts[0]==='api'&&parts[1]==='campaigns'&&parts[2]){const id=parts[2],c=await get(id);if(!c)return json(res,404,{error:'Campaign not found'});
 if(method==='POST'&&parts[3]==='run'){if(!['created','blocked'].includes(c.status))return json(res,409,{error:'Campaign already started'});await patch(id,{status:'queued'});run(id);return json(res,202,{ok:true,id})}
 if(method==='POST'&&parts[3]==='feedback'){if(LIVE)return json(res,403,{error:'Manual demo feedback disabled in live mode'});const b=await body(req);const rating=Number(b.rating);if(!Number.isInteger(rating)||rating<1||rating>10)throw Error('Rating must be 1–10');const response={id:uid('demo_response'),at:now(),tester:String(b.tester||'Anonymous').slice(0,50),completed:!!b.completed,rating,issue:String(b.issue||'').slice(0,500),source:'manual_demo_not_terac'};await update(s=>{const x=s.campaigns.find(x=>x.id===id);x.responses.push(response)});await event(id,'DemoTester','feedback_submitted','simulated',{responseId:response.id},response.id);return json(res,201,response)}
 if(method==='POST'&&parts[3]==='report'){const r=await report(id);return json(res,200,r)}
 if(method==='GET'&&parts[3]==='report'){if(!c.report)return json(res,404,{error:'Report not generated'});return json(res,200,c.report)}
 }
 if(method==='GET'&&(u.pathname==='/'||u.pathname==='/index.html')){const html=await fs.readFile(path.join(ROOT,'public','index.html'));res.writeHead(200,{'content-type':'text/html; charset=utf-8','cache-control':'no-store'});return res.end(html)}
 return json(res,404,{error:'Not found'});
 }catch(e){return json(res,400,{error:e.message})}});
app.listen(PORT,HOST,()=>console.log(`TestRunner MVP at http://${HOST==='0.0.0.0'?'localhost':HOST}:${PORT} (mode=${LIVE?'live-unconfigured':'demo'})`));
