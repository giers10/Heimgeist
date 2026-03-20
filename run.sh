#!/bin/sh
set -eu

PYTHON_BIN="${PYTHON_BIN:-python3.13}"
VENV_DIR="backend/.venv"
TORCH_FLAVOR_FILE="$VENV_DIR/.heimgeist-torch-flavor"
HEIMGEIST_TORCH_FLAVOR="${HEIMGEIST_TORCH_FLAVOR:-auto}"
HEIMGEIST_TORCH_INDEX_URL="${HEIMGEIST_TORCH_INDEX_URL:-}"

is_linux_x86_64() {
  [ "$(uname -s)" = "Linux" ] && [ "$(uname -m)" = "x86_64" ]
}

has_nvidia_gpu() {
  if command -v nvidia-smi >/dev/null 2>&1; then
    return 0
  fi

  for vendor_file in /sys/class/drm/card*/device/vendor; do
    [ -r "$vendor_file" ] || continue
    if grep -qi "0x10de" "$vendor_file"; then
      return 0
    fi
  done

  return 1
}

is_steam_deck() {
  if [ -r /etc/os-release ] && grep -Eiq '(^ID=steamos$|^NAME=.*SteamOS|^PRETTY_NAME=.*SteamOS)' /etc/os-release; then
    return 0
  fi

  for dmi_file in \
    /sys/devices/virtual/dmi/id/product_name \
    /sys/devices/virtual/dmi/id/product_version \
    /sys/devices/virtual/dmi/id/board_name
  do
    [ -r "$dmi_file" ] || continue
    if grep -Eiq 'steam deck|jupiter|galileo' "$dmi_file"; then
      return 0
    fi
  done

  return 1
}

resolve_torch_flavor() {
  case "$HEIMGEIST_TORCH_FLAVOR" in
    auto)
      if is_linux_x86_64; then
        if is_steam_deck; then
          printf '%s\n' "cpu"
          return
        fi
        if has_nvidia_gpu; then
          printf '%s\n' "default"
          return
        fi
        printf '%s\n' "cpu"
        return
      fi
      printf '%s\n' "default"
      ;;
    default|cpu|cuda|rocm|rocm6.4)
      printf '%s\n' "$HEIMGEIST_TORCH_FLAVOR"
      ;;
    *)
      echo "Unsupported HEIMGEIST_TORCH_FLAVOR '$HEIMGEIST_TORCH_FLAVOR'. Use auto, default, cpu, cuda, rocm, or rocm6.4." >&2
      exit 1
      ;;
  esac
}

install_selected_torch() {
  torch_flavor="$1"

  if [ -n "$HEIMGEIST_TORCH_INDEX_URL" ]; then
    echo "Installing PyTorch from custom index: $HEIMGEIST_TORCH_INDEX_URL"
    "$VENV_DIR/bin/python" -m pip install --upgrade --index-url "$HEIMGEIST_TORCH_INDEX_URL" torch
    return
  fi

  case "$torch_flavor" in
    default|cuda)
      return
      ;;
    cpu)
      echo "Installing CPU-only PyTorch for Whisper"
      "$VENV_DIR/bin/python" -m pip install --upgrade --index-url https://download.pytorch.org/whl/cpu torch
      ;;
    rocm|rocm6.4)
      echo "Installing ROCm PyTorch for Whisper"
      "$VENV_DIR/bin/python" -m pip install --upgrade --index-url https://download.pytorch.org/whl/rocm6.4 torch
      ;;
  esac
}

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3.13 is required. Set PYTHON_BIN to a Python 3.13 executable if needed." >&2
  exit 1
fi

TORCH_FLAVOR="$(resolve_torch_flavor)"
RECREATE_VENV=0

if [ -z "${HEIMGEIST_SETTINGS_FILE:-}" ]; then
  case "$(uname -s)" in
    Darwin)
      HEIMGEIST_SETTINGS_FILE="${HOME}/Library/Application Support/Heimgeist/settings.json"
      ;;
    Linux)
      HEIMGEIST_SETTINGS_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/Heimgeist/settings.json"
      ;;
  esac
  if [ -n "${HEIMGEIST_SETTINGS_FILE:-}" ]; then
    export HEIMGEIST_SETTINGS_FILE
    mkdir -p "$(dirname "$HEIMGEIST_SETTINGS_FILE")"
  fi
fi

if [ ! -x "$VENV_DIR/bin/python" ] || ! "$VENV_DIR/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 1)'; then
  RECREATE_VENV=1
fi

if [ "$RECREATE_VENV" -eq 0 ]; then
  PREVIOUS_TORCH_FLAVOR=""
  if [ -r "$TORCH_FLAVOR_FILE" ]; then
    PREVIOUS_TORCH_FLAVOR="$(cat "$TORCH_FLAVOR_FILE")"
  fi
  if [ -n "$PREVIOUS_TORCH_FLAVOR" ] && [ "$PREVIOUS_TORCH_FLAVOR" != "$TORCH_FLAVOR" ]; then
    RECREATE_VENV=1
  elif [ -z "$PREVIOUS_TORCH_FLAVOR" ] && [ "$TORCH_FLAVOR" != "default" ]; then
    RECREATE_VENV=1
  fi
fi

if [ "$RECREATE_VENV" -eq 1 ]; then
  rm -rf "$VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

printf '%s\n' "$TORCH_FLAVOR" > "$TORCH_FLAVOR_FILE"
echo "Using PyTorch flavor: $TORCH_FLAVOR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
install_selected_torch "$TORCH_FLAVOR"
"$VENV_DIR/bin/python" -m pip install -r backend/requirements.txt
npm install
npm run dev
