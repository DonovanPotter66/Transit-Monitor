"""Invoke the installed Drawing Analyzer batch contract in a staging-safe way."""
import argparse, importlib.util, os, sys, tempfile
from pathlib import Path

def main():
    p=argparse.ArgumentParser(); p.add_argument("entrypoint"); p.add_argument("input"); p.add_argument("output"); args=p.parse_args()
    entry=Path(args.entrypoint).resolve(); sys.path.insert(0,str(entry.parent))
    spec=importlib.util.spec_from_file_location("drawing_analyzer_batch",entry); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    supplied=[Path(os.environ[k]) for k in ("DRAWING_ANALYZER_BATCH_STAGE","DRAWING_ANALYZER_BATCH_ROLLBACK") if os.environ.get(k)]
    original=tempfile.mkdtemp; counter=iter(supplied)
    def safe_mkdtemp(prefix="",suffix="",dir=None):
        try: return str(next(counter))
        except StopIteration: return original(prefix=prefix.lstrip("."),suffix=suffix,dir=dir)
    tempfile.mkdtemp=safe_mkdtemp
    module.run_batch(args.input,args.output,recursive=True)
if __name__=="__main__": main()
