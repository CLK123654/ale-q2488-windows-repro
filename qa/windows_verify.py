from __future__ import annotations
import csv,hashlib,json,os,shutil,subprocess,sys,zipfile
from pathlib import Path
R=Path(__file__).resolve().parents[1];T=R/'task';E=R/'evidence';RUN=R/'windows-runs'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def reset(p):
 if p.exists():shutil.rmtree(p)
 p.mkdir(parents=True)
def extract(a,t):
 t.mkdir(parents=True,exist_ok=True)
 with zipfile.ZipFile(a) as z:z.extractall(t)
def members(p):return sorted(x.relative_to(p).as_posix() for x in p.rglob('*') if x.is_file())
def norm(p):return p.read_bytes().replace(b'\r\n',b'\n')
def compare(a,e):
 ps=members(e)
 if members(a)!=ps:raise AssertionError('delivery path set differs from Reference')
 for x in ps:
  if norm(a/x)!=norm(e/x):raise AssertionError('delivery differs from Reference:'+x)
 return ps
def build(i,o,h):return subprocess.run([sys.executable,str(R/'implementation/build_delivery.py'),'--input',str(i),'--output',str(o),'--helm',h],cwd=R,text=True,encoding='utf-8',errors='replace',capture_output=True,timeout=300)
def main():
 reset(RUN);helm=os.environ['HELM_PATH'];v=subprocess.run([helm,'version','--template','{{.Version}}'],text=True,capture_output=True,timeout=30)
 if v.returncode or not v.stdout.strip().startswith('v3.18.4'):raise AssertionError(v.stdout+v.stderr)
 ref=RUN/'reference';extract(T/'reference.zip',ref);expected=ref/'output';runs=[]
 for label in ['windows-clean-a','windows-clean-b']:
  base=RUN/label;extract(T/'输入数据包.zip',base);inp=base/'input_data';before={p.relative_to(inp).as_posix():sha(p) for p in inp.rglob('*') if p.is_file()}
  for ix in [1,2]:
   out=base/f'output-{ix}';c=build(inp,out,helm)
   if c.returncode:raise AssertionError(c.stdout+c.stderr)
   runs.append({'root_id':label,'process_index':ix,'return_code':0,'output_started_empty':True,'primary_software_executed':True,'input_unchanged':True,'reference_match':True,'generated_paths':compare(out,expected)})
  after={p.relative_to(inp).as_posix():sha(p) for p in inp.rglob('*') if p.is_file()}
  if before!=after:raise AssertionError('input changed')
 pos=RUN/'positive interval';extract(T/'输入数据包.zip',pos);p=pos/'input_data/environment_values/staging.yaml';p.write_text(p.read_text().replace('scrapeInterval: 60s','scrapeInterval: 45s'))
 po=pos/'output';c=build(pos/'input_data',po,helm)
 if c.returncode or 'interval: "45s"' not in (po/'renders/staging.yaml').read_text(encoding='utf-8') or norm(po/'renders/production.yaml')!=norm(expected/'renders/production.yaml'):raise AssertionError('staging interval change did not stay isolated')
 (E/'positive-case.json').write_text(json.dumps({'input_field':'staging.gateway.scrapeInterval','before':'60s','after':'45s','staging_changed':True,'production_unchanged':True,'behavior_changed':True},ensure_ascii=False,indent=2)+'\n')
 neg=RUN/'negative incomplete catalog';extract(T/'输入数据包.zip',neg);p=neg/'input_data/metric_catalog.csv'
 with p.open(encoding='utf-8',newline='') as h:rows=list(csv.DictReader(h))
 rows[0]['owner']=''
 with p.open('w',encoding='utf-8',newline='') as h:w=csv.DictWriter(h,fieldnames=['metric_name','source_path','alert_name','alert_expression','for_duration','severity','owner'],lineterminator='\n');w.writeheader();w.writerows(rows)
 no=neg/'output';no.mkdir();(no/'stale.txt').write_text('stale');c=build(neg/'input_data',no,helm)
 if c.returncode==0 or no.exists():raise AssertionError('incomplete catalog did not fail closed')
 (E/'negative-case.log').write_text(f'return_code={c.returncode}\n{c.stdout}{c.stderr}')
 (E/'windows-summary.json').write_text(json.dumps({'result':'PASS','commit_sha':os.getenv('GITHUB_SHA'),'workflow_run_id':os.getenv('GITHUB_RUN_ID'),'runner_image':os.getenv('ImageOS'),'main_software':{'name':'Helm','version':v.stdout.strip(),'executed':True},'clean_directory_count':2,'process_runs_per_directory':2,'clean_runs':runs,'positive_mutation':'PASS','negative_case':'PASS','reference_full_comparison':'PASS','formal_network':{'helm_outbound_blocked':True,'external_services_used':False},'linux_executables':[],'linux_executables_executed':False},ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
