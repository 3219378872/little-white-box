# Workspace justfile entrypoint. Source this file; do not execute it.
# shellcheck shell=bash

# Module lookup is independent of the caller's cwd and business ROOT override.
# The list below is explicit (no glob): config.sh must stay first because it is
# the only module that runs code at source time; the rest only define functions.
_XBH_STACK_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"

# shellcheck source=lib/config.sh
source "${_XBH_STACK_LIB_DIR}/config.sh" || return $?
# shellcheck source=lib/env.sh
source "${_XBH_STACK_LIB_DIR}/env.sh" || return $?
# shellcheck source=lib/db_credentials.sh
source "${_XBH_STACK_LIB_DIR}/db_credentials.sh" || return $?
# shellcheck source=lib/runtime_config.sh
source "${_XBH_STACK_LIB_DIR}/runtime_config.sh" || return $?
# shellcheck source=lib/process_identity.sh
source "${_XBH_STACK_LIB_DIR}/process_identity.sh" || return $?
# shellcheck source=lib/process_control.sh
source "${_XBH_STACK_LIB_DIR}/process_control.sh" || return $?
# shellcheck source=lib/readiness.sh
source "${_XBH_STACK_LIB_DIR}/readiness.sh" || return $?
# shellcheck source=lib/middleware.sh
source "${_XBH_STACK_LIB_DIR}/middleware.sh" || return $?
# shellcheck source=lib/proxy.sh
source "${_XBH_STACK_LIB_DIR}/proxy.sh" || return $?
# shellcheck source=lib/frontend.sh
source "${_XBH_STACK_LIB_DIR}/frontend.sh" || return $?
# shellcheck source=lib/fixtures.sh
source "${_XBH_STACK_LIB_DIR}/fixtures.sh" || return $?
# shellcheck source=lib/ops.sh
source "${_XBH_STACK_LIB_DIR}/ops.sh" || return $?
# shellcheck source=lib/checks.sh
source "${_XBH_STACK_LIB_DIR}/checks.sh" || return $?
# shellcheck source=lib/lifecycle.sh
source "${_XBH_STACK_LIB_DIR}/lifecycle.sh" || return $?

unset _XBH_STACK_LIB_DIR
