import hashlib, json, logging, os, shutil, sqlite3, subprocess, sys, tempfile, uuid, zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
import xml.etree.ElementTree as ET

STATES=("discovered","queued","running","staged","verified","committed","failed","retrying","blocked")
def now(): return datetime.now(timezone.utc).isoformat()
def later(seconds): return (datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat()
def sha256(data): return hashlib.sha256(data).hexdigest()
def resolve_executable(value, base):
    """Resolve absolute/relative paths while allowing cloud runners' PATH tools."""
    candidate=Path(value)
    if candidate.is_file(): return candidate
    if not candidate.is_absolute():
        local=Path(base)/candidate
        if local.is_file(): return local
    found=shutil.which(str(value))
    if found: return Path(found)
    # setup-python guarantees the interpreter running this module; use it
    # when the runner lacks a separate python/python3 PATH alias.
    if str(value) in {"python", "python3"} and Path(sys.executable).is_file():
        return Path(sys.executable)
    return candidate

SCHEMA="""
CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY,started_at TEXT,finished_at TEXT,status TEXT,dry_run INTEGER,config_hash TEXT,error TEXT);
CREATE TABLE IF NOT EXISTS sources(source_id TEXT PRIMARY KEY,adapter TEXT,location TEXT,enabled INTEGER,created_at TEXT,updated_at TEXT);
CREATE TABLE IF NOT EXISTS source_versions(source_id TEXT,version_id TEXT,content_hash TEXT,size_bytes INTEGER,observed_at TEXT,metadata_json TEXT,is_current INTEGER,PRIMARY KEY(source_id,version_id));
CREATE TABLE IF NOT EXISTS work_items(work_item_id TEXT PRIMARY KEY,run_id TEXT,source_id TEXT,source_version_id TEXT,pipeline TEXT,state TEXT,idempotency_key TEXT UNIQUE,attempt_count INTEGER,staging_path TEXT,committed_path TEXT,created_at TEXT,updated_at TEXT,last_error TEXT,available_at TEXT,locked_by TEXT,locked_until TEXT,lease_token TEXT,next_attempt_at TEXT);
CREATE TABLE IF NOT EXISTS attempts(attempt_id TEXT PRIMARY KEY,work_item_id TEXT,attempt_no INTEGER,started_at TEXT,finished_at TEXT,status TEXT,error TEXT);
CREATE TABLE IF NOT EXISTS artifacts(artifact_id TEXT PRIMARY KEY,work_item_id TEXT,stage_path TEXT,committed_path TEXT,sha256 TEXT,size_bytes INTEGER,content_type TEXT,expected_json TEXT,verified_at TEXT);
CREATE TABLE IF NOT EXISTS failures(failure_id TEXT PRIMARY KEY,run_id TEXT,work_item_id TEXT,attempt_id TEXT,error_code TEXT,message TEXT,retryable INTEGER,created_at TEXT);
"""
class LocalFileSource:
    def __init__(self,spec,base): self.spec,self.base=spec,base
    def read(self):
        p=Path(self.spec["path"]); p=p if p.is_absolute() else self.base/p; data=p.read_bytes(); return p,data,sha256(data)
class PythonJsonSource:
    def __init__(self,spec,base): self.spec,self.base=spec,base
    def read(self):
        python=resolve_executable(self.spec["python"],self.base)
        entry=Path(self.spec["entrypoint"]); entry=entry if entry.is_absolute() else self.base/entry
        if not python.is_file(): raise RuntimeError(f"executable_missing: source python {python}")
        if not entry.is_file(): raise RuntimeError(f"runner_missing: source entrypoint {entry}")
        acquisition=self._acquisition_root(); acquisition.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="acquire-",suffix=".json",dir=acquisition,delete=False) as handle: target=Path(handle.name)
        try:
            args=[str(x) for x in self.spec.get("args", [])]
            result=subprocess.run([str(python),str(entry),*args,"--output",str(target)],cwd=str(self.base),check=False,capture_output=True,text=True,timeout=int(self.spec.get("timeout_seconds",180)))
            if result.returncode: raise RuntimeError(f"nonzero_exit: source acquisition stderr={result.stderr[-1500:]} stdout={result.stdout[-1500:]}")
            data=target.read_bytes()
            if not data: raise RuntimeError("source_empty: acquisition output")
            if self.spec.get("reject_partial") and json.loads(data).get("run",{}).get("status") != "Complete":
                raise RuntimeError("source_partial: one or more configured agency routes were quarantined")
            data=self._with_deterministic_changes(data)
            target.write_bytes(data)
            content_hash=sha256(data); final=acquisition/f"{content_hash}.json"
            if not final.exists(): os.replace(target,final)
            else: target.unlink(missing_ok=True)
            return final,data,content_hash
        except Exception:
            target.unlink(missing_ok=True); raise
    def _acquisition_root(self): return self._path(self.spec.get("acquisition_root","state/acquisitions"))
    def _path(self,p): x=Path(p); return x if x.is_absolute() else self.base/x
    def _with_deterministic_changes(self, data):
        previous_path=self.spec.get("previous_snapshot")
        if not previous_path: return data
        path=Path(previous_path); path=path if path.is_absolute() else self.base/path
        if not path.is_file(): return data
        current=json.loads(data); previous=json.loads(path.read_bytes()); old={str(x.get("opportunity_id")):x for x in previous.get("opportunities",[]) if x.get("opportunity_id")}
        changes=[]; current_ids=set()
        for item in current.get("opportunities",[]):
            oid=str(item.get("opportunity_id","")); current_ids.add(oid); prior=old.get(oid)
            if prior is None: kind="New Opportunity"
            elif any(item.get(k)!=prior.get(k) for k in ("project_name","description","posted_date","due_date","status","priority","opportunity_url")): kind="Updated"
            else: continue
            changes.append({"agency":item.get("agency"),"source_name":item.get("source_name"),"opportunity_id":oid,"project_name":item.get("project_name"),"change_type":kind,"source_url":item.get("source_url"),"status":item.get("status"),"priority":item.get("priority"),"new_value":item.get("status","")})
        for oid,prior in old.items():
            if oid not in current_ids: changes.append({"agency":prior.get("agency"),"source_name":prior.get("source_name"),"opportunity_id":oid,"project_name":prior.get("project_name",""),"change_type":"Removed from Active Listing","source_url":prior.get("source_url",""),"status":"Closed","priority":prior.get("priority","Low"),"new_value":"Removed"})
        current["changes"]=changes
        return json.dumps(current,ensure_ascii=False,sort_keys=True,indent=2).encode()+b"\n"
