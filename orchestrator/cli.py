import argparse
from .engine import Orchestrator

def main(argv=None):
    p = argparse.ArgumentParser(prog="orchestrator")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--config", default="orchestrator/config.json")
    run.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    if args.command == "run":
        orchestrator = Orchestrator.from_file(args.config)
        result = orchestrator.run(dry_run=args.dry_run)
        if not args.dry_run and orchestrator.config.get("shadow_report_dir"):
            from .shadow_report import write_report
            write_report(orchestrator.config, orchestrator.config_path, result["run_id"], result["status"], result.get("error"))
        print(result)
        return 0 if result["status"] in ("completed", "dry_run") else 1
    return 2
