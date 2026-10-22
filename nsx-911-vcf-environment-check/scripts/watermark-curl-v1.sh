#!/usr/bin/env bash
# NSX standalone -> 9.x : vcfEnvironmentCheck (31944) bypass test via /api/v1/watermark  (lab only, unsupported)
# v1 2026-10-22: interactive prompts (NSX address / password / post fields) + y/N confirm on post and delete; original = watermark-curl.sh
#   bash watermark-curl-v1.sh [get|post|precheck|status|failures|delete]
# Prompts for the NSX Manager address and admin password. Set these in the environment to skip the prompts
# (e.g. when scripting): NSX (IP/FQDN), NSX_USER (default admin), NSX_PASS, and for post: MANAGED_BY, WM_VERSION.
set -euo pipefail

cmd=${1:-get}
case "$cmd" in
  get|post|precheck|status|failures|delete) ;;
  *) echo "usage: $0 get|post|precheck|status|failures|delete" >&2; exit 1 ;;
esac

# --- connection details: prompt for anything not given in the environment ---
if [[ -z "${NSX:-}" ]]; then read -rp "NSX Manager IP or FQDN: " NSX; fi
[[ -n "$NSX" ]] || { echo "NSX Manager address is required" >&2; exit 1; }
NSX_USER=${NSX_USER:-admin}
if [[ -z "${NSX_PASS:-}" ]]; then read -rsp "Password for $NSX_USER@$NSX: " NSX_PASS; echo; fi
[[ -n "$NSX_PASS" ]] || { echo "password is required" >&2; exit 1; }
A="$NSX_USER:$NSX_PASS"
H='Content-Type: application/json'
PY=$(command -v python3 || command -v python) || { echo "python not found (needed for status/failures)" >&2; exit 1; }

new_uuid() {   # Linux: uuidgen or /proc; Git Bash on Windows: neither exists, fall back to python
  uuidgen 2>/dev/null || cat /proc/sys/kernel/random/uuid 2>/dev/null || "$PY" -c 'import uuid; print(uuid.uuid4())'
}
confirm() { local a; read -rp "$1 [y/N]: " a; [[ $a =~ ^[Yy]$ ]] || { echo "aborted"; exit 0; }; }

case "$cmd" in
  get)       # 404 (error 600) = no watermark = UC 9.x blocks with NOT_VCF_ENVIRONMENT
    curl -sk -u "$A" "https://$NSX/api/v1/watermark" -w '\nHTTP %{http_code}\n' ;;
  post)      # same shape Cloud Builder 5.2.1 wrote on m06 (see RESULTS.md); UC only checks that the record exists
    if [[ -z "${MANAGED_BY:-}" ]]; then read -rp "managed_by, SDDC Manager FQDN to record [sddcm.example.local]: " MANAGED_BY; fi
    MANAGED_BY=${MANAGED_BY:-sddcm.example.local}
    if [[ -z "${WM_VERSION:-}" ]]; then read -rp "version, Cloud Builder / SDDC Manager build [5.2.1.0-24307856]: " WM_VERSION; fi
    WM_VERSION=${WM_VERSION:-5.2.1.0-24307856}
    confirm "POST watermark (deployed_by=VCF, managed_by=$MANAGED_BY, version=$WM_VERSION) to $NSX? lab only, unsupported"
    curl -sk -u "$A" -X POST -H "$H" "https://$NSX/api/v1/watermark" -w '\nHTTP %{http_code}\n' -d "{
      \"deployed_by\": \"VMware Cloud Foundation - VSRN\",
      \"version\": \"$WM_VERSION\",
      \"managed_by\": \"$MANAGED_BY\",
      \"instance_id\": \"$(new_uuid | tr -d '\r')\",
      \"log_token\": \"VCF\"
    }" ;;
  precheck)  # re-run UC pre-upgrade checks (bundle + UC 9.x already in place)
    curl -sk -u "$A" -X POST -H "$H" "https://$NSX/api/v1/upgrade?action=execute_pre_upgrade_checks" -w '\nHTTP %{http_code}\n'
    echo "then: bash $0 status   (repeat until every component shows COMPLETED), then: bash $0 failures" ;;
  status)
    curl -sk -u "$A" "https://$NSX/api/v1/upgrade/status-summary" | "$PY" -c 'import sys,json; d=json.load(sys.stdin); cs=d["component_status"] if isinstance(d,dict) else d; [print(c["component_type"], c["pre_upgrade_status"]["status"]) for c in cs]' ;;
  failures)
    curl -sk -u "$A" "https://$NSX/api/v1/upgrade/pre-upgrade-checks/failures" | "$PY" -c 'import sys,json; d=json.load(sys.stdin); print("count", d["result_count"]); [print("[%s] %s ack=%s %s" % (r["type"], r["message"]["error_code"], r.get("needs_ack"), r["message"]["message"][:140])) for r in d["results"]]' ;;
  delete)    # roll back: remove the watermark again (API spec: DELETE on /watermark itself); GET afterwards should be 404
    confirm "DELETE watermark on $NSX?"
    curl -sk -u "$A" -X DELETE "https://$NSX/api/v1/watermark" -w 'HTTP %{http_code}\n' ;;
esac
