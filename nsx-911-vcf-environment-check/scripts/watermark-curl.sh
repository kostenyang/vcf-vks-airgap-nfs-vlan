#!/usr/bin/env bash
# NSX standalone -> 9.x : vcfEnvironmentCheck (31944) bypass test via /api/v1/watermark  (lab only, unsupported)
# 2026-10-22 m07 (manually deployed NSX 4.2.1, never in VCF)
#   bash watermark-curl.sh get|post|precheck|failures|delete
# NSX_PASS must be set in the environment (admin password); NSX defaults to m07 node IP.
set -euo pipefail
NSX=${NSX:-10.0.1.166}
A="admin:${NSX_PASS:?set NSX_PASS}"
H='Content-Type: application/json'

case "${1:-get}" in
  get)       # 404 (error 600) = no watermark = UC 9.x blocks with NOT_VCF_ENVIRONMENT
    curl -sk -u "$A" "https://$NSX/api/v1/watermark"; echo ;;
  post)      # same shape Cloud Builder 5.2.1 wrote on m06 (see RESULTS.md); UC only checks that the record exists
    curl -sk -u "$A" -X POST -H "$H" "https://$NSX/api/v1/watermark" -d "{
      \"deployed_by\": \"VMware Cloud Foundation - VSRN\",
      \"version\": \"5.2.1.0-24307856\",
      \"managed_by\": \"vcf-m07-sddcm01.home.lab\",
      \"instance_id\": \"$(cat /proc/sys/kernel/random/uuid 2>/dev/null || powershell -NoProfile -Command '[guid]::NewGuid().ToString()' | tr -d '\r')\",
      \"log_token\": \"VCF\"
    }"; echo ;;
  precheck)  # re-run UC pre-upgrade checks (bundle + UC 9.1.1 already in place)
    curl -sk -u "$A" -X POST -H "$H" "https://$NSX/api/v1/upgrade?action=execute_pre_upgrade_checks"; echo
    echo "poll: curl -sk -u admin:*** https://$NSX/api/v1/upgrade/status-summary" ;;
  status)
    curl -sk -u "$A" "https://$NSX/api/v1/upgrade/status-summary" | python -c 'import sys,json; d=json.load(sys.stdin); [print(c["component_type"], c["pre_upgrade_status"]["status"]) for c in d["component_status"]]' ;;
  failures)
    curl -sk -u "$A" "https://$NSX/api/v1/upgrade/pre-upgrade-checks/failures" | python -c 'import sys,json; d=json.load(sys.stdin); print("count", d["result_count"]); [print(f"[{r[\"type\"]}] {r[\"message\"][\"error_code\"]} ack={r.get(\"needs_ack\")} {r[\"message\"][\"message\"][:140]}") for r in d["results"]]' ;;
  delete)    # roll back: remove the watermark again (API spec: DELETE on /watermark itself)
    curl -sk -u "$A" -X DELETE "https://$NSX/api/v1/watermark" -w 'HTTP %{http_code}\n' ;;
esac
