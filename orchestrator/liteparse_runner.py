"""Staging-safe LiteParse entrypoint invoked by the orchestration adapter."""
import argparse, hashlib, json
from pathlib import Path
from liteparse import LiteParse

def main():
    p=argparse.ArgumentParser(); p.add_argument("input"); p.add_argument("output"); p.add_argument("--source-id",required=True); p.add_argument("--pipeline",required=True); args=p.parse_args()
    source=Path(args.input); data=source.read_bytes(); source_hash=hashlib.sha256(data).hexdigest()
    parser=LiteParse(output_format="markdown",image_mode="off",extract_images=False,extract_links=True,keep_headers_footers=False,ocr_enabled=False,max_pages=1000,quiet=True)
    result=parser.parse(source); text=(result.text or "").strip()
    if not text: raise RuntimeError("LiteParse returned no text")
    payload={"schema_version":"liteparse-result-v1","source_id":args.source_id,"pipeline":args.pipeline,"source_version":source_hash,"source_path":str(source),"source_sha256":source_hash,"parser":"LiteParse","text":text}
    destination=Path(args.output); destination.parent.mkdir(parents=True,exist_ok=True); destination.write_text(json.dumps(payload,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
if __name__=="__main__": main()
