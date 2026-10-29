# MediaMTX contribution and preview chart

This chart belongs to GitOps (`helm/mediamtx`). The infra repository owns each
environment's Argo Application, listener addresses, MetalLB reservations and PF
forwarding. The existing Application name and Kubernetes resource names remain
`mediamtx`, so switching repositories does not create a second media service.

## Streams and authentication

- Contributions: `live/<source UUID>`, published over SRT, RTMP or RTSP.
- Derived browser previews: `preview/<source UUID>`, H.264 plus Opus.
- Django authorizes every media connection at `/api/mediamtx/auth`.
- Publish credentials can publish only the contribution. Read credentials can
  read the contribution and derived preview for their own source.
- A source-scoped **Preview conversion worker** credential can only read its
  contribution and publish its preview over RTSP. It cannot replace the original,
  read another source or use administrative actions. Requires django-api PR #81
  including migration `0043_alter_streamcredential_action`.

SRT uses UDP 8890, RTMP TCP 1935 and RTSP interleaved TCP 8554. Infra advertises
those public URLs in Django admin. Anonymous media reads are not enabled.

## WebRTC with standard Ingress

Set `webrtc.enabled`, `webrtc.hostname` and `webrtc.additionalHosts` to enable:

- Player: `https://<site>/preview/<source UUID>/`
- WHEP: `https://<site>/preview/<source UUID>/whep`
- Direct original player/WHEP/WHIP: the same native paths under `/live/<UUID>`.

The standard `networking.k8s.io/v1` Ingress reserves `/live` and `/preview` for
MediaMTX. It preserves paths and Location headers, so WHEP PATCH/DELETE session
URLs work without a proxy sidecar, rewrite middleware or IngressRoute CRD.
Traefik terminates HTTPS using the site's existing hostname/certificate. Its
certificate-resolver annotation is configurable. The frontend must not use
these reserved prefixes for ordinary pages.

Use a Read credential in the player or WHEP client's Basic authentication, or
its `username:password` bearer token. Django sessions are not preview credentials;
a frontend authorization/token endpoint is separate work. Source preview proves
reception, not that a feed is on air. A TX return requires publishing the channel
output as another source.

ICE media uses UDP 8189 and TCP 8189 fallback, forwarded by PF to the MediaMTX
LoadBalancer. Only the public hostname and reserved LAN address are advertised;
pod-interface addresses are not. No TURN relay is included. Networks blocking
both direct transports need a later TURN deployment.

## Low-latency Opus conversion

Configure one worker per source to keep the original contribution intact:

```yaml
preview:
  enabled: true
  sources:
    - id: 11111111-1111-1111-1111-111111111111
      credentialSecret: studio-preview-credential
```

Issue a Preview conversion worker credential in Django admin, then create a
Kubernetes Secret with `username` and `password` keys. Store only the Secret name
in Git/Helm values. The original password cannot be recovered from Django after
issuance. Rotate by replacing the Secret and restarting that source's worker.
For example, use `kubectl create secret generic ... --from-env-file=<secure-file>`;
do not paste secrets into issues or shell history.

Each worker copies video (`-c:v copy`) and converts audio to 48kHz stereo Opus,
using restricted low-delay mode, 10ms frames, prompt packet flushing and no
additional video encode. It reconnects if the contribution disconnects. Short
input probing limits startup buffering; encoder GOP length still affects the
first decodable picture. Source video must already be browser-compatible H.264
without B-frames. Video normalization is not included in this audio converter.

Workers are configured per source for now, not auto-created for every Django
source. A future controller can manage the same source/Secret declarations.
Only configured sources get Opus-preview output. Direct H.264/AAC originals may
provide WebRTC video without audio; use the converted preview for both tracks.
The worker suppresses FFmpeg diagnostics that could include credential URLs;
MediaMTX path/packet metrics indicate whether its preview is receiving data.

## Optional Prometheus Operator support

No monitoring resources are enabled by default. These switches are independent:

```yaml
monitoring:
  enabled: true                 # internal metrics endpoint and ClusterIP Service
  serviceMonitor:
    enabled: false              # no Operator or monitoring.coreos.com CRDs needed
```

Set `monitoring.serviceMonitor.enabled: true` only where Prometheus Operator is
installed. It supports `interval`, `scrapeTimeout` and `additionalLabels`, such
as `release: kube-prometheus-stack`. Enabling a ServiceMonitor while metrics are
disabled is rejected. Metrics port 9998 is not on the public LoadBalancer or
Ingress. Only the internal metrics action is excluded from Django HTTP auth;
media publish/read checks remain enforced. The management API remains disabled.

## Deployment and validation

The infra app definition provides callback address, environment hostname, LAN
address, source worker declarations and the cluster's Operator capability flag.
An initial staging trial may track this chart's feature branch; after merge,
reapply infra's app definitions to restore main tracking. Production requires a
released Django image containing the callback and converter role first.

```sh
helm lint helm/mediamtx \
  --set authURL=http://django-api.default.svc.cluster.local:8080/api/mediamtx/auth \
  --set service.loadBalancerIP=192.168.3.93
python helm/mediamtx/tests/check_render.py
```

CI exercises monitoring-off, metrics-without-Operator, optional ServiceMonitor,
standard WebRTC Ingress, private HTTP/metrics listeners and preview-worker
rendering. Missing callback/IP/candidate/worker-secret settings fail closed.

The initial public staging SRT/RTMP/RTSP test matrix passed with H.264/AAC media:
SRT to SRT/RTMP/RTSP, and RTMP/RTSP back to SRT. The infrastructure PR records the
staging rollout and all later WebRTC/conversion verification results. No source
is placed on air by this chart.

Recording is disabled. MediaMTX forbids always-available fallback on regex paths;
explicit per-source paths and capture/finalization remain controller work.
