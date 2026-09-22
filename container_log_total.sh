#!/bin/bash

DURATION_SECS=30
USE_EMBEDDED_TIMESTAMP=false
TOTAL_COUNT=0
VALIDATE_JSON=false
VALID_JSON_COUNT=0

#===================================================================================================
# Example usage:
# $ ./container_log_total.sh  -n load-generator -p load-generator-595d55b9d6-b9hxx -c load-generator -t -j -d 60
#
# Options:
# -p: Pod name
# -c: Container name
# -n: Pod namespace
# -d: Duration in seconds
# -t: Use embedded timestamp (application provided timestamp in the log message)
# -j: Validate JSON document
#===================================================================================================

while getopts "p:c:n:d:tj" opt; do
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
        d)
            DURATION_SECS="$OPTARG"
            ;;
        t)
            USE_EMBEDDED_TIMESTAMP=true
            ;;
        j)
            VALIDATE_JSON=true
            ;;
        \?)
            echo "Invalid option: -$OPTARG" >&2
            exit 1
            ;;
    esac
done

FIRST_LINE=true
CMD="oc logs -f $POD_NAME -n $POD_NAMESPACE -c $CONTAINER_NAME --tail=1"

# If not using the embedded timestamp, add --timestamps option to the query
if [ "$USE_EMBEDDED_TIMESTAMP" = false ]; then
    CMD="$CMD --timestamps"
fi

# Execute command and read output line by line
while IFS= read -r line; do

    # Capture raw log count prior to any validation
    TOTAL_COUNT=$((TOTAL_COUNT + 1))

    # Validate json document if required
    if [ "$VALIDATE_JSON" = true ]; then
        echo $line | jq > /dev/null 2>&1

        if [ $? -eq 0 ]; then
            VALID_JSON_COUNT=$((VALID_JSON_COUNT + 1))
        else
            # Skip invalid json document
            continue
        fi
    fi

    if [ "$FIRST_LINE" = true ]; then
        FIRST_LINE=false
        # Extract timestamp from the first line as the start time for this collection
        if [ "$USE_EMBEDDED_TIMESTAMP" = false ]; then
            # Extract timestamp from the first line
            START_TIME=$(echo $line | awk '{print $1}')
            START_DATETIME=$(date -d "$START_TIME" +%s)
            PROPOSED_END_DATETIME=$(($START_DATETIME + $DURATION_SECS))
        else
            # Extract timestamp from the first line
            START_TIME=$(echo $line | jq -r '.timestamp')
            START_DATETIME=$(date -d "$START_TIME" +%s)
            PROPOSED_END_DATETIME=$(($START_DATETIME + $DURATION_SECS))
        fi
    fi

    if [ "$USE_EMBEDDED_TIMESTAMP" = false ]; then
        CURRENT_TIME=$(echo $line | awk '{print $1}')
    else
        CURRENT_TIME=$(echo $line | jq -r '.timestamp')
    fi

    # If the current time is greater than the proposed end time, exit the loop
    CURRENT_DATETIME=$(date -d "$CURRENT_TIME" +%s)
    if [ $CURRENT_DATETIME -gt $PROPOSED_END_DATETIME ]; then
        echo "Start time: $START_TIME"
        echo "End time: $CURRENT_TIME"
        echo "Total count: $TOTAL_COUNT"

        if [ "$VALIDATE_JSON" = true ]; then
            echo "Valid JSON count: $VALID_JSON_COUNT"
        fi
        
        break
    fi

    echo $CURRENT_TIME

done < <($CMD)

