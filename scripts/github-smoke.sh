#!/usr/bin/env bash
# Toxiproxy sits at :9001 → api.github.com:443
# Note: TLS termination — for HTTPS upstreams you need to talk plain HTTP
# to the proxy and let it handle the TCP forwarding, OR use a MITM cert.
# Easiest local approach: use httpie with --verify=no against a local cert.

# Without fault:
curl -s --proxy "" http://localhost:9001/repos/octocat/Hello-World \
  -H "Accept: application/vnd.github+json" | python3 -m json.tool | head -20
