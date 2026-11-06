# container_log_counter_agent

All-in-one manifest for the Vector log line counter, derived from
[playbooks/container_log_counter_rh_image.yaml](../playbooks/container_log_counter_rh_image.yaml).

Unlike the playbook, which counts log lines for a single pod, this agent counts
log lines for **every pod and container in a namespace**. The DaemonSet runs on
every node (it tolerates all taints), so pods are counted wherever they are scheduled.

It creates these objects in `openshift-logging`:

| Kind           | Name                               |
|----------------|------------------------------------|
| ConfigMap      | `<namespace>-line-counter-vector`  |
| DaemonSet      | `<namespace>-line-counter-vector`  |
| Service        | `<namespace>-line-counter`         |
| ServiceMonitor | `<namespace>-line-counter`         |

## Placeholders

| Placeholder     | Source                                                             |
|-----------------|--------------------------------------------------------------------|
| `${NAMESPACE}`  | Namespace whose log lines are counted                              |
| `${IMAGE_NAME}` | Image of the first container in an openshift-logging collector pod |

## Deploy

```bash
export NAMESPACE=load-generator

# Same image lookup the playbook does
export IMAGE_NAME=$(oc get pods -n openshift-logging \
  -l app.kubernetes.io/component=collector \
  -o jsonpath='{.items[0].spec.containers[0].image}')

# Only substitute these two variables
envsubst '${NAMESPACE} ${IMAGE_NAME}' \
  < container_log_counter_agent.yaml | oc apply -f -
```

## Remove

Run the same `envsubst` command with `oc delete -f -` instead of `oc apply -f -`.

## Notes

- Vector reads `/var/log/pods/<namespace>_*/*/*.log`, so it counts all
  containers in all pods of the namespace, including pods created after deployment.
- Lines containing `/health/liveness` are counted in the
  `line_counter_health_liveness_lines_total` counter (filter logic borrowed from
  [example_line_counter.yaml](example_line_counter.yaml)). The counter has a
  `pod_name` label for the pod that wrote the line. (`pod` is the agent pod,
  set by Prometheus.) Vector's internal metrics are still exported too.

  ```promql
  sum by (pod_name) (rate(line_counter_health_liveness_lines_total[5m]))
  ```
- Each node exposes its own count. To get the total for the namespace, sum
  the metric across the agent pods in Prometheus.
- The DaemonSet uses the `app-logs` service account, which is hardcoded as in the playbook.
