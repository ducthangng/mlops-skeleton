TITLE="Burst from one client -> 429 + Retry-After, other clients unaffected"
BREAKS="15 requests in a burst from X-API-Key: drill-burst against router-dev (rate 5/s, burst 10)"
OBSERVE=("first ~10 requests 200, then 429 (token bucket empty)"
         "Retry-After header on the 429; a different X-API-Key still gets 200"
         "metric gateway_rate_limited_total increases")
RESTORE="nothing: the bucket refills at 5 tokens/s"
do_break() {
  incluster router-dev 'for i in $(seq 1 15); do curl -s -o /dev/null -w "%{http_code} " http://gw-router/route -H "content-type: application/json" -H "X-API-Key: drill-burst" -d "{\"prompt\":\"q$i\"}"; done; echo
curl -s -D - -o /dev/null http://gw-router/route -H "content-type: application/json" -H "X-API-Key: drill-burst" -d "{\"prompt\":\"again\"}" | grep -iE "^(HTTP|retry-after)"
curl -s -o /dev/null -w "other client: %{http_code}\n" http://gw-router/route -H "content-type: application/json" -H "X-API-Key: someone-else" -d "{\"prompt\":\"hi\"}"
curl -s http://gw-router/metrics | grep "^gateway_rate_limited_total"'
}
do_fix() { echo "    bucket refills by itself"; }
