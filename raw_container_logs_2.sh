#!/bin/bash

#===================================================================================================
# Example usage:
# $ ./raw_container_logs.sh  -n load-generator -p load-generator-595d55b9d6-x9nzv -c load-generator
#===================================================================================================

while getopts "p:c:n:" opt; do
    case $opt in
        p)
            POD_NAME="$OPTARG"
            ;;
        c)
            CONTAINER_NAME="$OPTARG"
            ;;
        n)
            POD_NAMESPACE="$OPTARG"
            ;;
        \?)
            echo "Invalid option: -$OPTARG" >&2
            exit 1
            ;;
    esac
done

#===================================================================================================
# Get available uncompressed log count for the container
#===================================================================================================

# Get worker node name for pod
WORKER_NODE=$(oc get pod -n $POD_NAMESPACE $POD_NAME -o jsonpath='{.spec.nodeName}')

# Get collector pod on worker node
COLLECTOR_POD=$(oc get pod -n openshift-logging -o wide | grep $WORKER_NODE | grep collector | awk '{print $1}')

LOG_DIR=$(oc exec -n openshift-logging $COLLECTOR_POD -- ls -la /var/log/pods/ | grep $POD_NAME | awk '{print $9}')

LOG_COUNT=$(oc exec -n openshift-logging $COLLECTOR_POD -- sh -c "cat /var/log/pods/$LOG_DIR/$CONTAINER_NAME/*.log*" | \
  grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{9}\+[0-9]{2}:[0-9]{2} stdout F' | wc -l)

# This may result in a race condition for a high volume of logs (Need to find a better way to get the begin and end time of the logs)
END_TIME=$(oc exec -n openshift-logging $COLLECTOR_POD -- sh -c "tail -n1 /var/log/pods/$LOG_DIR/$CONTAINER_NAME/*.log" | awk '{print $1}' | xargs)
BEGIN_TIME=$(oc exec -n openshift-logging $COLLECTOR_POD -- sh -c "head -n1 /var/log/pods/$LOG_DIR/$CONTAINER_NAME/*.log.*" | awk '{print $1}' | xargs)

echo ""
echo "Count of final container log lines between $BEGIN_TIME and $END_TIME:"
echo $LOG_COUNT
echo ""

#===================================================================================================
# Get the max and min of received events for raw_container_logs component
#===================================================================================================

TOKEN=$(oc whoami -t)
ENDPOINT=https://$(oc get route prometheus-k8s -n openshift-monitoring -o=jsonpath='{.spec.host}')/api/v1/query_range
THANOS_QUERIER=https://$(oc get route thanos-querier -n openshift-monitoring -o=jsonpath='{.spec.host}')/api/v1/query_range

QUERY="vector_component_received_events_total{component_id='raw_container_logs',pod_name='$POD_NAME',container_name='$CONTAINER_NAME'}"

# Increment END_TIME by 61 seconds to adjust for scraping delay
END_TIME=$(date -d "$END_TIME + 61 seconds" --iso-8601=seconds -u)

# echo $END_TIME

echo "Max and Min of received events for raw_container_logs component between $BEGIN_TIME and $END_TIME:"
curl -g -s -k -H "Authorization: Bearer $TOKEN" $ENDPOINT \
    --data-urlencode query="$QUERY" \
    --data-urlencode start="$BEGIN_TIME" \
    --data-urlencode end="$END_TIME" \
    --data-urlencode step=1m \
    | jq '[.data.result[].values[][1]] | .[] |= tonumber | {max: max_by(.), min: min_by(.)}'

#===================================================================================================
# Get the max and min of received events for aggregator_log_counts component
#===================================================================================================
QUERY="aggregator_pod_logs_counter{pod_name='$POD_NAME',container_name='$CONTAINER_NAME'}"

# echo $QUERY

echo ""
echo "Max and Min of received events for aggregator component between $BEGIN_TIME and $END_TIME:"
curl -g -s -k -H "Authorization: Bearer $TOKEN" $THANOS_QUERIER \
    --data-urlencode query="$QUERY" \
    --data-urlencode start="$BEGIN_TIME" \
    --data-urlencode end="$END_TIME" \
    --data-urlencode step=1m \
    | jq '[.data.result[].values[][1]] | .[] |= tonumber | {max: max_by(.), min: min_by(.)}'
