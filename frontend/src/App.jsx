import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { Link, Redirect, Route, Router as WouterRouter, Switch, useLocation, useParams, useSearch } from "wouter";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Activity, AlertTriangle, ArrowLeft, Check, ChevronRight, CircleHelp, ClipboardList, CloudUpload, FileImage, Filter, Globe2, LockKeyhole, LogOut, RadioTower, RotateCcw, Search, Server, Settings2, ShieldCheck, UploadCloud, Wifi, X, XCircle } from "lucide-react";
import { createAllowedDomain, createAllowedEmail, deleteAllowedDomain, deleteAllowedEmail, getAccessMetadata, getDicomwebSettings, getSessionToken, getStudy, listAccessAudit, listAllowedDomains, listAllowedEmails, listStudies, logout, requestAccessCode, retryStudy, reviewStudy, updateAllowedDomain, updateAllowedEmail, uploadStudies, validateSession, verifyAccessCode } from "./api";
import "./styles.css";
import "./worklist-redesign.css";

const basePath = import.meta.env.BASE_URL.replace(/\/$/, "");
const AuthContext = createContext(null);
const useAuth = () => useContext(AuthContext);
const canAccess = (access, page) => Boolean(page === "access-control" ? access?.is_admin : access?.is_admin || access?.allowed_pages === null || access?.allowed_pages?.includes(page));

