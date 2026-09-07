# shellcheck shell=bash
# Loaded by ../stack.sh; functions share the stack namespace.

knowledge_setup() {
  python3 -m venv "$ROOT/.venv-knowledge" || return $?
  "$ROOT/.venv-knowledge/bin/python" -m pip install \
    -r "$ROOT/deploy/dev/requirements-knowledge.txt" || return $?
  env -u KNOWLEDGE_PYTHON make -C "$BACKEND" knowledge-setup || return $?
  env -u KNOWLEDGE_PYTHON make -C "$FRONTEND" knowledge-setup
}

knowledge_ready() {
  command -v "$KNOWLEDGE_PYTHON" >/dev/null || {
    echo 'Run just knowledge-setup first (or set KNOWLEDGE_PYTHON)' >&2
    return 2
  }
}

knowledge_check() {
  knowledge_ready || return $?
  (
    cd "$ROOT"
    PYTHONDONTWRITEBYTECODE=1 "$KNOWLEDGE_PYTHON" -m unittest -v \
      deploy.dev.tests.test_workspace_checks
  ) || return $?
  PYTHONDONTWRITEBYTECODE=1 "$KNOWLEDGE_PYTHON" "$ROOT/deploy/dev/workspace_checks.py" \
    knowledge --root "$ROOT" --backend "$BACKEND" --frontend "$FRONTEND"
}

contract_check() {
  knowledge_ready || return $?
  PYTHONDONTWRITEBYTECODE=1 "$KNOWLEDGE_PYTHON" "$ROOT/deploy/dev/workspace_checks.py" \
    contract --root "$ROOT" --backend "$BACKEND" --frontend "$FRONTEND"
}


test_dev() {
  knowledge_ready || return $?
  (
    cd "$ROOT" || exit $?
    PYTHONDONTWRITEBYTECODE=1 "$KNOWLEDGE_PYTHON" -m unittest discover \
      -s deploy/dev/tests -t . -p 'test_*.py' -v
  )
}
