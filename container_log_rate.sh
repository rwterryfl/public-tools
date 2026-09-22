#!/bin/bash

#==============================================================================
# Usage:
# $ ./container_log_rate.sh -n load-generator -p load-generator-595d55b9d6-f2w6n -c load-generator
#==============================================================================

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

SLICE_TIME_SEC=30

#==============================================================================
# Download logs from a specific container in a pod
#==============================================================================
echo ""
echo "Downloading logs..."
echo ""
 
# Get worker node name for pod
WORKER_NODE=$(oc get pod -n $POD_NAMESPACE $POD_NAME -o jsonpath='{.spec.nodeName}')

# Get collector pod on worker node
COLLECTOR_POD=$(oc get pod -n openshift-logging -o wide | grep $WORKER_NODE | grep collector | awk '{print $1}')

# Get container log direcgory
LOG_DIR=$(oc exec -n openshift-logging $COLLECTOR_POD -- ls -la /var/log/pods/ | grep $POD_NAME | awk '{print $9}')

# Get the uncompressed logs (sort by time reversed):
CONTAINER_LOG_FILES=($(oc exec -n openshift-logging $COLLECTOR_POD -- sh -c "ls -tr /var/log/pods/$LOG_DIR/$CONTAINER_NAME/* | grep -v '**.gz$'"))

# Copy uncompressed logs from collector to ./logs/$POD_NAME
# Note: tar executable not present in collector image. Add indicator
#       at beginning of file name to represent order.
mkdir -p ./logs/$POD_NAME && \
INDEX=1 && \
for file in "${CONTAINER_LOG_FILES[@]}"
do 
    LOG_FILE=$(IFS='/' read -ra LOG <<< "$file"; echo ${LOG[-1]})
    oc exec -it -n openshift-logging $COLLECTOR_POD -- sh -c "cat $file" > ./logs/${POD_NAME}/${INDEX}_${LOG_FILE}
    ((INDEX++)) 
done

#==============================================================================
# Extract metrics from downloaded logs
#==============================================================================
echo ""
echo "Extracting metrics..."
echo ""

LOG_FILES=($(ls ./logs/${POD_NAME}/*))

FIRST_LINE=true
COUNT=0
FIRST_SLICE=true
PREVIOUS_SLICE_COUNT=0

for file in "${LOG_FILES[@]}"
do
    while IFS= read -r line
    do
        if [ "$FIRST_LINE" = true ]; then
            # SLICE_BEGIN_TIME=$(echo $line | awk '{print $1}' | xargs)
            IFS=' ' read -ra LOG <<< "$line"
            SLICE_BEGIN_TIME=${LOG[0]}
            SLICE_END_TIME=$(date -d "$SLICE_BEGIN_TIME + $SLICE_TIME_SEC seconds" --iso-8601=ns -u)
            FIRST_LINE=false
            # echo "First Line"
            # continue
        fi

        # If line matches the grep pattern then process
        if [[ $line =~ " stdout F " ]]; then
            IFS=' ' read -ra LOG <<< "$line"
            LINE_TIME=${LOG[0]}
            if [ "$LINE_TIME" \< "$SLICE_END_TIME" ]; then
                ((COUNT++))
            else
                if [ "$FIRST_SLICE" = true ]; then
                    FIRST_SLICE=false
                else
                    echo "$SLICE_END_TIME $COUNT"
                    echo "Rate: $(( $((COUNT - PREVIOUS_SLICE_COUNT)) / $SLICE_TIME_SEC))"
                    PREVIOUS_SLICE_COUNT=$COUNT
                    ((COUNT++))
                    SLICE_BEGIN_TIME=$SLICE_END_TIME
                    SLICE_END_TIME=$(date -d "$SLICE_END_TIME + $SLICE_TIME_SEC seconds" --iso-8601=ns -u)
                fi
            fi
        fi
    done < "$file"
    # Throw away the last partial slice
    # echo "Final count including partial slice: $COUNT"
done

