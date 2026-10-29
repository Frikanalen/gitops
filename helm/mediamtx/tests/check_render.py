"""Render real chart output, including the no-Operator installation mode."""
import pathlib
import subprocess
import unittest

import yaml

CHART = pathlib.Path(__file__).resolve().parents[1]


def render(*values, succeeds=True):
    command = ["helm", "template", "mediamtx", str(CHART)]
    for value in (
        "authURL=http://django-api.default.svc.cluster.local:8080/api/mediamtx/auth",
        "service.loadBalancerIP=192.168.3.93", *values,
    ):
        command += ["--set", value]
    result = subprocess.run(command, capture_output=True, text=True)
    if not succeeds:
        if result.returncode == 0:
            raise AssertionError("Chart accepted invalid configuration")
        return []
    if result.returncode:
        raise AssertionError(result.stderr)
    return [item for item in yaml.safe_load_all(result.stdout) if item]


def find(documents, kind, name):
    return next(d for d in documents if d["kind"] == kind and d["metadata"]["name"] == name)


class RenderTests(unittest.TestCase):
    def test_default_install_needs_no_prometheus_operator(self):
        documents = render()
        self.assertFalse(any(d["apiVersion"].startswith("monitoring.coreos.com/") for d in documents))
        config = yaml.safe_load(find(documents, "ConfigMap", "mediamtx")["data"]["mediamtx.yml"])
        self.assertFalse(config["metrics"])
        self.assertEqual(config["authHTTPExclude"], [])

    def test_metrics_without_operator_are_private(self):
        documents = render("monitoring.enabled=true")
        self.assertFalse(any(d["kind"] == "ServiceMonitor" for d in documents))
        self.assertEqual(find(documents, "Service", "mediamtx-metrics")["spec"]["type"], "ClusterIP")
        ports = find(documents, "Service", "mediamtx")["spec"]["ports"]
        self.assertNotIn(9998, [p["port"] for p in ports])
        config = yaml.safe_load(find(documents, "ConfigMap", "mediamtx")["data"]["mediamtx.yml"])
        self.assertEqual(config["authHTTPExclude"], [{"action": "metrics"}])
        self.assertFalse(config["api"])

    def test_operator_resources_are_explicitly_opted_in(self):
        documents = render("monitoring.enabled=true", "monitoring.serviceMonitor.enabled=true",
                           "monitoring.serviceMonitor.additionalLabels.release=kube-prometheus-stack")
        monitor = find(documents, "ServiceMonitor", "mediamtx")
        self.assertEqual(monitor["metadata"]["labels"]["release"], "kube-prometheus-stack")
        self.assertEqual(monitor["spec"]["endpoints"][0]["port"], "metrics")
        render("monitoring.serviceMonitor.enabled=true", succeeds=False)

    def test_webrtc_has_https_signaling_and_public_ice_but_no_public_http_port(self):
        documents = render("webrtc.enabled=true", "webrtc.hostname=contrib.staging.frikanalen.no",
                           "webrtc.additionalHosts[0]=staging.frikanalen.no",
                           "webrtc.allowOrigins[0]=https://staging.frikanalen.no")
        configmap = find(documents, "ConfigMap", "mediamtx")["data"]
        config = yaml.safe_load(configmap["mediamtx.yml"])
        self.assertFalse(config["webrtcIPsFromInterfaces"])
        self.assertEqual(config["webrtcAdditionalHosts"], ["staging.frikanalen.no"])
        self.assertEqual(config["webrtcAllowOrigins"],
                         ["https://contrib.staging.frikanalen.no", "https://staging.frikanalen.no"])
        self.assertNotIn("nginx.conf", configmap)
        ports = find(documents, "Service", "mediamtx")["spec"]["ports"]
        self.assertEqual({p["protocol"] for p in ports if p["port"] == 8189}, {"UDP", "TCP"})
        self.assertFalse(any(p["port"] in (8889, 8080, 9998) for p in ports))
        ingress = find(documents, "Ingress", "mediamtx-webrtc")
        self.assertEqual(ingress["spec"]["tls"][0]["hosts"], ["contrib.staging.frikanalen.no"])
        rule = ingress["spec"]["rules"][0]
        self.assertEqual(rule["host"], "contrib.staging.frikanalen.no")
        self.assertEqual([(p["path"], p["pathType"]) for p in rule["http"]["paths"]], [("/", "Prefix")])
        self.assertFalse(any(d["apiVersion"].startswith("traefik.io/") for d in documents))
        pod = find(documents, "Deployment", "mediamtx")["spec"]["template"]["spec"]
        self.assertEqual([c["name"] for c in pod["containers"]], ["mediamtx"])
        render("webrtc.enabled=true", "webrtc.hostname=staging.frikanalen.no", succeeds=False)

    def test_preview_worker_copies_video_uses_opus_and_secret_references(self):
        source = "11111111-1111-1111-1111-111111111111"
        documents = render("preview.enabled=true", f"preview.sources[0].id={source}",
                           "preview.sources[0].credentialSecret=converter-credential")
        deployment = find(documents, "Deployment", f"mediamtx-preview-{source}")
        pod = deployment["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        converter = pod["containers"][0]
        self.assertTrue(all(e["valueFrom"]["secretKeyRef"]["name"] == "converter-credential"
                            for e in converter["env"]))
        script = converter["args"][0]
        self.assertIn("-c:v copy", script)
        self.assertIn("-c:a libopus -application lowdelay", script)
        self.assertIn("-flush_packets 1 -muxdelay 0", script)
        self.assertIn(f"/preview/{source}", script)
        render("preview.enabled=true", "preview.sources[0].id=invalid", succeeds=False)
        render("preview.enabled=true", f"preview.sources[0].id={source}", succeeds=False)


if __name__ == "__main__":
    unittest.main()
