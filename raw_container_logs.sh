#!/bin/bash

# $ ./raw_container_logs.sh  -n load-generator -p load-generator-595d55b9d6-x9nzv -c load-generator -b "2024-05-02T11:35:58" -e "2024-05-02T11:36:04"

while getopts "p:c:n:b:e:" opt; do
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
        b)
            BEGIN_TIME="$OPTARG"
            ;;
        e)
            END_TIME="$OPTARG"
            ;;
        \?)
            echo "Invalid option: -$OPTARG" >&2
            exit 1
            ;;
    esac
done

# Get worker node name for pod
WORKER_NODE=$(oc get pod -n $POD_NAMESPACE $POD_NAME -o jsonpath='{.spec.nodeName}')

# Get collector pod on worker node
COLLECTOR_POD=$(oc get pod -n openshift-logging -o wide | grep $WORKER_NODE | grep collector | awk '{print $1}')

LOG_DIR=$(oc exec -n openshift-logging $COLLECTOR_POD -- ls -la /var/log/pods/ | grep $POD_NAME | awk '{print $9}')

# Count final container log lines between BEGIN_TIME and END_TIME
echo "Count of final container log lines between $BEGIN_TIME and $END_TIME:"
oc exec -n openshift-logging $COLLECTOR_POD -- cat /var/log/pods/$LOG_DIR/$CONTAINER_NAME/0.log | \
  awk -v d1="$BEGIN_TIME" -v d2="$END_TIME" '($1) >= d1 && ($1) <= d2' | \
  grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{9}\+[0-9]{2}:[0-9]{2} stdout F' | wc -l

