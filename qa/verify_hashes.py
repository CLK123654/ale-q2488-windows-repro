from __future__ import annotations
import hashlib,json
from pathlib import Path
R=Path(__file__).resolve().parents[1];e=json.loads((R/'qa/expected_hashes.json').read_text(encoding='utf-8'));a={n:hashlib.sha256((R/'task'/n).read_bytes()).hexdigest() for n in e}
if a!=e:raise SystemExit('attachment hash mismatch')
(R/'evidence').mkdir(exist_ok=True);(R/'evidence/attachment-hashes.json').write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
