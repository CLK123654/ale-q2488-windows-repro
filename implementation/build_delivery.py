from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml


REQUIRED = {
    "README.txt", "change_request.txt", "release_plan.csv", "metric_catalog.csv",
    "environment_values/staging.yaml", "environment_values/production.yaml",
    "starter/gateway-observe/Chart.yaml", "starter/gateway-observe/values.yaml",
    "starter/gateway-observe/templates/configmap.yaml",
}
CATALOG_FIELDS = {"metric_name", "source_path", "alert_name", "alert_expression", "for_duration", "severity", "owner"}


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, encoding="utf-8", errors="strict", capture_output=True, timeout=180)


def docs(text: str) -> list[dict]:
    result = [item for item in yaml.safe_load_all(text) if item]
    keys = [(item.get("kind"), item.get("metadata", {}).get("namespace"), item.get("metadata", {}).get("name")) for item in result]
    if not result or len(keys) != len(set(keys)):
        raise ValueError("观测清单对象为空或身份重复")
    return result


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--helm", required=True)
    args = parser.parse_args()
    source = Path(args.input).resolve()
    output = Path(args.output).resolve()
    if output.exists():
        shutil.rmtree(output)
    present = {item.relative_to(source).as_posix() for item in source.rglob("*") if item.is_file()}
    if not REQUIRED.issubset(present):
        raise ValueError("观测发布材料不完整")
    with (source / "release_plan.csv").open(encoding="utf-8", newline="") as handle:
        plan = list(csv.DictReader(handle))
    with (source / "metric_catalog.csv").open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle); catalog = list(reader); fields = set(reader.fieldnames or [])
    if {row["environment"] for row in plan} != {"staging", "production"}:
        raise ValueError("环境发布计划不完整")
    if fields != CATALOG_FIELDS or not catalog or any(not row[field] for row in catalog for field in CATALOG_FIELDS):
        raise ValueError("指标告警目录不完整")
    if len({row["alert_name"] for row in catalog}) != len(catalog):
        raise ValueError("告警名称重复")

    temp = Path(tempfile.mkdtemp(prefix="gateway-observe-", dir=output.parent))
    try:
        chart = temp / "chart/gateway-observe"
        values_dir = temp / "values"
        renders = temp / "renders"
        reports = temp / "reports"
        shutil.copytree(source / "starter/gateway-observe", chart)
        values_dir.mkdir(parents=True); renders.mkdir(); reports.mkdir()
        (chart / "values.schema.json").write_text(json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "additionalProperties": False,
            "required": ["gateway", "alerts"], "properties": {
                "gateway": {"type": "object", "additionalProperties": False, "required": ["serviceName", "servicePort", "metricsPath", "scrapeInterval", "team", "region"], "properties": {
                    "serviceName": {"type": "string", "minLength": 1}, "servicePort": {"type": "integer", "minimum": 1, "maximum": 65535},
                    "metricsPath": {"type": "string", "pattern": "^/"}, "scrapeInterval": {"type": "string", "pattern": "^[1-9][0-9]*s$"},
                    "team": {"type": "string", "minLength": 1}, "region": {"type": "string", "minLength": 1}}},
                "alerts": {"type": "array", "minItems": 1, "items": {"type": "object", "additionalProperties": False, "required": ["metricName", "name", "expression", "duration", "severity", "owner"], "properties": {
                    "metricName": {"type": "string", "minLength": 1}, "name": {"type": "string", "minLength": 1}, "expression": {"type": "string", "minLength": 1},
                    "duration": {"type": "string", "pattern": "^[1-9][0-9]*m$"}, "severity": {"type": "string", "enum": ["warning", "critical"]}, "owner": {"type": "string", "minLength": 1}}}},
            }}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (chart / "values.yaml").write_text("gateway:\n  serviceName: api-gateway\n  servicePort: 9102\n  metricsPath: /metrics\n  scrapeInterval: 60s\n  team: edge-platform\n  region: sandbox\nalerts: []\n", encoding="utf-8")
        (chart / "templates/configmap.yaml").unlink()
        (chart / "templates/servicemonitor.yaml").write_text(
            "apiVersion: monitoring.coreos.com/v1\nkind: ServiceMonitor\nmetadata:\n  name: {{ .Release.Name }}\n  labels:\n    app.kubernetes.io/part-of: gateway-observe\n    observe.example/team: {{ .Values.gateway.team | quote }}\nspec:\n  selector:\n    matchLabels:\n      app.kubernetes.io/name: {{ .Values.gateway.serviceName }}\n  namespaceSelector:\n    matchNames:\n      - {{ .Release.Namespace }}\n  endpoints:\n    - port: metrics\n      targetPort: {{ .Values.gateway.servicePort }}\n      path: {{ .Values.gateway.metricsPath | quote }}\n      interval: {{ .Values.gateway.scrapeInterval | quote }}\n",
            encoding="utf-8")
        (chart / "templates/prometheusrule.yaml").write_text(
            "apiVersion: monitoring.coreos.com/v1\nkind: PrometheusRule\nmetadata:\n  name: {{ .Release.Name }}\n  labels:\n    app.kubernetes.io/part-of: gateway-observe\nspec:\n  groups:\n    - name: {{ .Release.Name }}\n      rules:\n{{- range .Values.alerts }}\n        - alert: {{ .name }}\n          expr: {{ .expression | quote }}\n          for: {{ .duration }}\n          labels:\n            severity: {{ .severity | quote }}\n            owner: {{ .owner | quote }}\n            region: {{ $.Values.gateway.region | quote }}\n          annotations:\n            metric: {{ .metricName | quote }}\n{{- end }}\n",
            encoding="utf-8")

        endpoints: list[dict] = []
        alert_rows: list[dict] = []
        for row in plan:
            env_values = yaml.safe_load((source / row["values_file"]).read_text(encoding="utf-8"))
            values = {"gateway": env_values["gateway"], "alerts": [{"metricName": item["metric_name"], "name": item["alert_name"], "expression": item["alert_expression"], "duration": item["for_duration"], "severity": item["severity"], "owner": item["owner"]} for item in catalog]}
            values_path = values_dir / f"{row['environment']}.yaml"
            values_path.write_text(yaml.safe_dump(values, allow_unicode=True, sort_keys=False), encoding="utf-8")
            lint = run([args.helm, "lint", str(chart), "--values", str(values_path)])
            rendered = run([args.helm, "template", row["release_name"], str(chart), "--namespace", row["namespace"], "--values", str(values_path)])
            if lint.returncode or rendered.returncode:
                raise RuntimeError(lint.stdout + lint.stderr + rendered.stdout + rendered.stderr)
            objects = {item["kind"]: item for item in docs(rendered.stdout)}
            if set(objects) != {"ServiceMonitor", "PrometheusRule"}:
                raise ValueError("观测对象集合不符合发布范围")
            monitor = objects["ServiceMonitor"]
            rule = objects["PrometheusRule"]
            endpoint = monitor["spec"]["endpoints"][0]
            gateway = values["gateway"]
            if endpoint != {"port": "metrics", "targetPort": gateway["servicePort"], "path": gateway["metricsPath"], "interval": gateway["scrapeInterval"]}:
                raise ValueError("ServiceMonitor端点与环境值不一致")
            if monitor["spec"]["selector"]["matchLabels"].get("app.kubernetes.io/name") != gateway["serviceName"]:
                raise ValueError("ServiceMonitor没有选中网关Service")
            actual_rules = rule["spec"]["groups"][0]["rules"]
            if len(actual_rules) != len(catalog):
                raise ValueError("PrometheusRule没有覆盖指标目录")
            by_name = {item["alert"]: item for item in actual_rules}
            for item in catalog:
                actual = by_name.get(item["alert_name"])
                if not actual or actual["expr"] != item["alert_expression"] or actual["for"] != item["for_duration"] or actual["labels"]["severity"] != item["severity"] or actual["labels"]["owner"] != item["owner"]:
                    raise ValueError(f"告警规则与目录不一致:{item['alert_name']}")
                alert_rows.append({"environment": row["environment"], "alert_name": item["alert_name"], "metric_name": item["metric_name"], "duration": item["for_duration"], "severity": item["severity"], "owner": item["owner"], "evidence": f"renders/{row['environment']}.yaml", "status": "READY"})
            endpoints.append({"environment": row["environment"], "release_name": row["release_name"], "namespace": row["namespace"], "change_ticket": row["change_ticket"], "service_name": gateway["serviceName"], "service_port": gateway["servicePort"], "metrics_path": gateway["metricsPath"], "scrape_interval": gateway["scrapeInterval"], "region": gateway["region"], "status": "READY"})
            (renders / f"{row['environment']}.yaml").write_text(rendered.stdout, encoding="utf-8")

        write_csv(reports / "scrape_endpoints.csv", endpoints, ["environment", "release_name", "namespace", "change_ticket", "service_name", "service_port", "metrics_path", "scrape_interval", "region", "status"])
        write_csv(reports / "alert_routes.csv", alert_rows, ["environment", "alert_name", "metric_name", "duration", "severity", "owner", "evidence", "status"])
        (temp / "release_note.md").write_text("# API网关观测发布包\n\nstaging与production分别生成ServiceMonitor和PrometheusRule候选清单。scrape_endpoints.csv供观测团队核对抓取入口，alert_routes.csv连接指标、告警和负责人。\n\n版本负责人按变更单安排维护窗，现场应用、指标采集和告警观察由当班人员继续处理。\n", encoding="utf-8")
        temp.rename(output)
    except Exception:
        if temp.exists(): shutil.rmtree(temp)
        if output.exists(): shutil.rmtree(output)
        raise


if __name__ == "__main__":
    main()
