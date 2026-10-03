"""clavesa system-table maintenance (GH #53).

OPTIMIZE + VACUUM the four workspace bookkeeping tables under the system
catalog (node_runs, runs, tables, column_stats) so their Delta transaction
log and small-file count stay bounded. clavesa writes these tables once per
node per run, so without periodic compaction their _delta_log dirs grow to
thousands of commit files and dominate S3 LIST cost.

Runs as a normal scheduled transform (Lambda in the cloud, local docker
otherwise) and produces no output tables: it returns an empty dict. Only the
workspace-owned system catalog is touched, so the pipeline's default runner
IAM is sufficient and no other pipeline's data is read or rewritten.
"""

import os
import sys

_SYSTEM_TABLES = ["node_runs", "runs", "tables", "column_stats"]

_PROPS = {
    "delta.logRetentionDuration": "interval 24 hours",
    "delta.deletedFileRetentionDuration": "interval 24 hours",
    "delta.checkpointInterval": "10",
}

# VACUUM retention window. The system tables set a 24h
# deletedFileRetentionDuration, so 24h reclaims promptly while staying far
# above the longest concurrent transaction: several pipelines write these
# tables (multi-writer by design), so the window must exceed any in-flight
# append, and a seconds-long append clears 24h with enormous margin.
_VACUUM_RETAIN_HOURS = 24


def _system_db():
    sys_cat = os.environ.get("CLAVESA_SYSTEM_CATALOG") or ""
    if not sys_cat:
        cat = os.environ.get("CLAVESA_CATALOG", "")
        sys_cat = cat + "_system" if cat else ""
    return sys_cat.replace("-", "_") + "__pipelines"


def transform(spark, inputs):
    # Permit VACUUM with a sub-7-day retention, scoped to this session only —
    # the global runner config keeps the safety check on for every other run.
    spark.conf.set("spark.databricks.delta.retentionDurationCheck.enabled", "false")

    db = _system_db()
    if not db or db.startswith("__"):
        print("[clavesa] _maintenance: no system catalog resolved; nothing to do", file=sys.stderr)
        return {}

    props = ", ".join("'%s' = '%s'" % (k, v) for k, v in _PROPS.items())
    for name in _SYSTEM_TABLES:
        table_id = "%s.%s" % (db, name)
        if not spark.catalog.tableExists(table_id):
            print("[clavesa] _maintenance: %s not created yet, skipping" % table_id, file=sys.stderr)
            continue
        try:
            # SET TBLPROPERTIES also covers system tables created before the
            # #53 retention props shipped (the runner sets them only on first
            # create); idempotent on already-configured tables.
            spark.sql("ALTER TABLE %s SET TBLPROPERTIES (%s)" % (table_id, props))
            spark.sql("OPTIMIZE %s" % table_id)
            spark.sql("VACUUM %s RETAIN %d HOURS" % (table_id, _VACUUM_RETAIN_HOURS))
            print("[clavesa] _maintenance: compacted %s" % table_id, file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            # Best-effort per table — one failure must not abort the rest.
            print("[clavesa] _maintenance: %s failed: %r" % (table_id, exc), file=sys.stderr)

    return {}