function Brand({ light = false }) { return <div className={`brand ${light ? "light" : ""}`}><img src={`${basePath}/logo.svg`} alt="Chester AI" /><div>chester<small>research console · cxr</small></div></div>; }
const statusLabels = { received: "recebido", validating: "validando", queued: "na fila", processing: "processando", completed: "concluído", needs_review: "requer revisão", rejected: "rejeitado", error: "erro" };
function Status({ value }) { const key = String(value || "queued").toLowerCase(); return <span className={`pill pill-${key}`}>{statusLabels[key] || key.replaceAll("_", " ")}</span>; }
function displayAge(value) { if (!value) return "Age —"; return String(value).trim().toUpperCase().match(/^\d{3}[DWMY]$/) ? String(value).trim().toUpperCase() : `${value}y`; }
function latestResultRows(results = []) {
  const run = [...results].sort((a, b) => String(a.created_at || "").localeCompare(String(b.created_at || ""))).at(-1);
  if (!run || !run.raw_scores) return [];
  const keys = [...new Set([...Object.keys(run.raw_scores || {}), ...Object.keys(run.op_normalized_scores || {}), ...Object.keys(run.thresholds || {})])];
  return keys.map((pathology) => ({ pathology, raw_score: run.raw_scores?.[pathology], normalized_score: run.op_normalized_scores?.[pathology], threshold: run.thresholds?.[pathology], above_threshold: run.above_threshold?.[pathology] ?? run.above_threshold_findings?.includes?.(pathology) }));
}
function Thumb({ url }) {
  const [imageUrl, setImageUrl] = useState("");
  useEffect(() => {
    if (!url) { setImageUrl(""); return undefined; }
    if (!url.startsWith("/api/")) { setImageUrl(url); return undefined; }
    let objectUrl = "";
    fetch(url, { headers: { "X-Session-Token": getSessionToken() || "" }, credentials: "same-origin" })
      .then((response) => response.ok ? response.blob() : Promise.reject(new Error("Thumbnail unavailable")))
      .then((blob) => { objectUrl = URL.createObjectURL(blob); setImageUrl(objectUrl); })
      .catch(() => setImageUrl(""));
    return () => { if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [url]);
  return <div className="thumb">{imageUrl ? <img src={imageUrl} alt="Study radiograph" /> : <FileImage size={28} />}</div>;
}
function Sidebar() {
  const { access, signOut } = useAuth();
  const [location] = useLocation();
  const search = useSearch();
  const reviewActive = location === "/worklist" && new URLSearchParams(search).get("status") === "needs_review";
  const worklistActive = location === "/worklist" && !reviewActive;
  const settingsActive = location === "/settings";
  return <aside className="sidebar"><Brand /><div className="sidebar-section-label">Pesquisa</div><nav className="nav" aria-label="Pesquisa">{canAccess(access, "worklist") && <Link href="/worklist" aria-label="Todos os estudos" aria-current={worklistActive ? "page" : undefined} className={worklistActive ? "active" : ""}><ClipboardList size={16}/><span>Todos os estudos</span></Link>}{canAccess(access, "review") && <Link href="/worklist?status=needs_review" aria-label="Estudos que requerem revisão" aria-current={reviewActive ? "page" : undefined} className={reviewActive ? "active" : ""}><AlertTriangle size={16}/><span>Requer revisão</span></Link>}</nav><div className="sidebar-section-label sidebar-system-label">Sistema</div><nav className="nav nav-secondary" aria-label="Sistema">{canAccess(access, "settings") && <Link href="/settings" aria-label="Ajustes DICOMweb" aria-current={settingsActive ? "page" : undefined} className={settingsActive ? "active" : ""}><Settings2 size={16}/><span>Ajustes <small>Settings</small></span></Link>}{access?.is_admin && <Link href="/access-control" aria-label="Controle de acesso" className={location === "/access-control" ? "active" : ""}><ShieldCheck size={16}/><span>Acessos</span></Link>}</nav><div className="sidebar-note"><ShieldCheck size={15}/><strong>Ambiente controlado</strong><br/>Resultados para pesquisa. Toda decisão requer revisão especializada.</div><button className="btn btn-subtle" onClick={signOut}><LogOut size={15}/><span>Sair da console</span></button></aside>;
}
function AppShell({ children }) { const { access } = useAuth(); return <div className="app-shell"><Sidebar/><main className="main"><div className="topbar"><div className="eyebrow">Sala de leitura / {new Date().toLocaleDateString("pt-BR", { day: "2-digit", month: "short", year: "numeric" })}</div><div className="user-chip"><span>{access?.email || "Leitor"}</span><div className="avatar">{(access?.email || "L").slice(0,1).toUpperCase()}</div></div></div>{children}</main></div>; }
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
  return <div className="panel upload-panel"><div className="panel-title"><div><h3>Envio manual</h3><span>somente dados de teste</span></div><CircleHelp size={17} color="#7c918b"/></div><div className="upload-zone" onDragOver={(e)=>e.preventDefault()} onDrop={(e)=>{e.preventDefault();choose(e.dataTransfer.files)}}><UploadCloud size={26}/><p>Arraste os estudos ou selecione arquivos DICOM, PNG ou JPEG.</p><input id="files" type="file" multiple accept=".dcm,.dicom,image/png,image/jpeg" hidden onChange={(e)=>choose(e.target.files)}/><label className="btn btn-subtle" htmlFor="files"><CloudUpload size={15}/>Selecionar arquivos</label></div>{files.length > 0 && <div className="upload-selection">{files.map((f)=><div key={`${f.name}-${f.size}`} className="meta-row"><FileImage size={14}/>{f.name}<span className="mono">{(f.size/1024/1024).toFixed(1)} MB · {busy ? "enviando" : "pronto"}</span></div>)}<label className="deidentified-confirmation"><input type="checkbox" checked={confirmed} onChange={(e)=>setConfirmed(e.target.checked)}/> Confirmo que estas imagens são de teste ou estão desidentificadas.</label><button className="btn btn-primary" onClick={submit} disabled={busy || !confirmed}>{busy ? "Enviando…" : "Enviar estudos"}</button></div>}{result?.errors?.length > 0 && <div className="notice upload-result"><strong>{result.studies?.length || 0} aceito(s); {result.errors.length} rejeitado(s).</strong>{result.errors.map((item)=><div key={item.filename} className="meta-row"><span>{item.filename}</span><span>{item.error}</span></div>)}<button className="btn btn-subtle" onClick={onDone}>Voltar à worklist</button></div>}{error && <div className="error-box upload-error">{error}</div>}</div>;
}
function StudyCard({ study }) { return <Link href={`/studies/${study.id}`} className="study-card"><Thumb url={study.thumbnail_url}/><div className="study-primary"><strong>{study.patient_id || "Não identificado"}</strong><div className="meta-row">{study.description || "Radiografia de tórax"} · {study.modality || "XR"} {study.view_position ? `· ${study.view_position}` : ""}</div></div><div className="study-cell"><b>{displayAge(study.patient_age)} / {study.patient_sex || "—"}</b><span className="mono">{study.study_date || "Sem data"}</span></div><div className="study-cell"><b>{study.source || "Upload manual"}</b><span>{study.validation_reason || study.validation_state || "Validação pendente"}</span></div><div className="findings">{study.top_findings?.length ? study.top_findings.slice(0,2).map((finding,index)=><span className="finding" key={`${finding.pathology}-${index}`}>{finding.pathology} {Number(finding.normalized_score).toFixed(2)}</span>) : <span className="finding">Aguardando modelo</span>}</div><Status value={study.status}/><ChevronRight size={15} color="#6f90aa"/></Link>; }
function Worklist() {
  const { access } = useAuth();
  const searchParams = useSearch();
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState(()=>new URLSearchParams(searchParams).get("status") || "");
  const [filtersOpen, setFiltersOpen] = useState(true);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const load = useCallback(async () => { try { setError(""); setData(await listStudies({search:query,status})); } catch(e) { setError(e.message); } }, [query,status]);
  useEffect(()=>{ setStatus(new URLSearchParams(searchParams).get("status") || ""); },[searchParams]); useEffect(()=>{ load(); },[load]); useEffect(()=>{ if (!data?.items?.some(s=>!["completed","needs_review","rejected","error"].includes(s.status))) return; const timer=setInterval(load,4000); return()=>clearInterval(timer); },[data,load]);
  const counts = data?.counts || {};
  const items = data?.items || [];
  const processingCount = (counts.received || 0) + (counts.validating || 0) + (counts.queued || 0) + (counts.processing || 0);
  const completedCount = counts.completed || 0;
  const attentionCount = (counts.needs_review || 0) + (counts.rejected || 0);
  const errorCount = counts.error || 0;
  const totalCount = Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0);
  const completedRate = totalCount ? Math.round((completedCount / totalCount) * 100) : 0;
  const attentionRate = totalCount ? Math.round((attentionCount / totalCount) * 100) : 0;
  const hasFilters = Boolean(query || status);
  const resetFilters = () => { setQuery(""); setStatus(""); };
  const summaryBackground = `conic-gradient(var(--teal) 0 ${completedRate}%, var(--amber) ${completedRate}% ${Math.min(100, completedRate + attentionRate)}%, #263552 ${Math.min(100, completedRate + attentionRate)}% 100%)`;

  return <AppShell>
    <div className="page-heading">
      <div><div className="eyebrow">CXR / sistema de controle de qualidade</div><h1>Chester AI Worklist</h1><p className="page-subtitle">Pipeline de triagem e análise para estudos torácicos desidentificados.</p></div>
      <div className="heading-actions"><div className="live-indicator"><i/> sincronizado</div>{canAccess(access, "upload") && <button className="btn btn-primary" onClick={()=>setUploadOpen(!uploadOpen)}><CloudUpload size={15}/>{uploadOpen ? "Fechar envio" : "Analisar estudo"}</button>}</div>
    </div>
    {uploadOpen && <UploadDialog onDone={()=>{setUploadOpen(false);load()}}/>}
    <div className="stats stats-five">
      <div className="stat"><ClipboardList size={16}/><b>{data ? totalCount : "—"}</b><span>Total de estudos</span></div>
      <div className="stat"><Activity size={16}/><b>{data ? processingCount : "—"}</b><span>Em processamento</span></div>
      <div className="stat"><Check size={16}/><b>{data ? completedCount : "—"}</b><span>Concluídos</span></div>
      <div className="stat"><AlertTriangle size={16}/><b>{data ? attentionCount : "—"}</b><span>Revisão / rejeitados</span></div>
      <div className="stat"><XCircle size={16}/><b>{data ? errorCount : "—"}</b><span>Com erro</span></div>
    </div>
    <section className="quality-summary" aria-label="Resumo da fila">
      <div className="summary-title"><span className="summary-icon"><Activity size={15}/></span><div><strong>Distribuição operacional</strong><small>Status global dos estudos</small></div></div>
      <div className="summary-chart"><div className="status-ring" style={{background:summaryBackground}}><span>{completedRate}%<small>concluídos</small></span></div><div className="summary-legend"><span><i className="legend-completed"/>Concluídos <b>{completedCount}</b></span><span><i className="legend-attention"/>Revisão/rejeitados <b>{attentionCount}</b></span><span><i className="legend-queue"/>Fila/processamento <b>{processingCount}</b></span></div></div>
      <div className="summary-meta"><span>{items.length}</span><small>estudos exibidos</small></div>
    </section>
    <section className={`filter-panel ${filtersOpen ? "is-open" : ""}`}>
      <button className="filter-heading" onClick={()=>setFiltersOpen(value=>!value)} aria-expanded={filtersOpen}><span><Filter size={15}/>Filtros da worklist</span><ChevronRight size={15}/></button>
      {filtersOpen && <div className="filter-grid">
        <label className="filter-search"><span>Busca</span><div className="search"><Search size={15}/><input className="input" placeholder="Paciente ou descrição…" value={query} onChange={event=>setQuery(event.target.value)}/></div></label>
        <label><span>Status</span><select className="select" value={status} onChange={event=>setStatus(event.target.value)}><option value="">Todos</option>{Object.entries(statusLabels).filter(([value])=>value !== "needs_review" || canAccess(access, "review")).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
        <button className="btn btn-subtle filter-reset" onClick={resetFilters} disabled={!hasFilters}><RotateCcw size={14}/>Limpar filtros</button>
      </div>}
    </section>
    <div className="list-summary"><span>{items.length} de {data?.total ?? 0} estudos nesta seleção</span>{hasFilters && <small>KPIs mantêm o panorama global</small>}</div>
    {error ? <div className="error-box"><AlertTriangle size={22}/><h3>Worklist indisponível</h3><p>{error}</p><button className="btn btn-subtle retry-button" onClick={load}>Tentar novamente</button></div> : !data ? <div className="study-list"><div className="skeleton"/><div className="skeleton"/><div className="skeleton"/></div> : items.length ? <><div className="worklist-head"><span>Imagem</span><span>Estudo / paciente</span><span>Demografia</span><span>Origem / validação</span><span>Achados principais</span><span>Status</span><span/></div><div className="study-list">{items.map(s=><StudyCard key={s.id} study={s}/>)}</div></> : <div className="empty"><ClipboardList size={28}/><h3>Nenhum estudo nesta seleção</h3><p>Tente outros filtros ou envie uma radiografia desidentificada.</p></div>}
  </AppShell>;
}
function SettingRow({ label, value, mono = false }) { return <div className="setting-row"><dt>{label}</dt><dd className={mono ? "mono" : ""}>{value || "Não configurado"}</dd></div>; }
function ConnectionStatus({ status, label }) { return <span className={`connection-status connection-status-${status}`}><i/>{label}</span>; }
function SettingsCard({ children, className = "" }) { return <section className={`settings-card ${className}`}>{children}</section>; }
function Settings() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => { try { setError(""); setData(await getDicomwebSettings()); } catch (e) { setError(e.message); } }, []);
  useEffect(() => { load(); }, [load]);
  const scp = data?.scp;
  const stow = data?.stow_rs;
  return <AppShell>
    <div className="page-heading settings-page-heading">
      <div><div className="eyebrow">Sistema / conectividade</div><h1>Configuração DICOMweb</h1><p className="page-subtitle">Endpoints de pesquisa e entrada para dispositivos autorizados.</p></div>
      <div className="settings-heading-mark"><RadioTower size={18}/><span>somente leitura</span></div>
    </div>
    {error ? <div className="error-box settings-error"><Wifi size={22}/><h3>Configuração indisponível</h3><p>{error}</p><button className="btn btn-subtle retry-button" onClick={load}>Tentar novamente</button></div> : !data ? <div className="settings-loading"><div className="skeleton"/><div className="skeleton"/><div className="skeleton"/></div> : <div className="settings-grid">
      <SettingsCard className="settings-card-scp">
        <div className="settings-card-header"><div><div className="settings-kicker settings-kicker-green"><span className="settings-dot"/>DICOM SCP <span className="settings-kicker-muted">(C-STORE)</span></div><h2>Gateway de entrada</h2></div><ConnectionStatus status={scp.status} label={scp.status_label}/></div>
        <p className="settings-card-description">Recebe estudos de PACS ou modalidades através do gateway externo e encaminha cada instância para o STOW-RS.</p>
        <dl className="settings-rows"><SettingRow label="AE Title" value={scp.ae_title} mono/><SettingRow label="Porta" value={scp.port} mono/><SettingRow label="Serviços" value={scp.services.join(" / ")} mono/><SettingRow label="Transporte" value={scp.transport}/></dl>
        <div className="settings-inset"><div className="settings-inset-title"><Server size={15}/>Gateway externo</div><dl className="settings-rows"><SettingRow label="Endereço" value={scp.host} mono/><SettingRow label="Destino STOW-RS" value={scp.gateway_target} mono/><SettingRow label="Owner da worklist" value={scp.owner_configured ? "Definido" : "Pendente"}/></dl></div>
        <p className="settings-note settings-note-green"><RadioTower size={14}/>C-FIND e C-MOVE não fazem parte deste gateway de armazenamento.</p>
      </SettingsCard>
      <SettingsCard className="settings-card-stow">
        <div className="settings-card-header"><div><div className="settings-kicker settings-kicker-blue"><span className="settings-dot"/>DICOMweb STOW-RS <span className="settings-kicker-muted">(Entrada / HTTP)</span></div><h2>Endpoint da worklist</h2></div><ConnectionStatus status={stow.status} label={stow.status_label}/></div>
        <p className="settings-card-description">Endpoint para envio de instâncias DICOM via requisições multipart autenticadas.</p>
        <dl className="settings-rows"><SettingRow label="URL" value={stow.url} mono/><SettingRow label="AE Title" value={stow.ae_title}/><SettingRow label="Criptografia" value={stow.https ? "HTTPS" : "HTTP · ambiente local"} mono/><SettingRow label="Serviço" value={stow.services.join(" / ")} mono/></dl>
        <div className="settings-inset"><div className="settings-inset-title"><Globe2 size={15}/>Detalhes do endpoint</div><dl className="settings-rows"><SettingRow label="Hostname" value={stow.hostname} mono/><SettingRow label="Path" value={stow.path} mono/><SettingRow label="Porta" value={stow.port} mono/><SettingRow label="HTTPS" value={stow.https ? "Ativo" : "Não ativo"} /></dl></div>
        <p className="settings-note settings-note-amber"><LockKeyhole size={14}/>{stow.request_limit}.</p>
      </SettingsCard>
      <SettingsCard className="settings-card-security">
        <div className="settings-card-header"><div><div className="settings-kicker settings-kicker-purple"><span className="settings-dot"/>Credenciais para dispositivos</div><h2>Acesso de ingestão</h2></div><ConnectionStatus status={data.service_token_configured ? "configured" : "not_configured"} label={data.service_token_configured ? "Configurado" : "Não configurado"}/></div>
        <div className="security-content"><div><p className="settings-card-description">Dispositivos que não usam sessão do navegador devem enviar uma credencial de serviço no cabeçalho da requisição.</p><div className="credential-methods"><span><LockKeyhole size={13}/>X-DICOM-Ingest-Key</span><span><LockKeyhole size={13}/>Authorization: Bearer</span></div></div><div className="security-safe"><ShieldCheck size={18}/><strong>Valor protegido</strong><span>O segredo nunca é exibido nesta tela.</span></div></div>
        <p className="settings-note settings-note-purple"><Wifi size={14}/>Use somente com dados de teste ou desidentificados. A porta DICOM SCP não deve ser exposta à internet.</p>
      </SettingsCard>
      <div className="settings-footer-note"><Wifi size={14}/><span>Conectividade informativa · os valores refletem a configuração efetiva deste ambiente.</span></div>
    </div>}
  </AppShell>;
}
function Detail() {
  const { access } = useAuth();
  const { id } = useParams(); const [study,setStudy]=useState(null); const [error,setError]=useState(""); const [busy,setBusy]=useState(false);
  const load=useCallback(async()=>{try{setStudy(await getStudy(id))}catch(e){setError(e.message)}},[id]); useEffect(()=>{load()},[load]);
  const action=async(fn)=>{setBusy(true);try{await fn();await load()}catch(e){setError(e.message)}finally{setBusy(false)}};
  if(error) return <AppShell><div className="error-box"><AlertTriangle size={22}/><h3>Could not load study</h3><p>{error}</p><button className="btn btn-subtle" style={{marginTop:15}} onClick={load}>Try again</button></div></AppShell>;
  if(!study) return <AppShell><div className="detail-grid"><div className="skeleton"/><div className="skeleton"/></div></AppShell>;
  const results=latestResultRows(study.results || []); const chartData=results.map((r,i)=>({name:r.pathology || `Finding ${i+1}`, score:Number(r.normalized_score ?? 0)})); const canReview=study.status==="needs_review" && canAccess(access, "review") && ["admin","radiologist","validador_radiologista"].includes(access?.role);
  return <AppShell><Link href="/worklist" className="eyebrow" style={{display:"inline-flex",gap:7,alignItems:"center",marginBottom:20}}><ArrowLeft size={14}/> Back to worklist</Link><div className="detail-header"><div><h1>{study.description || "Chest radiograph"}</h1><div className="meta-row" style={{marginTop:10}}><span className="mono">{study.id}</span><Status value={study.status}/><span>{study.source || "Manual upload"}</span></div></div><div className="detail-actions">{study.status==="error" && <button className="btn btn-subtle" disabled={busy} onClick={()=>action(()=>retryStudy(id))}>Retry analysis</button>}{canReview && <><button className="btn btn-accent" disabled={busy} onClick={()=>action(()=>reviewStudy(id,"approve"))}><Check size={15}/> Approve for analysis</button><button className="btn btn-danger" disabled={busy} onClick={()=>action(()=>reviewStudy(id,"reject"))}><X size={15}/> Reject study</button></>}</div></div>{study.status==="error" && <div className="notice" style={{marginBottom:14}}><strong>Inference error.</strong> {study.error_message || "Analysis could not be completed. Retry when ready."}</div>}{study.status==="needs_review" && <div className="notice" style={{marginBottom:14}}><strong>Review required.</strong> Confirm this study before analysis proceeds.</div>}<div className="detail-grid"><div><div className="panel"><div className="panel-title"><h3>Study image</h3><span>{study.modality || "XR"}</span></div><div style={{background:"#263b40",borderRadius:9,minHeight:315,display:"grid",placeItems:"center"}}><Thumb url={study.thumbnail_url}/></div></div><div className="panel"><div className="panel-title"><h3>Exam metadata</h3><span>source record</span></div><dl className="metadata"><div><dt>Patient</dt><dd>{study.patient_id || "Unidentified"}</dd></div><div><dt>Age / sex</dt><dd>{displayAge(study.patient_age)} / {study.patient_sex || "—"}</dd></div><div><dt>Study date</dt><dd>{study.study_date || "—"}</dd></div><div><dt>View position</dt><dd>{study.view_position || "—"}</dd></div><div><dt>Model version</dt><dd>{study.model_version || "—"}</dd></div><div><dt>Preprocessing</dt><dd>{study.preprocessing_version || "—"}</dd></div></dl></div></div><div><div className="panel"><div className="panel-title"><h3>AI findings</h3><span>{results.length} outputs · latest run</span></div><div className="notice"><strong>Research score, not probability.</strong> Raw model output and operating-point normalized scores are shown for research interpretation. Neither is a calibrated clinical probability.</div><div style={{overflowX:"auto",marginTop:15}}><table><thead><tr><th>Pathology</th><th>Raw output</th><th>Normalized research score</th><th>Threshold</th><th>Flag</th></tr></thead><tbody>{results.map((r,i)=><tr key={r.pathology || i}><td><b>{r.pathology || "Unnamed output"}</b></td><td className="mono">{Number(r.raw_score ?? 0).toFixed(4)}</td><td><div style={{display:"flex",alignItems:"center",gap:9}}><div className="bar"><i style={{width:`${Math.min(100,Number(r.normalized_score ?? 0)*100)}%`}}/></div><span className="mono">{Number(r.normalized_score ?? 0).toFixed(3)}</span></div></td><td className="mono">{Number(r.threshold ?? 0).toFixed(3)}</td><td>{r.above_threshold ? <span className="pill pill-needs_review">above</span> : <span className="pill pill-completed">below</span>}</td></tr>)}</tbody></table></div></div>{results.length>0 && <div className="panel"><div className="panel-title"><h3>Score distribution</h3><span>normalized research score</span></div><div className="chart"><ResponsiveContainer width="100%" height="100%"><AreaChart data={chartData}><CartesianGrid stroke="#e1e6df" vertical={false}/><XAxis dataKey="name" tick={{fontSize:10}} angle={-20} height={55} interval={0}/><YAxis domain={[0,1]} tick={{fontSize:10}}/><Tooltip/><Area type="monotone" dataKey="score" stroke="#28796d" fill="#d6f2e7" strokeWidth={2}/></AreaChart></ResponsiveContainer></div></div>}</div></div></AppShell>;
}
function AuthPage() {
  const [, setLocation] = useLocation();
  const { setAccess } = useAuth();
  const [step, setStep] = useState("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const sendCode = async () => {
    const normalized = email.trim().toLowerCase();
    if (!normalized || busy) return;
    setBusy(true); setError("");
    try {
      await requestAccessCode(normalized);
      setEmail(normalized); setCode(""); setStep("code");
    } catch (requestError) {
      setError(requestError.message.replace(/^"|"$/g, ""));
    } finally { setBusy(false); }
  };
  const submitEmail = (event) => { event.preventDefault(); sendCode(); };
  const submitCode = async (event) => {
    event.preventDefault();
    if (busy || code.length !== 6) return;
    setBusy(true); setError("");
    try {
      const access = await verifyAccessCode(email, code);
      setAccess(access);
      setLocation("/worklist", { replace: true });
    } catch (requestError) {
      setError(requestError.message.replace(/^"|"$/g, ""));
    } finally { setBusy(false); }
  };

  return <div className="auth-shell"><div className="auth-wrap"><div className="auth-mark"><Brand light/><p>Console de pesquisa · radiografia torácica</p></div><section className="auth-card" aria-labelledby="auth-title">
    <div className="auth-card-mark"><img src={`${basePath}/logo.svg`} alt="" /></div>
    {step === "email" ? <><h1 id="auth-title">Entrar no Chester AI</h1><p className="auth-card-subtitle">Informe seu email para receber o código de acesso.</p><form onSubmit={submitEmail}><label className="auth-field"><span>Seu e-mail</span><input className="auth-input" type="email" autoComplete="email" autoFocus value={email} onChange={(event) => setEmail(event.target.value)} placeholder="Digite o endereço de e-mail" required /></label>{error && <p className="auth-error" role="alert"><XCircle size={14}/>{error}</p>}<button className="auth-submit" type="submit" disabled={busy}>{busy ? "Enviando…" : "Continuar"} <ChevronRight size={15}/></button></form></> : <><h1 id="auth-title">Verifique seu email</h1><p className="auth-card-subtitle">Enviamos um código de 6 dígitos para <strong>{email}</strong>.</p><form onSubmit={submitCode}><label className="auth-field"><span>Código de verificação</span><input className="auth-input auth-code-input" type="text" inputMode="numeric" autoComplete="one-time-code" autoFocus value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))} placeholder="000000" required /></label>{error && <p className="auth-error" role="alert"><XCircle size={14}/>{error}</p>}<button className="auth-submit" type="submit" disabled={busy || code.length < 6}>{busy ? "Validando…" : "Confirmar acesso"} <ChevronRight size={15}/></button></form><button className="auth-back" type="button" disabled={busy} onClick={sendCode}>Reenviar código</button><button className="auth-back" type="button" disabled={busy} onClick={() => { setStep("email"); setCode(""); setError(""); }}>Usar outro email</button></>}
  </section><div className="auth-research-note"><ShieldCheck size={14}/><span>Pesquisa somente · dados desidentificados</span></div><p className="auth-restricted">Acesso restrito a usuários autorizados</p></div></div>;
}

