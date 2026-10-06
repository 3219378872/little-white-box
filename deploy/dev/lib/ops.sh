# shellcheck shell=bash
# Loaded by ../stack.sh; functions share the stack namespace.
#
# Operator commands that act on stack data rather than on processes.

# Review roles are granted only through this ops path (RVW-050): the backend
# rolectl writes xbh_review.reviewer plus an audit row; no online endpoint exists.
#   review_role grant <userId> <roles> [markets] [languages]
#   review_role revoke <userId>
review_role() {
  local action="${1:-}" user="${2:-}"
  if [[ ! "$action" =~ ^(grant|revoke)$ || ! "$user" =~ ^[1-9][0-9]*$ ]]; then
    echo "usage: just review-role grant <userId> <roles> [markets] [languages] | revoke <userId>" >&2
    return 2
  fi
  load_env || return $?
  ensure_assistant_db_env || return $?
  local -a args=("$action" -user "$user")
  if [[ "$action" == grant ]]; then
    local roles="${3:-}"
    [[ -n "$roles" ]] || {
      echo "review-role grant needs roles (reviewer,qa,policy_admin,qualification_reviewer)" >&2
      return 2
    }
    args+=(-roles "$roles" -markets "${4:-US,DE,ID}" -languages "${5:-en,de,id}")
  fi
  (cd "$BACKEND" && go run ./app/review/rolectl "${args[@]}")
}
