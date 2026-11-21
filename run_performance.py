"""
run_performance.py — Track how your uploaded clips perform and learn from it.

    python run_performance.py auth                       # one-time Google sign-in (read-only)
    python run_performance.py ingest outputs/myvideo     # store a run's predicted scores
    python run_performance.py link outputs/myvideo 2 VIDEO_ID   # clip rank 2 -> uploaded video
    python run_performance.py fetch                      # snapshot views / retention (run daily)
    python run_performance.py report                     # predictions vs. real results
    python run_performance.py examples                   # top clips -> prompt examples

See docs/PERFORMANCE.md.
"""

import argparse
import sys

from clipping import performance as perf


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Learn from how your uploaded clips perform.")
    p.add_argument("--db", default=perf.DB_FILE, help=f"SQLite file (default: {perf.DB_FILE})")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auth", help="One-time Google sign-in for YouTube Data + Analytics (read-only).")
    ing = sub.add_parser("ingest", help="Store predicted scores of a run folder (and uploaded video IDs).")
    ing.add_argument("run_dirs", nargs="+")
    ln = sub.add_parser("link", help="Attach an uploaded YouTube video ID to a clip.")
    ln.add_argument("run_dir")
    ln.add_argument("rank", type=int)
    ln.add_argument("video_id")
    sub.add_parser("fetch", help="Snapshot views/likes/comments and retention for linked clips.")
    rep = sub.add_parser("report", help="Correlate predictions with real results.")
    rep.add_argument("--min-age-hours", type=float, default=72,
                     help="Only use snapshots at least this old (default: 72)")
    ex = sub.add_parser("examples", help="Write your top clips as prompt examples.")
    ex.add_argument("--top", type=int, default=3)
    ex.add_argument("--min-age-hours", type=float, default=72)
    args = p.parse_args(argv)

    if args.cmd == "auth":
        print(f"✅ Token saved to {perf.authorize()}")
        return 0

    conn = perf.connect(args.db)
    if args.cmd == "ingest":
        for run_dir in args.run_dirs:
            r = perf.ingest_run(conn, run_dir)
            print(f"✅ {run_dir}: {r['clips']} clips ({r['added']} new), {r['linked']} linked to uploads")
    elif args.cmd == "link":
        perf.link_clip(conn, args.run_dir, args.rank, args.video_id)
        print(f"✅ Rank {args.rank} of {args.run_dir} -> {args.video_id}")
    elif args.cmd == "fetch":
        youtube, analytics = perf.build_services()
        print(f"✅ {perf.fetch_metrics(conn, youtube, analytics)} snapshots saved")
    elif args.cmd == "report":
        print(perf.build_report(conn, args.min_age_hours))
    elif args.cmd == "examples":
        ex = perf.write_examples(conn, top=args.top, min_age_hours=args.min_age_hours)
        if ex:
            print(f"✅ {len(ex)} examples written to {perf.EXAMPLES_FILE}; the next runs will show them to the AI.")
        else:
            print("No clips with performance data yet; nothing written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
