import { useCallback, useEffect, useState } from "react";
import { ClerkProvider, Show, SignIn, SignUp, useClerk, useUser } from "@clerk/react";
import { publishableKeyFromHost } from "@clerk/react/internal";
import { ptBR } from "@clerk/localizations";
import { shadcn } from "@clerk/themes";
import { Link, Redirect, Route, Router as WouterRouter, Switch, useLocation, useParams } from "wouter";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AlertTriangle, ArrowLeft, Check, ChevronRight, CircleHelp, ClipboardList, CloudUpload, FileImage, LogOut, Search, ShieldCheck, UploadCloud, X } from "lucide-react";
import { getStudy, listStudies, retryStudy, reviewStudy, uploadStudies } from "./api";
import "./styles.css";

const basePath = import.meta.env.BASE_URL.replace(/\/$/, "");
const clerkPubKey = publishableKeyFromHost(window.location.hostname, import.meta.env.VITE_CLERK_PUBLISHABLE_KEY);
const clerkProxyUrl = import.meta.env.VITE_CLERK_PROXY_URL;
const appearance = {
  theme: shadcn, cssLayerName: "clerk",
  options: { logoPlacement: "inside", logoLinkUrl: basePath || "/", logoImageUrl: `${window.location.origin}${basePath}/logo.svg` },
  variables: { colorPrimary: "#19bda7", colorForeground: "#eff6ff", colorMutedForeground: "#9aaec4", colorBackground: "#0f1a32", colorInput: "#09162c", colorInputForeground: "#eff6ff", colorDanger: "#fb6d78", colorNeutral: "#29405e", fontFamily: "Plus Jakarta Sans", borderRadius: "0.55rem" },
  elements: { rootBox: "w-full flex justify-center", cardBox: "rounded-xl w-[440px] max-w-full overflow-hidden", card: "!shadow-none !border-0 !bg-transparent", footer: "!shadow-none !border-0 !bg-transparent", main: "bg-transparent" }
};
const localization = {
  ...ptBR,
  signIn: {
    ...ptBR.signIn,
    start: {
      ...ptBR.signIn?.start,
      title: "Entrar no Chester AI",
      titleCombined: "Entrar no Chester AI",
      subtitle: "Informe seu email para receber o código de acesso.",
      subtitleCombined: "Informe seu email para receber o código de acesso.",
    },
    emailCode: {
      ...ptBR.signIn?.emailCode,
      title: "Verifique seu email",
      formTitle: "Código de verificação",
      subtitle: "Enviamos um código de 6 dígitos para seu email.",
    },
  },
  signUp: {
    ...ptBR.signUp,
    emailCode: {
      ...ptBR.signUp?.emailCode,
      title: "Verifique seu email",
      formTitle: "Código de verificação",
      formSubtitle: "Insira o código de 6 dígitos enviado para seu email.",
    },
  },
};
const stripBase = (path) => basePath && path.startsWith(basePath) ? path.slice(basePath.length) || "/" : path;

