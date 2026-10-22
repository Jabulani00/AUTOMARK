// AutoMark prototype - frontend logic (vanilla JS, no build step).
"use strict";

const api = {
  async get(u){ const r = await fetch(u); return r.json(); },
  async send(method, u, body){
    const r = await fetch(u, {method, headers:{'Content-Type':'application/json'},
      body: body!==undefined ? JSON.stringify(body) : undefined});
    let data = null; try{ data = await r.json(); }catch(e){}
    return {ok:r.ok, status:r.status, data};
  },
};

let ME = null;               // current user {id,email,role,display_name}
let TCH = null;              // assignment being edited (teacher)
let AUTH_TAB = "login";
let ASSIGN_TITLES = {};      // id -> title (for student history)

function $(id){ return document.getElementById(id); }
function esc(s){ return (s==null?"":String(s)).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

// ---------------- env + boot ----------------
async function initEnv(){
  const e = await api.get("/api/env");
  $("envtag").textContent =
    `Python ${e.python} · Java ${e.java?"ready":"off"} · DB: ${e.db} · marking: ${e.llm}`;
}
async function boot(){
  await initEnv();
  const {user} = await api.get("/api/auth/me");
  if(user){ enterApp(user); } else { showAuth(); }
}

// ---------------- auth ----------------
function showAuth(){
  ME = null;
  $("authView").classList.remove("hidden");
  $("studentView").classList.add("hidden");
  $("teacherView").classList.add("hidden");
  $("userbox").classList.add("hidden");
  $("teacherNav").classList.add("hidden");
  $("demoHint").innerHTML = "Demo accounts (password <b>password123</b>): "
    + "teacher@automark.test, alice@class.test";
}
function setAuthTab(tab){
  AUTH_TAB = tab;
  $("tabLogin").classList.toggle("active", tab==="login");
  $("tabSignup").classList.toggle("active", tab==="signup");
  $("signupName").classList.toggle("hidden", tab!=="signup");
  $("authBtn").textContent = tab==="login" ? "Sign in" : "Create account & sign in";
  $("authErr").innerHTML = "";
}
async function doAuth(){
  $("authErr").innerHTML = "";
  const email = $("auEmail").value.trim(), pass = $("auPass").value;
  const path = AUTH_TAB==="login" ? "/api/auth/login" : "/api/auth/signup";
  const body = AUTH_TAB==="login" ? {email,password:pass}
             : {email,password:pass,display_name:$("auName").value.trim()};
  const {ok,data} = await api.send("POST", path, body);
  if(!ok){ $("authErr").innerHTML = `<div class="err">${esc(data&&data.error||"Failed")}</div>`; return; }
  enterApp(data.user);
}
async function logout(){ await api.send("POST","/api/auth/logout"); showAuth(); }

function enterApp(user){
  ME = user;
  $("authView").classList.add("hidden");
  $("userbox").classList.remove("hidden");
  $("uName").textContent = user.display_name || user.email;
  $("uRole").textContent = "· " + user.role;
  if(user.role==="teacher"){
    $("teacherNav").classList.remove("hidden");
    $("teacherView").classList.remove("hidden");
    $("studentView").classList.add("hidden");
    showTeacherTab("work");
    loadTeacherAssignments();
  } else {
    $("teacherNav").classList.add("hidden");
    $("studentView").classList.remove("hidden");
    $("teacherView").classList.add("hidden");
    loadStudentAssignments().then(loadMySubs);  // titles must load before history
  }
}

// ---------------- STUDENT ----------------
async function loadStudentAssignments(){
  const {assignments} = await api.get("/api/assignments");
  ASSIGN_TITLES = {}; assignments.forEach(a=>ASSIGN_TITLES[a.id]=a.title);
  const sel = $("stuAssignment");
  sel.innerHTML = assignments.map(a=>`<option value="${a.id}">${esc(a.title)} · ${a.language}</option>`).join("")
    || `<option>No published assignments yet</option>`;
  loadBrief();
}
async function loadBrief(){
  const id = $("stuAssignment").value; if(!id) return;
  const a = await api.get("/api/assignments/"+id);
  $("stuBrief").textContent = a.brief || "—";
}
let ZIP_B64 = null;   // set when a .zip is uploaded (multi-file)
function loadFile(){
  const f = $("stuFile").files[0]; if(!f) return;
  if(f.name.toLowerCase().endsWith(".zip")){
    const reader = new FileReader();
    reader.onload = () => {
      const bytes = new Uint8Array(reader.result);
      let bin = ""; for(let i=0;i<bytes.length;i++) bin += String.fromCharCode(bytes[i]);
      ZIP_B64 = btoa(bin);
      $("stuCode").value = `[uploaded zip: ${f.name} — extracted and graded as multiple files]`;
      $("stuCode").readOnly = true;
    };
    reader.readAsArrayBuffer(f);
  } else {
    const reader = new FileReader();
    reader.onload = () => { ZIP_B64 = null; $("stuCode").readOnly = false; $("stuCode").value = reader.result; };
    reader.readAsText(f);
  }
}
function clearCode(){ ZIP_B64=null; $("stuCode").readOnly=false; $("stuCode").value=""; $("stuFile").value=""; }

async function submitCode(){
  $("submitErr").innerHTML = "";
  const btn = $("submitBtn"); btn.disabled = true; btn.textContent = "Marking…";
  const body = {
    assignment_id: $("stuAssignment").value,
    filename: ($("stuFile").files[0]||{}).name || "Submission",
    code: $("stuCode").value,
    zip_b64: ZIP_B64 || undefined,
  };
  const {ok, data} = await api.send("POST", "/api/submit", body);
  btn.disabled = false; btn.textContent = "Submit for marking →";
  if(!ok){ $("submitErr").innerHTML = `<div class="err">${esc(data&&data.error||"Submission failed")}</div>`; return; }
  renderResult(data);
  loadMySubs();
}

function bar(el, v){ $(el).style.width = Math.round(v*100)+"%"; }
function renderResult(sub){
  $("resultCard").classList.remove("hidden");
  $("resStatus").textContent = sub.status;
  $("resStatus").className = "pill " + sub.status;
  const oldFlag = document.getElementById("resFlag"); if(oldFlag) oldFlag.remove();
  if(sub.result && sub.result.flags && sub.result.flags.length){
    $("resStatus").insertAdjacentHTML("afterend",
      ' <span class="pill not_met" id="resFlag">⚠ flagged for review</span>');
  }
  if(sub.status!=="graded"){ $("resMark").textContent="—";
    $("resFeedback").textContent = sub.error || "Could not grade."; return; }
  const r = sub.result, s = r.scores;
  $("resMark").textContent = (sub.override? sub.override.mark : s.final_mark);
  $("resOverride").textContent = sub.override
    ? `Teacher override: ${sub.override.mark} (original ${s.final_mark}). Reason: ${sub.override.reason}` : "";
  $("wTests").textContent = `${s.weights.tests}% · score ${(s.test_score*100).toFixed(0)}%`;
  $("wRubric").textContent = `${s.weights.rubric}% · score ${(s.rubric_score*100).toFixed(0)}%`;
  $("wStyle").textContent = `${s.weights.style}% · score ${(s.style_score*100).toFixed(0)}%`;
  bar("barTests", s.test_score); bar("barRubric", s.rubric_score); bar("barStyle", s.style_score);
  $("resFeedback").textContent = r.feedback;
  const g = r.grader;
  function detailCell(t){
    let d = `<span class="muted mono">${esc(t.message||'')}</span>`;
    if(t.methods && t.methods.length){
      d += `<div style="margin-top:4px">` + t.methods.map(m=>
        `<span class="pill ${m.passed?'passed':'fail'}" style="margin:1px" title="${esc(m.message||'')}">${esc(m.method)}</span>`).join(" ") + `</div>`;
    }
    if(t.coverage!=null){ d += `<div class="muted" style="margin-top:3px">line coverage: ${t.coverage}%</div>`; }
    if(t.earned!=null && t.earned!==t.marks){ d += `<div class="muted">earned ${t.earned} of ${t.marks} marks</div>`; }
    return d;
  }
  $("resTests").innerHTML =
    `<tr><th>Test</th><th>Result</th><th>Marks</th><th>Detail</th></tr>` +
    (g.tests.map(t=>`<tr><td>${esc(t.name)}</td>
       <td><span class="pill ${t.passed?'passed':'fail'}">${t.passed?'pass':'fail'}</span></td>
       <td>${t.marks}</td><td>${detailCell(t)}</td></tr>`).join("")
     || `<tr><td class="muted">No tests ran.</td></tr>`);
  if(g.warnings && g.warnings.length){
    $("resTests").innerHTML += `<tr><td colspan="4" class="muted">⚠ ${g.warnings.length} compiler warning(s): ${esc(g.warnings.slice(0,3).join("; "))}</td></tr>`;
  }
  if(g.compiled){ $("compileBox").innerHTML = ""; }
  else {
    const errLines = new Set(g.error_lines||[]);
    const src = (sub.code||"").split("\n");
    const numbered = src.map((ln,i)=>{
      const n=i+1, bad=errLines.has(n);
      return `<div class="codeline${bad?' bad':''}"><span class="ln">${n}</span>${esc(ln)||' '}</div>`;
    }).join("");
    $("compileBox").innerHTML =
      `<div class="err" style="margin-top:10px">Did not compile — see highlighted line(s):</div>
       <div class="codeview">${numbered}</div>
       <pre class="code">${esc((g.compile_errors||[]).join("\n"))}</pre>`;
  }
  $("resReqs").innerHTML = r.rubric.requirements.map(q=>`<div class="reqline">
     <span class="pill ${q.verdict}">${q.verdict.replace('_',' ')}</span>
     <div><b>${esc(q.id)}</b> ${esc(q.explanation)}
       <div class="lines">${q.lines&&q.lines.length?('lines '+q.lines.join(', ')):''}</div>
     </div></div>`).join("");
}
async function loadMySubs(){
  const {submissions} = await api.get("/api/submissions");
  $("mySubs").innerHTML = submissions.map(s=>`<tr style="cursor:pointer" onclick="openSub('${s.id}')">
     <td>${esc(ASSIGN_TITLES[s.assignment_id]||s.assignment_id||'')}</td>
     <td><span class="pill ${s.status}">${s.status}</span></td>
     <td>${s.attempts}</td><td>${s.mark==null?'—':s.mark}</td>
     <td class="muted">${esc(s.created_at.replace('T',' '))}</td></tr>`).join("")
     || `<tr><td class="muted">No submissions yet — submit one above.</td></tr>`;
}
async function openSub(id){
  const sub = await api.get("/api/submission/"+id);
  if(sub && sub.result){ renderResult(sub); window.scrollTo({top:0,behavior:'smooth'}); }
}

// ---------------- TEACHER: nav ----------------
function showTeacherTab(tab){
  $("navWork").classList.toggle("active", tab==="work");
  $("navAnalytics").classList.toggle("active", tab==="analytics");
  $("teacherWork").classList.toggle("hidden", tab!=="work");
  $("analyticsView").classList.toggle("hidden", tab!=="analytics");
  if(tab==="analytics") loadAnalytics();
}

// ---------------- TEACHER: assignments ----------------
async function loadTeacherAssignments(){
  const {assignments} = await api.get("/api/assignments");
  const sel = $("tchAssignment");
  sel.innerHTML = assignments.map(a=>`<option value="${a.id}">${esc(a.title)} [${a.status}]</option>`).join("");
  if(assignments.length) loadAssignment(); else newAssignmentBlank();
}
function newAssignmentBlank(){
  TCH = {id:null, title:"", brief:"", language:"python", rubric:[], tests:[],
         weights:{tests:60,rubric:25,style:15}, status:"draft"};
  renderTeacher();
}
async function newAssignment(){
  const {ok,data} = await api.send("POST","/api/assignments",
    {title:"New assignment", brief:"", language:"python"});
  if(ok){ await loadTeacherAssignments(); $("tchAssignment").value=data.id; loadAssignment(); }
}
async function loadAssignment(){
  const id = $("tchAssignment").value;
  if(!id){ newAssignmentBlank(); return; }
  TCH = await api.get("/api/assignments/"+id);
  renderTeacher(); loadMarks();
}
function renderTeacher(){
  $("tchErr").innerHTML="";
  $("tchTitle").value=TCH.title; $("tchBrief").value=TCH.brief; $("tchLang").value=TCH.language;
  $("wgTests").value=TCH.weights.tests; $("wgRubric").value=TCH.weights.rubric; $("wgStyle").value=TCH.weights.style;
  $("tchStatus").textContent=TCH.status;
  $("tchStatus").className="pill "+(TCH.status==="published"?"graded":"pending");
  renderRubric(); renderTests();
}
function renderRubric(){
  const total = TCH.rubric.reduce((a,r)=>a+(+r.marks||0),0);
  $("rubricTotal").textContent = `(total ${total} / 100)`;
  $("rubricBody").innerHTML = TCH.rubric.map((r,i)=>`<tr>
     <td><input value="${esc(r.id)}" onchange="TCH.rubric[${i}].id=this.value"></td>
     <td><input value="${esc(r.text)}" onchange="TCH.rubric[${i}].text=this.value"></td>
     <td><input type="number" value="${r.marks}" onchange="TCH.rubric[${i}].marks=+this.value;renderRubric()"></td>
     <td><input value="${esc((r.patterns||[]).join(', '))}" onchange="TCH.rubric[${i}].patterns=this.value.split(',').map(s=>s.trim()).filter(Boolean)"></td>
     <td><input value="${esc((r.test_ids||[]).join(', '))}" placeholder="t1, t4" onchange="TCH.rubric[${i}].test_ids=this.value.split(',').map(s=>s.trim()).filter(Boolean)"></td>
     <td><button class="ghost sm" onclick="TCH.rubric.splice(${i},1);renderRubric()">✕</button></td></tr>`).join("");
}
function addRubric(){ TCH.rubric.push({id:"R"+(TCH.rubric.length+1),text:"",marks:0,patterns:[],test_ids:[]}); renderRubric(); }
function renderTests(){
  $("testBody").innerHTML = TCH.tests.map((t,i)=>{
    const kind = t.kind || 'io';
    const kindSel = `<select onchange="TCH.tests[${i}].kind=this.value;renderTests()">
       <option value="io" ${kind==='io'?'selected':''}>io</option>
       <option value="junit" ${kind==='junit'?'selected':''}>junit</option></select>`;
    const body = kind==='junit'
      ? `<td colspan="2"><textarea style="min-height:90px;white-space:pre" placeholder="import org.junit.jupiter.api.Test;\n...\npublic class XTest { @Test void t(){ assertTrue(...); } }" onchange="TCH.tests[${i}].body=this.value">${esc(t.body||'')}</textarea></td>`
      : `<td><textarea style="min-height:46px;white-space:pre" onchange="TCH.tests[${i}].stdin=this.value">${esc(t.stdin||'')}</textarea></td>
         <td><textarea style="min-height:46px;white-space:pre" onchange="TCH.tests[${i}].expected=this.value">${esc(t.expected||'')}</textarea></td>`;
    return `<tr>
     <td><input value="${esc(t.id||'')}" onchange="TCH.tests[${i}].id=this.value"></td>
     <td><input value="${esc(t.name)}" onchange="TCH.tests[${i}].name=this.value"></td>
     <td>${kindSel}</td>
     ${body}
     <td><input type="number" value="${t.marks}" onchange="TCH.tests[${i}].marks=+this.value"></td>
     <td><label class="muted"><input type="checkbox" style="width:auto" ${t.approved?'checked':''} onchange="TCH.tests[${i}].approved=this.checked"> ok</label></td>
     <td><button class="ghost sm" onclick="TCH.tests.splice(${i},1);renderTests()">✕</button></td></tr>`;
  }).join("");
}
function addTest(kind){
  const n = TCH.tests.length+1;
  if(kind==='junit') TCH.tests.push({id:"j"+n,name:"JUnit test",kind:"junit",body:"",marks:10,approved:false});
  else TCH.tests.push({id:"t"+n,name:"test",kind:"io",stdin:"",expected:"",marks:1,approved:false});
  renderTests();
}
function collect(){
  TCH.title=$("tchTitle").value; TCH.brief=$("tchBrief").value; TCH.language=$("tchLang").value;
  TCH.weights={tests:+$("wgTests").value,rubric:+$("wgRubric").value,style:+$("wgStyle").value};
}
async function saveAssignment(){
  collect();
  if(!TCH.id){ const {data}=await api.send("POST","/api/assignments",{title:TCH.title,brief:TCH.brief,language:TCH.language}); TCH.id=data.id; }
  const {ok,data}=await api.send("PUT","/api/assignments/"+TCH.id, TCH);
  if(ok){ TCH=data; await loadTeacherAssignments(); $("tchAssignment").value=TCH.id; renderTeacher(); flash("Saved."); }
}
async function autodraft(){
  if(!TCH.id) await saveAssignment();
  const {ok,data}=await api.send("POST","/api/assignments/"+TCH.id+"/autodraft");
  if(ok){ TCH=data; renderTeacher(); flash("Draft rubric & tests added — review and approve."); }
}
async function publish(){
  collect(); await api.send("PUT","/api/assignments/"+TCH.id, TCH);
  const {ok,data}=await api.send("POST","/api/assignments/"+TCH.id+"/publish");
  if(!ok){ $("tchErr").innerHTML=`<div class="err">${esc(data.error)}</div>`; }
  else { TCH=data; renderTeacher(); flash("Published — students can now submit."); }
}
function exportCsv(){ if(TCH&&TCH.id) window.location="/api/assignments/"+TCH.id+"/export.csv"; }
function flash(m){ $("tchErr").innerHTML=`<div class="brief">${esc(m)}</div>`;
  setTimeout(()=>{ if($("tchErr").textContent===m)$("tchErr").innerHTML=""; },3000); }

async function loadMarks(){
  if(!TCH.id){ $("marksBody").innerHTML=""; return; }
  const {submissions}=await api.get("/api/submissions?assignment_id="+TCH.id);
  $("marksBody").innerHTML = submissions.map(s=>`<tr>
     <td>${esc(s.student)}</td>
     <td><span class="pill ${s.status}">${s.status}</span></td>
     <td>${s.attempts}</td>
     <td>${s.mark==null?'—':s.mark}${s.overridden?` <span class="muted">(was ${s.original_mark})</span>`:''}${s.flagged?' <span class="pill not_met" title="Possible gaming / hard-coded answers">⚠ review</span>':''}</td>
     <td class="row">
       <button class="ghost sm" onclick="doOverride('${s.id}')">Override</button>
       <button class="ghost sm" onclick="doRegrade('${s.id}')">Regrade</button>
     </td></tr>`).join("") || `<tr><td class="muted">No submissions yet.</td></tr>`;
}
async function doOverride(sid){
  const mark=prompt("New mark (0-100):"); if(mark===null) return;
  const reason=prompt("Reason for override:"); if(!reason) return;
  const {ok,data}=await api.send("POST","/api/submission/"+sid+"/override",{mark:+mark,reason});
  if(ok) loadMarks(); else alert(data.error||"failed");
}
async function doRegrade(sid){ await api.send("POST","/api/submission/"+sid+"/regrade"); loadMarks(); }

// ---------------- TEACHER: analytics ----------------
function barRow(label, value, max, suffix, bad){
  const pct = max>0 ? Math.round(100*value/max) : 0;
  return `<div class="barrow"><span>${esc(label)}</span>
     <span class="track"><span class="fill ${bad?'bad':''}" style="width:${pct}%"></span></span>
     <span>${value}${suffix||''}</span></div>`;
}
async function loadAnalytics(){
  const a = await api.get("/api/analytics");
  if(a.error){ $("kpis").innerHTML=`<div class="err">${esc(a.error)}</div>`; return; }
  const t=a.totals;
  $("kpis").innerHTML = [
    ["Assignments", t.assignments], ["Submissions", t.submissions],
    ["Students", t.students],
    ["Avg mark", t.avg_mark==null?'—':t.avg_mark],
  ].map(([l,n])=>`<div class="kpi"><div class="n">${n}</div><div class="l">${l}</div></div>`).join("")
   + `<div class="kpi"><div class="n">${t.pass_rate==null?'—':t.pass_rate+'%'}</div><div class="l">Pass rate (≥50)</div></div>`;

  $("analyticsCards").innerHTML = a.assignments.map(x=>{
    const distMax = Math.max(1, ...x.distribution.map(d=>d.count));
    const dist = x.distribution.map(d=>barRow(d.band, d.count, distMax, "")).join("");
    const reqs = x.hardest_requirements.length
      ? x.hardest_requirements.map(r=>barRow(r.id+" not met", r.not_met, x.graded, "", true)).join("")
      : `<div class="muted">No failing requirements.</div>`;
    const tests = x.hardest_tests.length
      ? x.hardest_tests.map(r=>barRow(r.name, r.fails, x.graded, "", true)).join("")
      : `<div class="muted">No failing tests.</div>`;
    return `<div class="card">
      <h2>${esc(x.title)} <span class="muted">· ${x.language} · ${x.status}</span></h2>
      <div class="kpis" style="grid-template-columns:repeat(5,1fr)">
        <div class="kpi"><div class="n">${x.graded}</div><div class="l">graded</div></div>
        <div class="kpi"><div class="n">${x.avg_mark==null?'—':x.avg_mark}</div><div class="l">avg mark</div></div>
        <div class="kpi"><div class="n">${x.pass_rate==null?'—':x.pass_rate+'%'}</div><div class="l">pass rate</div></div>
        <div class="kpi"><div class="n">${x.avg_test==null?'—':x.avg_test}</div><div class="l">avg tests</div></div>
        <div class="kpi"><div class="n">${x.avg_rubric==null?'—':x.avg_rubric}</div><div class="l">avg rubric</div></div>
      </div>
      <div class="grid" style="margin-top:14px">
        <div><label>Grade distribution</label><div class="bars">${dist}</div></div>
        <div>
          <label>Most-failed requirements</label><div class="bars">${reqs}</div>
          <label style="margin-top:12px">Most-failed tests</label><div class="bars">${tests}</div>
        </div>
      </div></div>`;
  }).join("") || `<div class="card muted">No assignments yet.</div>`;
}

// ---------------- boot ----------------
setAuthTab("login");
boot();
