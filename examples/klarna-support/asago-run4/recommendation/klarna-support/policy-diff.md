# Policy diff: before -> after

- **No sandbox:** there are no rules at all, so every action reaches its target.
- **OpenShell default -> recommended:** the default has no network rules (everything denied, so the agent can't reach its model or tools). The recommendation adds exactly the use case's endpoints (`+`) and tightens the filesystem and process.
- The MCP tool server, its tool allowlist and the token injection are in `provider-profile.yaml`.

```diff
--- openshell-default (observed)
+++ recommended/policy.yaml
@@ -1,7 +1,6 @@
 version: 1
 filesystem_policy:
-  include_workdir: true
+  include_workdir: false
   read_only:
-  - /bin
   - /usr
   - /lib
@@ -10,7 +9,32 @@
   - /etc
   - /var/log
+  - /opt
   read_write:
+  - /sandbox/workdir
   - /tmp
   - /dev/null
+  - /dev/shm
 landlock:
   compatibility: best_effort
+process:
+  run_as_user: '1500'
+  run_as_group: '1500'
+network_policies:
+  model:
+    endpoints:
+    - host: host.openshell.internal
+      port: 11434
+    binaries:
+    - path: /opt/app-root/.venv/bin/python-ogx
+  http_shipping_status:
+    endpoints:
+    - host: host.openshell.internal
+      port: 9101
+      protocol: rest
+      enforcement: enforce
+      rules:
+      - allow:
+          method: GET
+          path: /shipments/*
+    binaries:
+    - path: /opt/app-root/.venv/bin/python-mcp
```
