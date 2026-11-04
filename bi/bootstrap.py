"""Load a project snapshot and create native Superset assets idempotently."""

import json
import os
import re
from pathlib import Path

import psycopg2
from superset.app import create_app

ROOT = Path("/app/bi")
spec = json.loads((ROOT / "dashboard.json").read_text())
password = os.environ["BI_DB_PASSWORD"]
with (
    psycopg2.connect(host="db", dbname="bi", user="bi", password=password) as conn,
    conn.cursor() as cur,
):
    cur.execute((ROOT / "schema.sql").read_text())
    cur.execute("TRUNCATE bi_records")
    with (ROOT / "data/records.csv").open() as source:
        cur.copy_expert("COPY bi_records FROM STDIN WITH (FORMAT CSV, HEADER TRUE)", source)

app = create_app()
with app.app_context():
    from superset import appbuilder, db
    from superset.connectors.sqla.models import SqlaTable
    from superset.models.core import Database
    from superset.models.dashboard import Dashboard
    from superset.models.slice import Slice

    user = appbuilder.sm.find_user(username="admin")
    if user is None:
        user = appbuilder.sm.add_user(
            "admin",
            "BI",
            "Owner",
            "admin@localhost.invalid",
            appbuilder.sm.find_role("Admin"),
            os.environ["BI_ADMIN_PASSWORD"],
        )
    database = db.session.query(Database).filter_by(database_name="Project BI").one_or_none()
    if database is None:
        database = Database(
            database_name="Project BI",
            sqlalchemy_uri=f"postgresql+psycopg2://bi:{password}@db:5432/bi",
        )
        db.session.add(database)
    database.expose_in_sqllab = True
    database.cache_timeout = -1
    db.session.flush()
    charts = []
    for cfg in spec["charts"]:
        for col in cfg["columns"]:
            if not re.fullmatch(r"[a-z_][a-z0-9_]*", col):
                raise ValueError("Invalid configured column")
        table = (
            db.session.query(SqlaTable)
            .filter_by(database_id=database.id, table_name=cfg["table"], schema="public")
            .one_or_none()
        )
        if table is None:
            table = SqlaTable(table_name=cfg["table"], schema="public", database=database)
            db.session.add(table)
            db.session.flush()
        table.fetch_metadata()
        table.cache_timeout = -1
        viz = cfg["viz_type"]
        params = {
            "datasource": f"{table.id}__table",
            "viz_type": viz,
            "row_limit": 1000,
            "time_range": "No filter",
            "adhoc_filters": [],
        }
        columns, metrics = cfg["columns"], []
        if viz == "table":
            params.update(query_mode="raw", all_columns=columns, server_pagination=False)
        else:
            name = cfg["metric"]
            if name not in columns:
                raise ValueError("Metric must be an exported column")
            metric = {
                "expressionType": "SQL",
                "sqlExpression": f'SUM("{name}")',
                "label": name,
                "hasCustomLabel": True,
                "optionName": f"metric_{name}",
            }
            metrics = [metric]
            columns = cfg.get("groupby", [])
            params.update(metrics=metrics, groupby=columns)
            if viz == "big_number_total":
                params.update(
                    metric=metric,
                    header_font_size=0.4,
                    subheader=cfg["title"],
                    subheader_font_size=0.15,
                    y_axis_format=",.0f",
                )
            elif viz == "echarts_timeseries_bar":
                params.update(
                    x_axis=columns[0],
                    groupby=columns[1:],
                    show_legend=bool(columns[1:]),
                    x_axis_sort="name",
                    x_axis_sort_asc=True,
                    orientation="vertical",
                    show_value=True,
                    rich_tooltip=True,
                    color_scheme="supersetColors",
                    y_axis_format=",.0f",
                )
        query = {
            "columns": columns,
            "metrics": metrics,
            "filters": [],
            "row_limit": 1000,
            "extras": {"where": "", "having": ""},
            "order_desc": True,
        }
        context = {
            "datasource": {"id": table.id, "type": "table"},
            "force": False,
            "queries": [query],
            "form_data": params,
            "result_format": "json",
            "result_type": "full",
        }
        chart = (
            db.session.query(Slice)
            .filter_by(slice_name=cfg["title"], datasource_id=table.id, datasource_type="table")
            .one_or_none()
        )
        if chart is None:
            chart = Slice(slice_name=cfg["title"], datasource_id=table.id, datasource_type="table")
            db.session.add(chart)
        chart.viz_type, chart.params = viz, json.dumps(params)
        chart.cache_timeout = -1
        chart.query_context = json.dumps(context)
        chart.owners = [user]
        charts.append(chart)
    db.session.flush()
    dash = db.session.query(Dashboard).filter_by(slug="project-bi").one_or_none()
    if dash is None:
        dash = Dashboard(slug="project-bi")
        db.session.add(dash)
    dash.dashboard_title, dash.published, dash.owners, dash.slices = (
        spec["title"],
        True,
        [user],
        charts,
    )
    positions = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"id": "ROOT_ID", "type": "ROOT", "children": ["GRID_ID"]},
        "GRID_ID": {"id": "GRID_ID", "type": "GRID", "parents": ["ROOT_ID"], "children": []},
        "HEADER_ID": {"id": "HEADER_ID", "type": "HEADER", "meta": {"text": spec["title"]}},
    }
    for index, chart in enumerate(charts):
        row_id, chart_id = f"ROW-{index}", f"CHART-{chart.id}"
        positions["GRID_ID"]["children"].append(row_id)
        positions[row_id] = {
            "id": row_id,
            "type": "ROW",
            "parents": ["ROOT_ID", "GRID_ID"],
            "children": [chart_id],
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
        }
        positions[chart_id] = {
            "id": chart_id,
            "type": "CHART",
            "parents": ["ROOT_ID", "GRID_ID", row_id],
            "meta": {
                "chartId": chart.id,
                "width": 12,
                "height": 20 if chart.viz_type == "big_number_total" else 35,
                "sliceName": chart.slice_name,
            },
        }
    dash.position_json = json.dumps(positions)
    dash.json_metadata = json.dumps({"refresh_frequency": 0, "timed_refresh_immune_slices": []})
    db.session.commit()
    print(f"Imported snapshot and {len(charts)} charts. Dashboard: /superset/dashboard/project-bi/")
