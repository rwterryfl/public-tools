oc logs -f load-generator-69875cb4c8-jrp8f -n load-generator | \
awk '
{
    match($0, /"timestamp"[[:space:]]*:[[:space:]]*"([^"]+)"/, m)
    if (m[1] == "") next

    # First 19 characters = YYYY-MM-DDTHH:MM:SS
    ts = substr(m[1], 1, 19)

    if (ts == last) {
        count++
    } else {
        if (last != "")
            printf "%s : %d lines/sec\n", last, count

        last = ts
        count = 1
    }
}
END {
    if (last != "")
        printf "%s : %d lines/sec\n", last, count
}'