function Brand({ light = false }) { return <div className={`brand ${light ? "light" : ""}`}><img src={`${basePath}/logo.svg`} alt="Chester" /><div>chester<small>radiology research console</small></div></div>; }
const statusLabels = { received: "recebido", validating: "validando", queued: "na fila", processing: "processando", completed: "concluído", needs_review: "requer revisão", rejected: "rejeitado", error: "erro" };
function Status({ value }) { const key = String(value || "queued").toLowerCase(); return <span className={`pill pill-${key}`}>{statusLabels[key] || key.replaceAll("_", " ")}</span>; }
function displayAge(value) { if (!value) return "Age —"; return String(value).trim().toUpperCase().match(/^\d{3}[DWMY]$/) ? String(value).trim().toUpperCase() : `${value}y`; }
function latestResultRows(results = []) {
  const run = [...results].sort((a, b) => String(a.created_at || "").localeCompare(String(b.created_at || ""))).at(-1);
  if (!run || !run.raw_scores) return [];
  const keys = [...new Set([...Object.keys(run.raw_scores || {}), ...Object.keys(run.op_normalized_scores || {}), ...Object.keys(run.thresholds || {})])];
  return keys.map((pathology) => ({ pathology, raw_score: run.raw_scores?.[pathology], normalized_score: run.op_normalized_scores?.[pathology], threshold: run.thresholds?.[pathology], above_threshold: run.above_threshold?.[pathology] ?? run.above_threshold_findings?.includes?.(pathology) }));
}
function Thumb({ url }) { return <div className="thumb">{url ? <img src={url} alt="Study radiograph" /> : <FileImage size={28} />}</div>; }
function Sidebar() {
  const { signOut } = useClerk();
  return <aside className="sidebar"><Brand /><nav className="nav"><Link href="/worklist" className="active"><ClipboardList size={16}/><span>Worklist</span></Link><Link href="/worklist?status=needs_review"><AlertTriangle size={16}/><span>Requer revisão</span></Link></nav><div className="sidebar-note"><ShieldCheck size={15}/><br/>Console de pesquisa<br/>Todo resultado requer revisão especializada.</div><button className="btn btn-subtle" onClick={() => signOut({ redirectUrl: basePath || "/" })}><LogOut size={15}/><span>Sair</span></button></aside>;
}
function AppShell({ children }) { const { user } = useUser(); return <div className="app-shell"><Sidebar/><main className="main"><div className="topbar"><div className="eyebrow">Sala de leitura / {new Date().toLocaleDateString("pt-BR", { day: "2-digit", month: "short", year: "numeric" })}</div><div className="user-chip"><span>{user?.firstName || user?.primaryEmailAddress?.emailAddress || "Leitor"}</span><div className="avatar">{(user?.firstName || "L").slice(0,1)}</div></div></div>{children}</main></div>; }
function Landing() { return <div className="landing"><div className="landing-nav"><Brand light/><Link className="btn btn-accent" href="/sign-in">Entrar <ChevronRight size={15}/></Link></div><section className="hero"><div className="eyebrow">Imagem torácica · fluxo de pesquisa</div><h1>Leia o sinal.<br/><em>Questione o ruído.</em></h1><p>Chester é a console segura para revisar radiografias de tórax, validar resultados do modelo e manter cada decisão rastreável.</p><Link className="btn btn-accent" href="/sign-up">Abrir a console <ChevronRight size={15}/></Link></section><div className="safety"><ShieldCheck size={17}/><span><strong>Aviso de segurança.</strong> Chester é destinado exclusivamente à pesquisa. Não é um dispositivo diagnóstico e deve ser usado somente com dados de teste ou devidamente desidentificados.</span></div></div>; }
function UploadDialog({ onDone }) {
  const [files, setFiles] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [result, setResult] = useState(null);
  const choose = (nextFiles) => { setFiles([...nextFiles]); setConfirmed(false); setResult(null); setError(""); };
  const submit = async () => {
    if (!files.length || !confirmed) return;
    setBusy(true); setError(""); setResult(null);
    try {
      const response = await uploadStudies(files);
      setResult(response);
      if (!response.errors?.length) onDone();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  return <div className="panel" style={{marginBottom:18}}><div className="panel-title"><div><h3>Manual upload</h3><span>test data only</span></div><CircleHelp size={17} color="#7c918b"/></div><div className="upload-zone" onDragOver={(e)=>e.preventDefault()} onDrop={(e)=>{e.preventDefault();choose(e.dataTransfer.files)}}><UploadCloud size={26}/><p>Drop studies here or choose DICOM, PNG, or JPEG files.</p><input id="files" type="file" multiple accept=".dcm,.dicom,image/png,image/jpeg" hidden onChange={(e)=>choose(e.target.files)}/><label className="btn btn-subtle" htmlFor="files"><CloudUpload size={15}/>Choose files</label></div>{files.length > 0 && <div style={{marginTop:12,fontSize:12}}>{files.map((f)=><div key={`${f.name}-${f.size}`} className="meta-row"><FileImage size={14}/>{f.name}<span className="mono">{(f.size/1024/1024).toFixed(1)} MB · {busy ? "uploading" : "ready"}</span></div>)}<label style={{display:"flex",gap:8,alignItems:"center",fontSize:11,margin:"12px 0"}}><input type="checkbox" checked={confirmed} onChange={(e)=>setConfirmed(e.target.checked)}/> I confirm these are test or de-identified images.</label><button className="btn btn-primary" onClick={submit} disabled={busy || !confirmed}>{busy ? "Uploading…" : "Upload studies"}</button></div>}{result?.errors?.length > 0 && <div className="notice" style={{marginTop:10}}><strong>{result.studies?.length || 0} accepted; {result.errors.length} rejected.</strong>{result.errors.map((item)=><div key={item.filename} className="meta-row"><span>{item.filename}</span><span>{item.error}</span></div>)}<button className="btn btn-subtle" style={{marginTop:10}} onClick={onDone}>Return to worklist</button></div>}{error && <div className="error-box" style={{marginTop:10,padding:12}}>{error}</div>}</div>;
}
function StudyCard({ study }) { return <Link href={`/studies/${study.id}`} className="study-card"><Thumb url={study.thumbnail_url}/><div className="study-primary"><strong>{study.patient_id || "Não identificado"}</strong><div className="meta-row">{study.description || "Radiografia de tórax"} · {study.modality || "XR"} {study.view_position ? `· ${study.view_position}` : ""}</div></div><div className="study-cell"><b>{displayAge(study.patient_age)} / {study.patient_sex || "—"}</b><span className="mono">{study.study_date || "Sem data"}</span></div><div className="study-cell"><b>{study.source || "Upload manual"}</b><span>{study.validation_reason || study.validation_state || "Validação pendente"}</span></div><div className="findings">{study.top_findings?.length ? study.top_findings.slice(0,2).map((finding,index)=><span className="finding" key={`${finding.pathology}-${index}`}>{finding.pathology} {Number(finding.normalized_score).toFixed(2)}</span>) : <span className="finding">Aguardando modelo</span>}</div><Status value={study.status}/><ChevronRight size={15} color="#6f90aa"/></Link>; }
function Worklist() {
  const [location] = useLocation(); const [query, setQuery] = useState(""); const [status, setStatus] = useState(()=>new URLSearchParams(window.location.search).get("status") || ""); const [data, setData] = useState(null); const [error, setError] = useState(""); const [uploadOpen, setUploadOpen] = useState(false);
  const load = useCallback(async () => { try { setError(""); setData(await listStudies({search:query,status})); } catch(e) { setError(e.message); } }, [query,status]);
  useEffect(()=>{ setStatus(new URLSearchParams(window.location.search).get("status") || ""); },[location]); useEffect(()=>{ load(); },[load]); useEffect(()=>{ if (!data?.items?.some(s=>!["completed","needs_review","rejected","error"].includes(s.status))) return; const timer=setInterval(load,4000); return()=>clearInterval(timer); },[data,load]);
  const counts = data?.counts || {}; const items = data?.items || [];
  return <AppShell><div><div className="eyebrow">Monitoramento de qualidade · estudos de tórax</div><h1 style={{marginTop:7}}>Worklist</h1><p className="page-subtitle">Triagem, processamento e revisão de estudos em uma única superfície de leitura.</p></div><div className="toolbar"><div className="search"><Search size={16}/><input className="input" placeholder="Buscar ID do paciente ou descrição…" value={query} onChange={e=>setQuery(e.target.value)}/></div><select className="select" value={status} onChange={e=>setStatus(e.target.value)}><option value="">Todos os status</option>{["received","validating","queued","processing","completed","needs_review","rejected","error"].map(value=><option key={value} value={value}>{statusLabels[value]}</option>)}</select><button className="btn btn-primary" onClick={()=>setUploadOpen(!uploadOpen)}><CloudUpload size={15}/>Enviar estudo</button></div>{uploadOpen && <UploadDialog onDone={()=>{setUploadOpen(false);load()}}/>}<div className="stats"><div className="stat"><b>{data?.total ?? "—"}</b><span>Total de estudos</span></div><div className="stat"><b>{data ? (counts.received || 0) + (counts.validating || 0) + (counts.queued || 0) + (counts.processing || 0) : "—"}</b><span>Em processamento</span></div><div className="stat"><b>{counts.completed ?? "—"}</b><span>Concluídos</span></div><div className="stat"><b>{data ? (counts.needs_review || 0) + (counts.error || 0) : "—"}</b><span>Requer atenção</span></div></div>{error ? <div className="error-box"><AlertTriangle size={22}/><h3>Worklist indisponível</h3><p>{error}</p><button className="btn btn-subtle" style={{marginTop:15}} onClick={load}>Tentar novamente</button></div> : !data ? <div className="study-list"><div className="skeleton"/><div className="skeleton"/><div className="skeleton"/></div> : items.length ? <><div className="worklist-head"><span>Imagem</span><span>Estudo / paciente</span><span>Demografia</span><span>Origem / validação</span><span>Achados principais</span><span>Status</span><span/></div><div className="study-list">{items.map(s=><StudyCard key={s.id} study={s}/>)}</div></> : <div className="empty"><ClipboardList size={28}/><h3>Nenhum exame encontrado</h3><p>Tente outra busca ou envie um estudo desidentificado.</p></div>}</AppShell>;
}
function Detail() {
  const { id } = useParams(); const [study,setStudy]=useState(null); const [error,setError]=useState(""); const [busy,setBusy]=useState(false);
  const load=useCallback(async()=>{try{setStudy(await getStudy(id))}catch(e){setError(e.message)}},[id]); useEffect(()=>{load()},[load]);
  const action=async(fn)=>{setBusy(true);try{await fn();await load()}catch(e){setError(e.message)}finally{setBusy(false)}};
  if(error) return <AppShell><div className="error-box"><AlertTriangle size={22}/><h3>Could not load study</h3><p>{error}</p><button className="btn btn-subtle" style={{marginTop:15}} onClick={load}>Try again</button></div></AppShell>;
  if(!study) return <AppShell><div className="detail-grid"><div className="skeleton"/><div className="skeleton"/></div></AppShell>;
  const results=latestResultRows(study.results || []); const chartData=results.map((r,i)=>({name:r.pathology || `Finding ${i+1}`, score:Number(r.normalized_score ?? 0)})); const canReview=study.status==="needs_review";
  return <AppShell><Link href="/worklist" className="eyebrow" style={{display:"inline-flex",gap:7,alignItems:"center",marginBottom:20}}><ArrowLeft size={14}/> Back to worklist</Link><div className="detail-header"><div><h1>{study.description || "Chest radiograph"}</h1><div className="meta-row" style={{marginTop:10}}><span className="mono">{study.id}</span><Status value={study.status}/><span>{study.source || "Manual upload"}</span></div></div><div className="detail-actions">{study.status==="error" && <button className="btn btn-subtle" disabled={busy} onClick={()=>action(()=>retryStudy(id))}>Retry analysis</button>}{canReview && <><button className="btn btn-accent" disabled={busy} onClick={()=>action(()=>reviewStudy(id,"approve"))}><Check size={15}/> Approve for analysis</button><button className="btn btn-danger" disabled={busy} onClick={()=>action(()=>reviewStudy(id,"reject"))}><X size={15}/> Reject study</button></>}</div></div>{study.status==="error" && <div className="notice" style={{marginBottom:14}}><strong>Inference error.</strong> {study.error_message || "Analysis could not be completed. Retry when ready."}</div>}{study.status==="needs_review" && <div className="notice" style={{marginBottom:14}}><strong>Review required.</strong> Confirm this study before analysis proceeds.</div>}<div className="detail-grid"><div><div className="panel"><div className="panel-title"><h3>Study image</h3><span>{study.modality || "XR"}</span></div><div style={{background:"#263b40",borderRadius:9,minHeight:315,display:"grid",placeItems:"center"}}><Thumb url={study.thumbnail_url}/></div></div><div className="panel"><div className="panel-title"><h3>Exam metadata</h3><span>source record</span></div><dl className="metadata"><div><dt>Patient</dt><dd>{study.patient_id || "Unidentified"}</dd></div><div><dt>Age / sex</dt><dd>{displayAge(study.patient_age)} / {study.patient_sex || "—"}</dd></div><div><dt>Study date</dt><dd>{study.study_date || "—"}</dd></div><div><dt>View position</dt><dd>{study.view_position || "—"}</dd></div><div><dt>Model version</dt><dd>{study.model_version || "—"}</dd></div><div><dt>Preprocessing</dt><dd>{study.preprocessing_version || "—"}</dd></div></dl></div></div><div><div className="panel"><div className="panel-title"><h3>AI findings</h3><span>{results.length} outputs · latest run</span></div><div className="notice"><strong>Research score, not probability.</strong> Raw model output and operating-point normalized scores are shown for research interpretation. Neither is a calibrated clinical probability.</div><div style={{overflowX:"auto",marginTop:15}}><table><thead><tr><th>Pathology</th><th>Raw output</th><th>Normalized research score</th><th>Threshold</th><th>Flag</th></tr></thead><tbody>{results.map((r,i)=><tr key={r.pathology || i}><td><b>{r.pathology || "Unnamed output"}</b></td><td className="mono">{Number(r.raw_score ?? 0).toFixed(4)}</td><td><div style={{display:"flex",alignItems:"center",gap:9}}><div className="bar"><i style={{width:`${Math.min(100,Number(r.normalized_score ?? 0)*100)}%`}}/></div><span className="mono">{Number(r.normalized_score ?? 0).toFixed(3)}</span></div></td><td className="mono">{Number(r.threshold ?? 0).toFixed(3)}</td><td>{r.above_threshold ? <span className="pill pill-needs_review">above</span> : <span className="pill pill-completed">below</span>}</td></tr>)}</tbody></table></div></div>{results.length>0 && <div className="panel"><div className="panel-title"><h3>Score distribution</h3><span>normalized research score</span></div><div className="chart"><ResponsiveContainer width="100%" height="100%"><AreaChart data={chartData}><CartesianGrid stroke="#e1e6df" vertical={false}/><XAxis dataKey="name" tick={{fontSize:10}} angle={-20} height={55} interval={0}/><YAxis domain={[0,1]} tick={{fontSize:10}}/><Tooltip/><Area type="monotone" dataKey="score" stroke="#28796d" fill="#d6f2e7" strokeWidth={2}/></AreaChart></ResponsiveContainer></div></div>}</div></div></AppShell>;
}
function AuthPage({ signUp=false }) { return <div className="auth-shell"><div className="clerk-wrap"><div className="auth-mark"><Brand light/><p>Console seguro para pesquisa em radiologia torácica</p></div>{signUp ? <SignUp routing="path" path={`${basePath}/sign-up`} signInUrl={`${basePath}/sign-in`}/> : <SignIn routing="path" path={`${basePath}/sign-in`} signUpUrl={`${basePath}/sign-up`}/>}<p style={{textAlign:"center",color:"#657a95",fontSize:10,marginTop:18}}>Acesso restrito a usuários autorizados</p></div></div>; }
function HomeRoute(){return <><Show when="signed-in"><Redirect to="/worklist"/></Show><Show when="signed-out"><Landing/></Show></>;}
function Protected({children}){return <><Show when="signed-in">{children}</Show><Show when="signed-out"><Redirect to="/"/></Show></>;}
function Routes(){ return <Switch><Route path="/" component={HomeRoute}/><Route path="/sign-in/*?" component={()=> <AuthPage/>}/><Route path="/sign-up/*?" component={()=> <AuthPage signUp/>}/><Route path="/worklist"><Protected><Worklist/></Protected></Route><Route path="/studies/:id"><Protected><Detail/></Protected></Route><Route><Redirect to="/"/></Route></Switch>; }
function ClerkRoutes(){ const [,setLocation]=useLocation(); return <ClerkProvider publishableKey={clerkPubKey} proxyUrl={clerkProxyUrl} appearance={appearance} localization={localization} signInUrl={`${basePath}/sign-in`} signUpUrl={`${basePath}/sign-up`} routerPush={(to)=>setLocation(stripBase(to))} routerReplace={(to)=>setLocation(stripBase(to), { replace: true })}><Routes/></ClerkProvider>; }
export default function App(){ if(!clerkPubKey) return <div className="error-box">Missing VITE_CLERK_PUBLISHABLE_KEY.</div>; return <WouterRouter base={basePath}><ClerkRoutes/></WouterRouter>; }