function AccessControl() {
  const [metadata, setMetadata] = useState({ roles: [], pages: [] });
  const [emails, setEmails] = useState([]); const [domains, setDomains] = useState([]); const [audit, setAudit] = useState([]);
  const [email, setEmail] = useState(""); const [domain, setDomain] = useState(""); const [role, setRole] = useState("technician"); const [pages, setPages] = useState(""); const [error, setError] = useState("");
  const load = useCallback(async () => { try { setError(""); const [meta, nextEmails, nextDomains, nextAudit] = await Promise.all([getAccessMetadata(), listAllowedEmails(), listAllowedDomains(), listAccessAudit()]); setMetadata(meta); setEmails(nextEmails); setDomains(nextDomains); setAudit(nextAudit); } catch (e) { setError(e.message); } }, []);
  useEffect(() => { load(); }, [load]);
  const selectedPages = () => pages.split(",").map((item) => item.trim()).filter(Boolean);
  const addEmail = async (event) => { event.preventDefault(); try { await createAllowedEmail({ email, role, allowed_pages: selectedPages() }); setEmail(""); setPages(""); await load(); } catch (e) { setError(e.message); } };
  const addDomain = async (event) => { event.preventDefault(); try { await createAllowedDomain({ domain, role, allowed_pages: selectedPages() }); setDomain(""); setPages(""); await load(); } catch (e) { setError(e.message); } };
  const editPages = async (item, kind) => { const next = window.prompt("Páginas permitidas, separadas por vírgula. Deixe vazio para todas.", (item.allowed_pages || []).join(", ")); if (next === null) return; try { const fn = kind === "email" ? updateAllowedEmail : updateAllowedDomain; await fn(item.id, { allowed_pages: next.split(",").map((value) => value.trim()).filter(Boolean) }); await load(); } catch (e) { setError(e.message); } };
  return <AppShell><div className="page-heading"><div><div className="eyebrow">Sistema / segurança</div><h1>Controle de acesso</h1><p className="page-subtitle">E-mails, domínios, papéis e páginas autorizadas.</p></div></div>{error && <div className="error-box">{error}</div>}<section className="panel"><div className="panel-title"><h3>Novo acesso por email</h3></div><form className="toolbar" onSubmit={addEmail}><input className="input" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="email@empresa.com" required/><select className="select" value={role} onChange={(e) => setRole(e.target.value)}>{metadata.roles.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select><input className="input" value={pages} onChange={(e) => setPages(e.target.value)} placeholder="Páginas: worklist, upload…" /><button className="btn btn-primary">Adicionar email</button></form></section><section className="panel"><div className="panel-title"><h3>Novo acesso por domínio</h3></div><form className="toolbar" onSubmit={addDomain}><input className="input" value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="empresa.com" required/><select className="select" value={role} onChange={(e) => setRole(e.target.value)}>{metadata.roles.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select><button className="btn btn-primary">Adicionar domínio</button></form></section><section className="panel"><div className="panel-title"><h3>E-mails autorizados</h3></div><table><thead><tr><th>Email</th><th>Papel</th><th>Páginas</th><th>Status</th><th/></tr></thead><tbody>{emails.map((item) => <tr key={item.id}><td>{item.email}{item.is_env_admin && <small className="mono"> · ambiente</small>}</td><td>{item.role_label}</td><td>{item.allowed_pages?.join(", ") || "Todas"}</td><td>{item.active ? "Ativo" : "Inativo"}</td><td>{!item.is_env_admin && <><button className="btn btn-subtle" onClick={() => editPages(item, "email")}>Páginas</button> <button className="btn btn-danger" onClick={async () => { if (window.confirm("Remover este email?")) { try { await deleteAllowedEmail(item.id); await load(); } catch (e) { setError(e.message); } } }}>Remover</button></>}</td></tr>)}</tbody></table></section><section className="panel"><div className="panel-title"><h3>Domínios autorizados</h3></div><table><thead><tr><th>Domínio</th><th>Papel</th><th>Páginas</th><th>Status</th><th/></tr></thead><tbody>{domains.map((item) => <tr key={item.id}><td>{item.domain}</td><td>{item.role_label}</td><td>{item.allowed_pages?.join(", ") || "Todas"}</td><td>{item.active ? "Ativo" : "Inativo"}</td><td><button className="btn btn-subtle" onClick={() => editPages(item, "domain")}>Páginas</button> <button className="btn btn-danger" onClick={async () => { if (window.confirm("Remover este domínio?")) { try { await deleteAllowedDomain(item.id); await load(); } catch (e) { setError(e.message); } } }}>Remover</button></td></tr>)}</tbody></table></section><section className="panel"><div className="panel-title"><h3>Auditoria</h3></div><table><thead><tr><th>Quando</th><th>Ator</th><th>Ação</th><th>Alvo</th></tr></thead><tbody>{audit.slice(0, 20).map((item) => <tr key={item.id}><td className="mono">{new Date(item.created_at).toLocaleString("pt-BR")}</td><td>{item.actor_email}</td><td>{item.action}</td><td>{item.target_key}</td></tr>)}</tbody></table></section></AppShell>;
}

function HomeRoute(){ const { access, loading } = useAuth(); if (loading) return <div className="auth-shell"><div className="auth-loading">Verificando acesso…</div></div>; return <Redirect to={access ? "/worklist" : "/sign-in"}/>; }
function AccessDenied() { const { signOut } = useAuth(); return <div className="auth-shell"><div className="access-denied"><ShieldCheck size={28}/><h1>Acesso restrito</h1><p>Seu perfil não tem acesso a esta página.</p><button className="btn btn-primary" onClick={signOut}>Sair</button></div></div>; }
function Protected({ page, children }) { const { access, loading } = useAuth(); if (loading) return <div className="auth-shell"><div className="auth-loading">Verificando acesso…</div></div>; if (!access) return <Redirect to="/sign-in"/>; if (!canAccess(access, page)) { const fallback = ["worklist", "settings", "access-control"].find((candidate) => canAccess(access, candidate)); return fallback ? <Redirect to={`/${fallback === "worklist" ? "worklist" : fallback}`}/> : <AccessDenied/>; } return children; }
function Routes(){ const { access } = useAuth(); return <Switch><Route path="/" component={HomeRoute}/><Route path="/sign-in/*?">{access ? <Redirect to="/worklist"/> : <AuthPage/>}</Route><Route path="/worklist"><Protected page="worklist"><Worklist/></Protected></Route><Route path="/settings"><Protected page="settings"><Settings/></Protected></Route><Route path="/access-control"><Protected page="access-control"><AccessControl/></Protected></Route><Route path="/studies/:id"><Protected page="study-detail"><Detail/></Protected></Route><Route><Redirect to="/"/></Route></Switch>; }
function AppRoot() { const [access, setAccess] = useState(null); const [loading, setLoading] = useState(true); const refresh = useCallback(async () => { if (!getSessionToken()) { setAccess(null); setLoading(false); return; } try { const result = await validateSession(); setAccess(result.access); } catch { setAccess(null); } finally { setLoading(false); } }, []); useEffect(() => { refresh(); }, [refresh]); const signOut = useCallback(async () => { await logout(); setAccess(null); }, []); const value = useMemo(() => ({ access, setAccess, loading, signOut }), [access, loading, signOut]); return <AuthContext.Provider value={value}><WouterRouter base={basePath}><Routes/></WouterRouter></AuthContext.Provider>; }
export default function App(){ return <AppRoot/>; }