class MockPipeline:
    def __init__(self,spec): self.spec=spec
    def run(self,item,data,destination,attempt_no):
        if attempt_no<=int(self.spec.get("fail_first_attempts",0)): raise RuntimeError("injected transient mock failure")
        out={"schema_version":"mock-result-v1","source_id":item["source_id"],"source_version":item["source_version_id"],"source_sha256":item["source_hash"],"pipeline":item["pipeline"],"byte_count":len(data),"payload_sha256":sha256(data)}
        destination.parent.mkdir(parents=True,exist_ok=True); destination.write_text(json.dumps(out,sort_keys=True,indent=2)+"\n",encoding="utf-8")

class Orchestrator:
    def __init__(self,config,config_path):
        self.config,self.config_path=config,Path(config_path).resolve(); self.base=self.config_path.parent
        self.dbpath=self._path(config["database_path"]); self.stage=self._path(config["staging_root"]); self.commit=self._path(config["commit_root"]); self.logpath=self._path(config["log_path"]); self.lease_seconds=int(config.get("lease_seconds",60)); self.batch_size=int(config.get("batch_size",100))
        self.logpath.parent.mkdir(parents=True,exist_ok=True); self.logger=logging.getLogger("orchestrator")
        for old in list(self.logger.handlers): old.close(); self.logger.removeHandler(old)
        h=logging.FileHandler(self.logpath,encoding="utf-8"); h.setFormatter(logging.Formatter("%(message)s")); self.logger.addHandler(h); self.logger.setLevel(logging.INFO)
    def _path(self,p): x=Path(p); return x if x.is_absolute() else self.base/x
    @classmethod
    def from_file(cls,path): p=Path(path).resolve(); return cls(json.loads(p.read_text(encoding="utf-8")),p)
    def event(self,**kw): self.logger.info(json.dumps({"ts":now(),**kw},sort_keys=True))
    def conn(self):
        self.dbpath.parent.mkdir(parents=True,exist_ok=True); c=sqlite3.connect(self.dbpath,timeout=30); c.row_factory=sqlite3.Row; c.executescript(SCHEMA)
        existing={r[1] for r in c.execute("PRAGMA table_info(work_items)")}; additions={"available_at":"TEXT","locked_by":"TEXT","locked_until":"TEXT","lease_token":"TEXT","next_attempt_at":"TEXT"}
        for name,typ in additions.items():
            if name not in existing: c.execute(f"ALTER TABLE work_items ADD COLUMN {name} {typ}")
        c.commit(); return c
    def verify_executables(self):
        for name,spec in self.config.get("executables",{}).items():
            if not spec.get("enabled",True): continue
            p=resolve_executable(spec["path"], self.base)
            if not p.is_file():
                if spec.get("required",True): raise RuntimeError(f"required executable missing: {name}: {p}")
                self.event(event="executable_unavailable",name=name,path=str(p)); continue
            try: subprocess.run([str(p),"--version"],check=True,capture_output=True,text=True,timeout=15)
            except Exception as e:
                if spec.get("required",True): raise RuntimeError(f"executable failed: {name}: {e}")
                self.event(event="executable_failed",name=name,error=str(e))
    def verify_imports(self):
        """Import-test packages in the interpreter that will execute each pipeline."""
        for name,spec in self.config.get("pipelines",{}).items():
            imports=spec.get("required_imports",[])
            if not imports: continue
            python=resolve_executable(spec.get("python",sys.executable), self.base)
            if not python.is_file(): raise RuntimeError(f"executable_missing: {name}: {python}")
            code="import " + ", ".join(imports)
            try: subprocess.run([str(python),"-c",code],check=True,capture_output=True,text=True,timeout=30)
            except Exception as e: raise RuntimeError(f"required_import_failed: {name}: {imports}: {e}") from e
    def verify_pipeline_tools(self):
        for name,spec in self.config.get("pipelines",{}).items():
            if spec.get("adapter") not in {"workbook", "workbook_python"}: continue
            node=resolve_executable(spec.get("node",""), self.base)
            script=Path(spec.get("script","")); script=script if script.is_absolute() else self._path(str(script))
            canonical=Path(spec.get("canonical_workbook","")); canonical=canonical if canonical.is_absolute() else self._path(str(canonical))
            if spec.get("adapter") == "workbook" and not node.is_file(): raise RuntimeError(f"executable_missing: {name} node: {node}")
            if not script.is_file(): raise RuntimeError(f"runner_missing: {name}: {script}")
            if not canonical.is_file(): raise RuntimeError(f"workbook_missing: {canonical}")
    def verify_writable(self, path, label):
        path=Path(path); path.mkdir(parents=True,exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile(prefix=".preflight-",dir=path,delete=True): pass
        except Exception as e: raise RuntimeError(f"not_writable: {label}: {path}: {e}") from e
    def verify_workbook(self, spec):
        if not spec: return
        path=Path(spec.get("path","")); path=path if path.is_absolute() else self._path(str(path))
        if not path.is_file(): raise RuntimeError(f"workbook_missing: {path}")
        try:
            with zipfile.ZipFile(path) as z:
                root=ET.fromstring(z.read("xl/workbook.xml")); ns={"x":"http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                sheets={s.attrib.get("name") for s in root.findall("x:sheets/x:sheet",ns)}
                missing=set(spec.get("required_sheets",[]))-sheets
                if missing: raise RuntimeError(f"workbook_missing_sheets: {sorted(missing)}")
                tables=[n for n in z.namelist() if n.startswith("xl/tables/") and n.endswith(".xml")]
                if len(tables)<int(spec.get("minimum_tables",0)): raise RuntimeError(f"workbook_missing_tables: found {len(tables)}")
        except RuntimeError: raise
        except Exception as e: raise RuntimeError(f"workbook_unreadable: {path}: {e}") from e
    def verify_proxy_policy(self):
        policy=self.config.get("preflight",{}).get("proxy_policy")
        if policy not in {"absent","absent_or_valid"}: return
        names=("HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","http_proxy","https_proxy","all_proxy")
        configured={n:os.environ.get(n) for n in names if os.environ.get(n)}
        if policy=="absent" and configured: raise RuntimeError(f"proxy_not_allowed: {sorted(configured)}")
        if policy=="absent_or_valid":
            for name,value in configured.items():
                if "127.0.0.1:9" in value or "localhost:9" in value: raise RuntimeError(f"proxy_unreachable: {name}={value}")
    def preflight(self):
        p=self.config.get("preflight",{})
        if not p.get("enabled",False): return
        self.verify_imports(); self.verify_pipeline_tools(); self.verify_proxy_policy()
        self.verify_writable(self.stage,"staging_root"); self.verify_writable(self.commit,"commit_root")
        c=self.conn(); c.execute("SELECT 1"); conflicts=c.execute("SELECT work_item_id FROM work_items WHERE state='running' AND locked_until IS NOT NULL AND locked_until>?",(now(),)).fetchall(); c.close()
        if conflicts: raise RuntimeError(f"conflicting_run_owns_work: {[r[0] for r in conflicts]}")
        for spec in self.config.get("sources",[]):
            if not spec.get("enabled",True): continue
            if spec.get("adapter")=="local_file":
                path,data,ch=LocalFileSource(spec,self.base).read()
                if not data: raise RuntimeError(f"source_empty: {path}")
                if ch!=sha256(data): raise RuntimeError(f"source_hash_failed: {path}")
            elif spec.get("adapter")=="python_json":
                py=Path(spec.get("python","")); py=py if py.is_absolute() else self._path(str(py)); ep=Path(spec.get("entrypoint","")); ep=ep if ep.is_absolute() else self._path(str(ep))
                if not py.is_file(): raise RuntimeError(f"executable_missing: source python {py}")
                if not ep.is_file(): raise RuntimeError(f"runner_missing: source entrypoint {ep}")
            else: raise RuntimeError(f"source_preflight_unsupported: {spec.get('adapter')}")
        self.verify_workbook(p.get("workbook"))
        self.event(event="preflight_passed",checks=["executables","imports","sources","hashes","proxy","directories","sqlite","workbook","ownership"])
    def recover_expired(self,c,run_id):
        rows=c.execute("SELECT * FROM work_items WHERE state IN ('running','staged','verified') AND locked_until IS NOT NULL AND locked_until<=?",(now(),)).fetchall(); maxa=int(self.config.get("max_attempts",3))
        for row in rows:
            retry=row["attempt_count"]<maxa; msg="lease expired; prior worker presumed interrupted"; aid="recovery-"+uuid.uuid4().hex
            c.execute("INSERT INTO failures VALUES(?,?,?,?,?,?,?,?)",("fail-"+uuid.uuid4().hex,run_id,row["work_item_id"],aid,"lease_expired",msg,int(retry),now()))
            state="retrying" if retry else "failed"; c.execute("UPDATE work_items SET state=?,available_at=?,next_attempt_at=?,locked_by=NULL,locked_until=NULL,lease_token=NULL,updated_at=?,last_error=? WHERE work_item_id=?",(state,now(),now() if retry else None,now(),msg,row["work_item_id"]))
            self.event(event="lease_recovered",run_id=run_id,work_item_id=row["work_item_id"],state=state)
        c.commit(); return len(rows)
    def discover(self,c,run_id):
        for spec in self.config.get("sources",[]):
            if not spec.get("enabled",True): continue
            adapter=PythonJsonSource(spec,self.base) if spec.get("adapter")=="python_json" else LocalFileSource(spec,self.base)
            path,data,ch=adapter.read(); sid=spec["source_id"]; pipeline=spec["pipeline"]; idem=f"{sid}:{ch}:{pipeline}"; wid="wi-"+hashlib.sha256(idem.encode()).hexdigest()[:24]
            c.execute("INSERT INTO sources VALUES(?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET updated_at=excluded.updated_at",(sid,spec["adapter"],str(path),1,now(),now())); c.execute("INSERT OR IGNORE INTO source_versions VALUES(?,?,?,?,?,?,?)",(sid,ch,ch,len(data),now(),json.dumps({"path":str(path),"adapter":spec["adapter"]}),1))
            c.execute("INSERT OR IGNORE INTO work_items(work_item_id,run_id,source_id,source_version_id,pipeline,state,idempotency_key,attempt_count,created_at,updated_at,available_at,next_attempt_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(wid,run_id,sid,ch,pipeline,"discovered",idem,0,now(),now(),now(),now()))
            c.execute("UPDATE work_items SET state='queued',available_at=COALESCE(available_at,?),next_attempt_at=COALESCE(next_attempt_at,?) WHERE work_item_id=? AND state IN ('discovered','retrying')",(now(),now(),wid))
        c.commit()
    def execute_pipeline(self, pipeline, item, data, destination, attempt):
        spec=self.config["pipelines"][pipeline]; adapter=spec.get("adapter","mock")
        if adapter=="mock": MockPipeline(spec).run(item,data,destination,attempt); return
        if adapter=="drawing_analyzer": self.execute_drawing_analyzer(spec,item,destination); return
        if adapter=="graphrag": self.execute_graphrag(spec,item,data,destination); return
        if adapter=="workbook": self.execute_workbook(spec,item,data,destination); return
        if adapter!="liteparse": raise RuntimeError(f"unsupported pipeline adapter: {adapter}")
        python=Path(spec["python"]); python=python if python.is_absolute() else self._path(spec["python"])
        runner=Path(spec.get("runner","liteparse_runner.py")); runner=runner if runner.is_absolute() else self.base/runner
        if not python.is_file(): raise RuntimeError(f"executable_missing: {python}")
        if not runner.is_file(): raise RuntimeError(f"runner_missing: {runner}")
        source=Path(next(s for s in self.config["sources"] if s["source_id"]==item["source_id"])["path"]); source=source if source.is_absolute() else self.base/source
        destination.parent.mkdir(parents=True,exist_ok=True); cmd=[str(python),str(runner),str(source),str(destination),"--source-id",item["source_id"],"--pipeline",pipeline]
        try: subprocess.run(cmd,cwd=str(self.base),check=True,capture_output=True,text=True,timeout=int(spec.get("timeout_seconds",900)))
        except subprocess.TimeoutExpired as e: raise RuntimeError(f"timeout: LiteParse exceeded {spec.get('timeout_seconds',900)} seconds") from e
        except subprocess.CalledProcessError as e: raise RuntimeError(f"nonzero_exit: LiteParse: {e.stderr[-1000:]}") from e
    def execute_drawing_analyzer(self,spec,item,destination):
        python=Path(spec["python"]); python=python if python.is_absolute() else self._path(spec["python"])
        entry=Path(spec["entrypoint"]); entry=entry if entry.is_absolute() else self.base/entry
        if not python.is_file(): raise RuntimeError(f"executable_missing: {python}")
        if not entry.is_file(): raise RuntimeError(f"runner_missing: {entry}")
        source=Path(next(s for s in self.config["sources"] if s["source_id"]==item["source_id"])["path"]); source=source if source.is_absolute() else self.base/source
        analysis=destination.parent/"drawing_analysis"; input_dir=analysis/"input"; output_root=analysis/"drawings_split"; input_dir.mkdir(parents=True,exist_ok=True); shutil.copy2(source,input_dir/source.name)
        batch_stage=analysis/"batch-staging"; batch_rollback=analysis/"batch-rollback"; batch_stage.mkdir(parents=True,exist_ok=True); batch_rollback.mkdir(parents=True,exist_ok=True)
        runner=Path(__file__).with_name("drawing_analyzer_runner.py"); cmd=[str(python),str(runner),str(entry),str(input_dir),str(output_root)]
        try: subprocess.run(cmd,cwd=str(entry.parent),check=True,capture_output=True,text=True,timeout=int(spec.get("timeout_seconds",3600)),env={**__import__('os').environ,"DRAWING_ANALYZER_BATCH_STAGE":str(batch_stage),"DRAWING_ANALYZER_BATCH_ROLLBACK":str(batch_rollback)})
        except subprocess.TimeoutExpired as e: raise RuntimeError(f"timeout: Drawing Analyzer exceeded {spec.get('timeout_seconds',3600)} seconds") from e
        except subprocess.CalledProcessError as e: raise RuntimeError(f"nonzero_exit: Drawing Analyzer: {e.stderr[-1500:]}") from e
        required=spec.get("expected_outputs",["batch_manifest.json","sheet_index.json","db/source_identity_report.json"]); missing=[p for p in required if not (analysis/p).is_file()]
        if missing: raise RuntimeError(f"missing_output: Drawing Analyzer missing {missing}")
        batch=json.loads((analysis/"drawings_split"/"batch_manifest.json").read_text(encoding="utf-8")); identity=json.loads((analysis/"db"/"source_identity_report.json").read_text(encoding="utf-8"))
        if not identity.get("verified") or batch.get("source_count")<1: raise RuntimeError("provenance_mismatch: Drawing Analyzer identity report failed")
        db_path=analysis/"db"/"project.sqlite"
        if spec.get("require_database",False) and not db_path.is_file(): raise RuntimeError("missing_output: Drawing Analyzer database/project.sqlite was not produced by batch entrypoint")
        destination.parent.mkdir(parents=True,exist_ok=True); destination.write_text(json.dumps({"schema_version":"drawing-analyzer-result-v1","source_id":item["source_id"],"source_version":item["source_hash"],"source_sha256":item["source_hash"],"pipeline":item["pipeline"],"analysis_root":str(analysis),"batch_manifest":batch,"source_identity_report":identity,"database_path":str(db_path) if db_path.is_file() else None},sort_keys=True,indent=2)+"\n",encoding="utf-8")
    def execute_graphrag(self,spec,item,data,destination):
        python=Path(spec["python"]); python=python if python.is_absolute() else self._path(spec["python"])
        project=Path(spec["project_root"]); project=project if project.is_absolute() else self._path(spec["project_root"])
        if not python.is_file(): raise RuntimeError(f"executable_missing: {python}")
        if not project.is_dir(): raise RuntimeError(f"project_missing: {project}")
        staged=destination.parent/"graphrag_project"; shutil.rmtree(staged,ignore_errors=True); shutil.copytree(project,staged,ignore=shutil.ignore_patterns("output","cache","logs"))
        input_dir=staged/"input"; shutil.rmtree(input_dir,ignore_errors=True); input_dir.mkdir(parents=True,exist_ok=True); (input_dir/(item["source_id"]+".txt")).write_bytes(data)
        env=dict(os.environ); env_file=Path(spec.get("env_file",project/".env")); env_file=env_file if env_file.is_absolute() else self._path(spec.get("env_file",project/".env"))
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                    key,value=line.split("=",1); env[key.strip()]=value.strip().strip('"').strip("'")
        if not env.get("GRAPHRAG_API_KEY"): raise RuntimeError("missing_api_key: GRAPHRAG_API_KEY is not available")
        module=spec.get("module","graphrag"); cmd=[str(python),"-m",module,"index","--root",str(staged)]
        try: subprocess.run(cmd,cwd=str(staged),check=True,capture_output=True,text=True,timeout=int(spec.get("timeout_seconds",3600)),env=env)
        except subprocess.TimeoutExpired as e: raise RuntimeError(f"timeout: GraphRAG exceeded {spec.get('timeout_seconds',3600)} seconds") from e
        except subprocess.CalledProcessError as e:
            detail=f"stderr={e.stderr[-1200:]} stdout={e.stdout[-1200:]}"
            if "No entities detected" in detail: raise RuntimeError(f"semantic_validation: GraphRAG returned no entities: {detail}") from e
            raise RuntimeError(f"nonzero_exit: GraphRAG {detail}") from e
        expected_outputs=spec.get("expected_outputs",["output/entities.parquet","output/relationships.parquet","output/text_units.parquet","output/communities.parquet"]); outputs=[]
        for relative in expected_outputs:
            path=staged/relative
            if not path.is_file(): raise RuntimeError(f"missing_output: GraphRAG missing {relative}")
            if path.stat().st_size<=0: raise RuntimeError(f"invalid_output: GraphRAG empty {relative}")
            try:
                import pyarrow.parquet as pq; rows=pq.read_metadata(path).num_rows
            except Exception as e: raise RuntimeError(f"invalid_output: GraphRAG unreadable {relative}: {e}") from e
            if rows<=0: raise RuntimeError(f"invalid_output: GraphRAG has no rows {relative}")
            outputs.append({"path":relative,"sha256":sha256(path.read_bytes()),"size_bytes":path.stat().st_size,"rows":rows})
        prompt_files=sorted((staged/"prompts").glob("*.txt")) if (staged/"prompts").is_dir() else []; prompt_hash=sha256(b"".join(p.name.encode()+p.read_bytes() for p in prompt_files)); config_hash=sha256((staged/"settings.yaml").read_bytes()+json.dumps({"module":module,"expected":expected_outputs,"model":spec.get("model"),"prompt_hash":prompt_hash,"configured_prompt_hash":spec.get("prompt_hash")},sort_keys=True).encode()); bundle=destination.parent/"graphrag_bundle"; bundle.mkdir(parents=True,exist_ok=True)
        for record in outputs: (bundle/record["path"]).parent.mkdir(parents=True,exist_ok=True); shutil.copy2(staged/record["path"],bundle/record["path"])
        destination.parent.mkdir(parents=True,exist_ok=True); destination.write_text(json.dumps({"schema_version":"graphrag-result-v1","source_id":item["source_id"],"source_version":item["source_hash"],"source_sha256":item["source_hash"],"source_citation":{"source_id":item["source_id"],"source_sha256":item["source_hash"]},"pipeline":item["pipeline"],"model":spec.get("model"),"prompt_hash":prompt_hash,"config_sha256":config_hash,"outputs":outputs,"bundle":"graphrag_bundle"},sort_keys=True,indent=2)+"\n",encoding="utf-8"); self._bundle_stage=bundle
    def execute_workbook(self,spec,item,data,destination):
        python=resolve_executable(spec.get("python",sys.executable),self.base); node=resolve_executable(spec.get("node",""),self.base) if spec.get("adapter") == "workbook" else None; script=Path(spec["script"]); script=script if script.is_absolute() else self._path(str(script)); canonical=Path(spec["canonical_workbook"]); canonical=canonical if canonical.is_absolute() else self._path(str(canonical))
        if not python.is_file(): raise RuntimeError(f"executable_missing: workbook python {python}")
        if spec.get("adapter") == "workbook" and (not node or not node.is_file()): raise RuntimeError(f"executable_missing: workbook node {node}")
        if not script.is_file(): raise RuntimeError(f"runner_missing: workbook publisher {script}")
        if not canonical.is_file(): raise RuntimeError(f"workbook_missing: {canonical}")
        payload_dir=destination.parent/"workbook_payload"; payload_dir.mkdir(parents=True,exist_ok=True); raw_payload_path=payload_dir/(item["source_id"]+".raw.json"); payload_path=payload_dir/(item["source_id"]+".normalized.json"); raw_payload_path.write_bytes(data)
        normalizer=self.base/"normalize_workbook_payload.py"
        try: subprocess.run([str(python),str(normalizer),str(raw_payload_path),str(payload_path)],cwd=str(self.base),check=True,capture_output=True,text=True,timeout=60)
        except subprocess.TimeoutExpired as e: raise RuntimeError("timeout: workbook payload normalization") from e
        except subprocess.CalledProcessError as e: raise RuntimeError(f"payload_validation: {e.stderr[-1500:]}") from e
        payload=json.loads(payload_path.read_text(encoding="utf-8")); payload["source_id"]=item["source_id"]; payload["source_sha256"]=item["source_hash"]; payload_path.write_text(json.dumps(payload,sort_keys=True,indent=2)+"\n",encoding="utf-8")
        bundle=destination.parent/"workbook_bundle"; bundle.mkdir(parents=True,exist_ok=True); output=bundle/"Transit Agency Monitor.xlsx"; manifest=bundle/"manifest.json"; cmd=([str(node),str(script)] if spec.get("adapter") == "workbook" else [str(python),str(script)])+[str(canonical),str(payload_path),str(output),str(manifest)]
        try:
            completed=subprocess.run(cmd,cwd=str(self.base),check=False,capture_output=True,text=True,timeout=int(spec.get("timeout_seconds",900)))
        except subprocess.TimeoutExpired as e: raise RuntimeError(f"timeout: workbook publisher exceeded {spec.get('timeout_seconds',900)} seconds") from e
        if completed.returncode != 0 and not (output.is_file() and manifest.is_file()):
            raise RuntimeError(f"nonzero_exit: workbook publisher stderr={completed.stderr[-1500:]} stdout={completed.stdout[-1500:]}")
        if not output.is_file() or output.stat().st_size<=0: raise RuntimeError("missing_output: workbook output missing or empty")
        if not manifest.is_file(): raise RuntimeError("missing_output: workbook manifest missing")
        manifest_obj=json.loads(manifest.read_text(encoding="utf-8"))
        if manifest_obj.get("source_sha256")!=item["source_hash"]: raise RuntimeError("provenance_mismatch: workbook manifest source hash mismatch")
        if manifest_obj.get("output_sha256")!=sha256(output.read_bytes()): raise RuntimeError("invalid_output: workbook manifest hash mismatch")
        if manifest_obj.get("output_size_bytes")!=output.stat().st_size: raise RuntimeError("invalid_output: workbook manifest size mismatch")
        destination.parent.mkdir(parents=True,exist_ok=True); destination.write_text(json.dumps({"schema_version":"workbook-result-v1","source_id":item["source_id"],"source_version":item["source_hash"],"source_sha256":item["source_hash"],"pipeline":"workbook","bundle":"workbook_bundle","workbook":str(output),"manifest":str(manifest)},sort_keys=True,indent=2)+"\n",encoding="utf-8"); self._bundle_stage=bundle
    def claim(self,c,run_id):
        c.execute("BEGIN IMMEDIATE"); row=c.execute("SELECT * FROM work_items WHERE state IN ('queued','retrying') AND COALESCE(available_at,next_attempt_at)<=? AND (locked_until IS NULL OR locked_until<=?) ORDER BY created_at LIMIT 1",(now(),now())).fetchone()
        if not row: c.commit(); return None
        token=uuid.uuid4().hex; updated=c.execute("UPDATE work_items SET state='running',locked_by=?,locked_until=?,lease_token=?,updated_at=? WHERE work_item_id=? AND state IN ('queued','retrying') AND (locked_until IS NULL OR locked_until<=?)",(run_id,later(self.lease_seconds),token,now(),row["work_item_id"],now())).rowcount
        if not updated: c.rollback(); return None
        c.commit(); return c.execute("SELECT * FROM work_items WHERE work_item_id=?",(row["work_item_id"],)).fetchone()
    def atomic_promote(self, stage, dest, bundle_stage=None):
        """Promote files only after validation, retaining backups until SQLite commits."""
        dest=Path(dest); dest.parent.mkdir(parents=True,exist_ok=True); temp_dest=dest.with_name(dest.name+".tmp-"+uuid.uuid4().hex); shutil.copy2(stage,temp_dest)
        if sha256(temp_dest.read_bytes())!=sha256(stage.read_bytes()): raise ValueError("staged destination hash mismatch")
        bundle_name=bundle_stage.name if bundle_stage else None; bundle_dest=dest.parent/bundle_name if bundle_name else None; temp_bundle=None
        if bundle_stage:
            for p in bundle_stage.rglob("*"):
                # Inspection sidecars are diagnostic artifacts, not publication
                # outputs.  They may be written by a concurrent validator after
                # the publisher has finished and must not affect the atomic
                # workbook bundle contract.
                if p.is_file() and p.name.endswith(".inspect.ndjson"):
                    continue
                if p.is_file() and p.stat().st_size<=0: raise ValueError("staged bundle contains an empty file")
        backups=[]
        try:
            if dest.exists():
                old=dest.with_name(dest.name+".previous-"+uuid.uuid4().hex); os.replace(dest,old); backups.append((old,dest))
            if bundle_dest and bundle_dest.exists():
                old=dest.parent/("graphrag_bundle.previous-"+uuid.uuid4().hex); os.replace(bundle_dest,old); backups.append((old,bundle_dest))
            os.replace(temp_dest,dest)
            if bundle_stage:
                bundle_dest.mkdir(parents=True, exist_ok=True)
                for source_file in bundle_stage.rglob("*"):
                    if not source_file.is_file() or source_file.name.endswith(".inspect.ndjson"):
                        continue
                    target_file = bundle_dest / source_file.relative_to(bundle_stage)
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_file, target_file)
            return dest,bundle_dest,backups
        except Exception:
            if temp_dest.exists(): temp_dest.unlink()
            if temp_bundle and temp_bundle.exists(): shutil.rmtree(temp_bundle,ignore_errors=True)
            for old,new in reversed(backups):
                if new.exists():
                    if new.is_dir(): shutil.rmtree(new,ignore_errors=True)
                    else: new.unlink()
                os.replace(old,new)
            raise
    def restore_backups(self, dest, bundle_dest, backups):
        if dest.exists(): dest.unlink()
        if bundle_dest and bundle_dest.exists(): shutil.rmtree(bundle_dest,ignore_errors=True)
        for old,new in reversed(backups):
            if old.exists(): os.replace(old,new)
    def remove_backups(self, backups):
        for old,_ in backups:
            if old.exists():
                if old.is_dir(): shutil.rmtree(old,ignore_errors=True)
                else: old.unlink()
    def publish_distribution(self, c, run_id):
        """Copy the verified committed workbook to the configured OneDrive destination."""
        dist = self.config.get("distribution") or {}
        if not dist.get("enabled"):
            return None
        destination = self._path(str(dist["path"]))
        row = c.execute("SELECT committed_path FROM work_items WHERE run_id=? AND pipeline='workbook' AND state='committed' ORDER BY updated_at DESC LIMIT 1", (run_id,)).fetchone()
        if not row:
            raise RuntimeError("distribution_source_missing: no committed workbook for successful run")
        source = Path(row[0]).parent / "workbook_bundle" / "Transit Agency Monitor.xlsx"
        if not source.is_file() or source.stat().st_size <= 0:
            raise RuntimeError(f"distribution_source_missing: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_name(destination.name + ".tmp-" + uuid.uuid4().hex)
        shutil.copy2(source, temp)
        source_hash = sha256(source.read_bytes())
        if sha256(temp.read_bytes()) != source_hash:
            temp.unlink(missing_ok=True)
            raise RuntimeError("distribution_hash_mismatch: temporary copy differs from committed workbook")
        os.replace(temp, destination)
        if sha256(destination.read_bytes()) != source_hash:
            raise RuntimeError("distribution_hash_mismatch: destination differs from committed workbook")
        audit = self._path(str(dist.get("audit_path", "orchestrator/state/distribution-audit.jsonl")))
        audit.parent.mkdir(parents=True, exist_ok=True)
        with audit.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"run_id":run_id,"status":"published_verified","source":str(source),"destination":str(destination),"sha256":source_hash,"published_at":now()}, sort_keys=True) + "\n")
        return str(destination)
    def process(self,c,run_id,row):
        sid,ver,pipeline=row["source_id"],row["source_version_id"],row["pipeline"]; spec=next(s for s in self.config["sources"] if s["source_id"]==sid); metadata=json.loads(c.execute("SELECT metadata_json FROM source_versions WHERE source_id=? AND version_id=?",(sid,ver)).fetchone()[0]); data=Path(metadata["path"]).read_bytes(); ch=sha256(data); maxa=int(self.config.get("max_attempts",3)); attempt=row["attempt_count"]+1; aid="att-"+uuid.uuid4().hex; c.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?)",(aid,row["work_item_id"],attempt,now(),None,"running",None)); c.commit()
        self._bundle_stage=None
        commit_backups=[]; bundle_dest=None; promoted_dest=None
        try:
            stage=self.stage/run_id/row["work_item_id"]/self.config["pipelines"][pipeline].get("output_name","result.json"); self.execute_pipeline(pipeline,{"source_id":sid,"source_version_id":ver,"source_hash":ch,"pipeline":pipeline},data,stage,attempt); raw=stage.read_bytes(); obj=json.loads(raw); expected={"source_id":sid,"source_version":ver,"source_sha256":ch,"pipeline":pipeline}
            if any(obj.get(k)!=v for k,v in expected.items()) or sha256(raw)!=sha256(stage.read_bytes()): raise ValueError("staged output failed content/hash verification")
            dest=self.commit/sid/ver/pipeline/stage.name; bundle_stage=self._bundle_stage; promoted_dest,bundle_dest,commit_backups=self.atomic_promote(stage,dest,bundle_stage)
            if sha256(promoted_dest.read_bytes())!=sha256(raw): raise ValueError("post-copy hash mismatch")
            if bundle_stage:
                for staged_file in bundle_stage.rglob("*"):
                    if staged_file.is_file() and not staged_file.name.endswith(".inspect.ndjson"):
                        committed_file=bundle_dest/staged_file.relative_to(bundle_stage)
                        if not committed_file.is_file() or sha256(committed_file.read_bytes())!=sha256(staged_file.read_bytes()):
                            raise ValueError(f"post-copy bundle hash mismatch: {staged_file.name}")
            c.execute("BEGIN"); c.execute("UPDATE attempts SET finished_at=?,status=? WHERE attempt_id=?",(now(),"succeeded",aid)); artifact_rows=[("art-"+uuid.uuid4().hex,row["work_item_id"],str(stage),str(dest),sha256(raw),len(raw),"application/json",json.dumps(expected,sort_keys=True),now())]
            if bundle_stage:
                for item_path in bundle_stage.rglob("*"):
                    if item_path.is_file() and not item_path.name.endswith(".inspect.ndjson"):
                        committed=bundle_dest/item_path.relative_to(bundle_stage); artifact_rows.append(("art-"+uuid.uuid4().hex,row["work_item_id"],str(item_path),str(committed),sha256(item_path.read_bytes()),item_path.stat().st_size,"application/octet-stream",json.dumps({"bundle":bundle_stage.name,"relative_path":str(item_path.relative_to(bundle_stage))},sort_keys=True),now()))
            c.executemany("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?)",artifact_rows); c.execute("UPDATE work_items SET state='committed',attempt_count=?,staging_path=?,committed_path=?,locked_by=NULL,locked_until=NULL,lease_token=NULL,updated_at=?,last_error=NULL WHERE work_item_id=?",(attempt,str(stage),str(promoted_dest),now(),row["work_item_id"])); c.commit(); self.remove_backups(commit_backups); return True
        except Exception as e:
            if commit_backups:
                self.restore_backups(promoted_dest,bundle_dest,commit_backups)
            retry=attempt<maxa; retry_at=later(float(self.config.get("retry_delay_seconds",0))) if retry else None; message=str(e); prefix=message.split(":",1)[0]; code=prefix if prefix in {"timeout","nonzero_exit","semantic_validation","executable_missing","runner_missing"} else ("transient_error" if any(x in message.lower() for x in ("transient","connection","429","temporarily")) else "invalid_output"); retryable=retry and code not in {"semantic_validation","invalid_output"}; state="retrying" if retryable else ("blocked" if code in {"semantic_validation","invalid_output"} else "failed"); c.execute("BEGIN"); c.execute("UPDATE attempts SET finished_at=?,status=?,error=? WHERE attempt_id=?",(now(),"failed",message,aid)); c.execute("INSERT INTO failures VALUES(?,?,?,?,?,?,?,?)",("fail-"+uuid.uuid4().hex,run_id,row["work_item_id"],aid,code,message,int(retryable),now())); c.execute("UPDATE work_items SET state=?,attempt_count=?,available_at=?,next_attempt_at=?,locked_by=NULL,locked_until=NULL,lease_token=NULL,updated_at=?,last_error=? WHERE work_item_id=?",(state,attempt,retry_at if retryable else None,retry_at if retryable else None,now(),message,row["work_item_id"])); c.commit(); return False
    def run(self,dry_run=False):
        run_id="run-"+uuid.uuid4().hex; self.stage.mkdir(parents=True,exist_ok=True); (self.commit.mkdir(parents=True,exist_ok=True) if not dry_run else None); c=self.conn(); cfg_hash=sha256(self.config_path.read_bytes()); c.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?)",(run_id,now(),None,"running",int(dry_run),cfg_hash,None)); c.commit()
        try:
            self.verify_executables(); self.preflight(); self.recover_expired(c,run_id); self.discover(c,run_id)
            if not dry_run:
                for _ in range(self.batch_size):
                    row=self.claim(c,run_id)
                    if not row: break
                    self.process(c,run_id,row)
            status="dry_run" if dry_run else ("failed" if c.execute("SELECT 1 FROM failures WHERE run_id=? AND retryable=0 LIMIT 1",(run_id,)).fetchone() else "completed")
            distribution = None
            if status == "completed" and not dry_run:
                distribution = self.publish_distribution(c, run_id)
            c.execute("UPDATE runs SET finished_at=?,status=? WHERE run_id=?",(now(),status,run_id)); c.commit(); self.event(event="run_finished",run_id=run_id,status=status,distribution=distribution); return {"run_id":run_id,"status":status,"distribution":distribution}
        except Exception as e:
            c.execute("UPDATE runs SET finished_at=?,status=?,error=? WHERE run_id=?",(now(),"failed",str(e),run_id)); c.commit(); self.event(event="run_failed",run_id=run_id,error=str(e)); return {"run_id":run_id,"status":"failed","error":str(e)}
        finally:
            c.close()
            for h in list(self.logger.handlers): h.close(); self.logger.removeHandler(h)
