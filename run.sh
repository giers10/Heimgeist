#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_BIN_WAS_SET=0
if [ -n "${PYTHON_BIN:-}" ]; then
  PYTHON_BIN_WAS_SET=1
fi
PYTHON_BIN="${PYTHON_BIN:-python3.13}"
VENV_DIR="backend/.venv"
TORCH_FLAVOR_FILE="$VENV_DIR/.heimgeist-torch-flavor"
TORCH_STATE_FILE="$VENV_DIR/.heimgeist-torch-state"
PYTHON_DEPS_STATE_FILE="$VENV_DIR/.heimgeist-python-deps-state"
NODE_DEPS_STATE_FILE="node_modules/.heimgeist-node-deps-state"
HEIMGEIST_NODE_MAJOR="${HEIMGEIST_NODE_MAJOR:-22}"
HEIMGEIST_NODE_DIR="${HEIMGEIST_NODE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/heimgeist/node-v$HEIMGEIST_NODE_MAJOR}"
HEIMGEIST_TOOL_BIN_DIR="${HEIMGEIST_TOOL_BIN_DIR:-$HOME/.local/bin}"
HEIMGEIST_SKIP_SYSTEM_DEPS="${HEIMGEIST_SKIP_SYSTEM_DEPS:-0}"
HEIMGEIST_BOOTSTRAP_ONLY="${HEIMGEIST_BOOTSTRAP_ONLY:-0}"
HEIMGEIST_TORCH_FLAVOR="${HEIMGEIST_TORCH_FLAVOR:-auto}"
HEIMGEIST_TORCH_INDEX_URL="${HEIMGEIST_TORCH_INDEX_URL:-}"
HEIMGEIST_FORCE_BOOTSTRAP="${HEIMGEIST_FORCE_BOOTSTRAP:-0}"
CARGO_HOME="${CARGO_HOME:-$HOME/.cargo}"
RUSTUP_HOME="${RUSTUP_HOME:-$HOME/.rustup}"
PATH="$HEIMGEIST_TOOL_BIN_DIR:$CARGO_HOME/bin:$PATH"
export CARGO_HOME RUSTUP_HOME PATH

as_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
    return
  fi
  if ! command -v sudo >/dev/null 2>&1; then
    echo "Installing native build dependencies requires root access, but sudo is unavailable." >&2
    exit 1
  fi
  sudo "$@"
}

is_steamos() {
  [ -r /etc/os-release ] \
    && grep -Eiq '(^ID=steamos$|^NAME=.*SteamOS|^PRETTY_NAME=.*SteamOS)' /etc/os-release
}

linux_native_deps_usable() {
  command -v cc >/dev/null 2>&1 \
    && command -v file >/dev/null 2>&1 \
    && command -v pkg-config >/dev/null 2>&1 \
    && pkg-config --exists webkit2gtk-4.1 gtk+-3.0 openssl librsvg-2.0
}

STEAMOS_RESTORE_READONLY=0

restore_steamos_readonly() {
  if [ "$STEAMOS_RESTORE_READONLY" -eq 1 ]; then
    echo "Re-enabling the SteamOS read-only filesystem"
    as_root steamos-readonly enable
    STEAMOS_RESTORE_READONLY=0
  fi
}

prepare_steamos_package_install() {
  if ! is_steamos || ! command -v steamos-readonly >/dev/null 2>&1; then
    return
  fi

  readonly_status="$(steamos-readonly status 2>/dev/null || true)"
  if printf '%s\n' "$readonly_status" | grep -qi enabled; then
    echo "Temporarily disabling the SteamOS read-only filesystem"
    as_root steamos-readonly disable
    STEAMOS_RESTORE_READONLY=1
  fi
}

install_arch_native_deps() {
  set -- \
    webkit2gtk-4.1 \
    base-devel \
    curl \
    wget \
    file \
    openssl \
    appmenu-gtk-module \
    libappindicator-gtk3 \
    librsvg \
    xdotool

  missing_packages=""
  for package_name in "$@"; do
    if ! pacman -Q "$package_name" >/dev/null 2>&1; then
      missing_packages="$missing_packages $package_name"
    fi
  done
  if [ -z "$missing_packages" ]; then
    return
  fi

  echo "Installing missing Tauri/Arch packages:$missing_packages"
  echo "SteamOS/Arch may ask for your sudo password."
  prepare_steamos_package_install
  trap 'restore_steamos_readonly' EXIT
  trap 'restore_steamos_readonly; exit 130' HUP INT TERM

  install_status=0
  if is_steamos; then
    # SteamOS system updates own the base image, so only install missing packages.
    as_root pacman -S --needed --noconfirm $missing_packages || install_status=$?
  else
    # Arch requires a full upgrade when synchronizing/installing packages.
    as_root pacman -Syu --needed --noconfirm $missing_packages || install_status=$?
  fi

  restore_steamos_readonly
  trap - EXIT HUP INT TERM
  return "$install_status"
}

install_debian_native_deps() {
  set -- \
    libwebkit2gtk-4.1-dev \
    build-essential \
    curl \
    wget \
    file \
    libxdo-dev \
    libssl-dev \
    libayatana-appindicator3-dev \
    librsvg2-dev \
    pkg-config

  missing_packages=""
  for package_name in "$@"; do
    if ! dpkg-query -W -f='${Status}' "$package_name" 2>/dev/null | grep -q 'ok installed'; then
      missing_packages="$missing_packages $package_name"
    fi
  done
  if [ -z "$missing_packages" ]; then
    return
  fi

  echo "Installing missing Tauri/Debian packages:$missing_packages"
  as_root apt-get update
  as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y $missing_packages
}

ensure_native_build_dependencies() {
  if [ "$HEIMGEIST_SKIP_SYSTEM_DEPS" = "1" ]; then
    return
  fi

  case "$(uname -s)" in
    Darwin)
      if ! xcode-select -p >/dev/null 2>&1; then
        echo "Installing the Xcode Command Line Tools required by Tauri."
        xcode-select --install || true
        echo "Complete the Xcode Command Line Tools installation, then run ./run.sh again." >&2
        exit 1
      fi
      ;;
    Linux)
      if linux_native_deps_usable; then
        return
      fi
      if command -v pacman >/dev/null 2>&1; then
        install_arch_native_deps
      elif command -v apt-get >/dev/null 2>&1 && command -v dpkg-query >/dev/null 2>&1; then
        install_debian_native_deps
      else
        echo "Native dependency installation currently supports Arch/SteamOS and Debian/Ubuntu." >&2
        echo "Install your distribution's Tauri 2 system prerequisites, then run ./run.sh again." >&2
        exit 1
      fi
      if ! linux_native_deps_usable; then
        echo "The native Tauri dependencies are still incomplete after package installation." >&2
        exit 1
      fi
      ;;
  esac
}

node_runtime_usable() {
  command -v node >/dev/null 2>&1 \
    && command -v npm >/dev/null 2>&1 \
    && node -e 'const major = Number(process.versions.node.split(".")[0]); process.exit(major >= 18 ? 0 : 1)' >/dev/null 2>&1
}

download_file() {
  download_url="$1"
  download_target="$2"

  if command -v curl >/dev/null 2>&1; then
    curl --fail --location --silent --show-error "$download_url" --output "$download_target"
    return
  fi
  if command -v wget >/dev/null 2>&1; then
    wget --quiet --output-document="$download_target" "$download_url"
    return
  fi

  echo "A required tool is missing and neither curl nor wget is available to download it." >&2
  echo "Install curl or wget, then run ./run.sh again." >&2
  exit 1
}

file_sha256() {
  checksum_file="$1"

  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$checksum_file" | awk '{print $1}'
    return
  fi
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$checksum_file" | awk '{print $1}'
    return
  fi

  echo "Cannot verify the Node.js download because sha256sum/shasum is unavailable." >&2
  exit 1
}

install_uv() {
  uv_tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/heimgeist-uv.XXXXXX")"
  trap 'rm -rf "$uv_tmp_dir"' EXIT HUP INT TERM

  echo "Python 3.13 is missing; installing the uv runtime manager locally"
  download_file "https://astral.sh/uv/install.sh" "$uv_tmp_dir/install.sh"
  mkdir -p "$HEIMGEIST_TOOL_BIN_DIR"
  UV_UNMANAGED_INSTALL="$HEIMGEIST_TOOL_BIN_DIR" UV_NO_MODIFY_PATH=1 sh "$uv_tmp_dir/install.sh"

  rm -rf "$uv_tmp_dir"
  trap - EXIT HUP INT TERM
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return
  fi

  install_uv
  if ! command -v uv >/dev/null 2>&1; then
    echo "The local uv installation did not provide an executable." >&2
    exit 1
  fi
}

python_runtime_usable() {
  command -v "$PYTHON_BIN" >/dev/null 2>&1 \
    && "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 1)' >/dev/null 2>&1
}

ensure_python_runtime() {
  if python_runtime_usable; then
    return
  fi
  if [ "$PYTHON_BIN_WAS_SET" -eq 1 ]; then
    echo "PYTHON_BIN '$PYTHON_BIN' is unavailable or is not Python 3.13." >&2
    exit 1
  fi

  ensure_uv
  echo "Installing Python 3.13 locally"
  uv python install 3.13
  PYTHON_BIN="$(uv python find 3.13)"

  if ! python_runtime_usable; then
    echo "The local Python 3.13 installation is incomplete." >&2
    exit 1
  fi
}

rust_runtime_usable() {
  command -v cargo >/dev/null 2>&1 \
    && command -v rustc >/dev/null 2>&1 \
    && rustc --version | awk '
      {
        split($2, version, ".")
        exit !((version[1] > 1) || (version[1] == 1 && version[2] >= 77))
      }
    '
}

install_rustup() {
  rustup_tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/heimgeist-rustup.XXXXXX")"
  trap 'rm -rf "$rustup_tmp_dir"' EXIT HUP INT TERM

  echo "Rust/Cargo 1.77+ was not found; installing the stable Rust toolchain locally"
  download_file "https://sh.rustup.rs" "$rustup_tmp_dir/rustup-init.sh"
  sh "$rustup_tmp_dir/rustup-init.sh" -y --profile minimal --default-toolchain stable --no-modify-path

  rm -rf "$rustup_tmp_dir"
  trap - EXIT HUP INT TERM
}

ensure_rust_runtime() {
  if rust_runtime_usable; then
    return
  fi

  if command -v rustup >/dev/null 2>&1; then
    echo "Installing/updating the stable Rust toolchain"
    rustup toolchain install stable --profile minimal
    rustup default stable
  else
    install_rustup
  fi

  if ! rust_runtime_usable; then
    echo "The Rust installation is incomplete; cargo and rustc 1.77+ are required." >&2
    exit 1
  fi
}

install_project_node() {
  case "$(uname -s)" in
    Linux) node_platform="linux" ;;
    Darwin) node_platform="darwin" ;;
    *)
      echo "Automatic Node.js installation is only supported on Linux and macOS." >&2
      echo "Install Node.js 18+ and npm, then run ./run.sh again." >&2
      exit 1
      ;;
  esac

  case "$(uname -m)" in
    x86_64|amd64) node_arch="x64" ;;
    arm64|aarch64) node_arch="arm64" ;;
    *)
      echo "Automatic Node.js installation does not support architecture $(uname -m)." >&2
      echo "Install Node.js 18+ and npm, then run ./run.sh again." >&2
      exit 1
      ;;
  esac

  node_release_url="https://nodejs.org/dist/latest-v$HEIMGEIST_NODE_MAJOR.x"
  node_tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/heimgeist-node.XXXXXX")"
  trap 'rm -rf "$node_tmp_dir"' EXIT HUP INT TERM

  echo "Node.js 18+ with npm was not found; installing Node.js v$HEIMGEIST_NODE_MAJOR locally"
  download_file "$node_release_url/SHASUMS256.txt" "$node_tmp_dir/SHASUMS256.txt"

  node_archive="$(awk -v suffix="-$node_platform-$node_arch.tar.xz" '
    length($2) >= length(suffix) && substr($2, length($2) - length(suffix) + 1) == suffix {
      print $2
      exit
    }
  ' "$node_tmp_dir/SHASUMS256.txt")"
  if [ -z "$node_archive" ]; then
    echo "No Node.js v$HEIMGEIST_NODE_MAJOR archive is available for $node_platform-$node_arch." >&2
    exit 1
  fi

  expected_sha256="$(awk -v archive="$node_archive" '$2 == archive {print $1; exit}' "$node_tmp_dir/SHASUMS256.txt")"
  download_file "$node_release_url/$node_archive" "$node_tmp_dir/$node_archive"
  actual_sha256="$(file_sha256 "$node_tmp_dir/$node_archive")"
  if [ "$actual_sha256" != "$expected_sha256" ]; then
    echo "Node.js download checksum verification failed." >&2
    exit 1
  fi

  tar -xJf "$node_tmp_dir/$node_archive" -C "$node_tmp_dir"
  mkdir -p "$(dirname "$HEIMGEIST_NODE_DIR")"
  rm -rf "$HEIMGEIST_NODE_DIR"
  mv "$node_tmp_dir/${node_archive%.tar.xz}" "$HEIMGEIST_NODE_DIR"
  rm -rf "$node_tmp_dir"
  trap - EXIT HUP INT TERM
}

ensure_node_runtime() {
  if node_runtime_usable; then
    return
  fi

  if [ -x "$HEIMGEIST_NODE_DIR/bin/node" ] && [ -x "$HEIMGEIST_NODE_DIR/bin/npm" ]; then
    original_path="$PATH"
    PATH="$HEIMGEIST_NODE_DIR/bin:$PATH"
    export PATH
    if node_runtime_usable; then
      return
    fi
    PATH="$original_path"
    export PATH
  fi

  install_project_node
  PATH="$HEIMGEIST_NODE_DIR/bin:$PATH"
  export PATH

  if ! node_runtime_usable; then
    echo "The local Node.js installation is incomplete or older than version 18." >&2
    exit 1
  fi
}

manifest_state() {
  for manifest_path in "$@"; do
    if [ -f "$manifest_path" ]; then
      checksum="$(cksum "$manifest_path" | awk '{print $1 ":" $2}')"
      printf '%s %s\n' "$manifest_path" "$checksum"
    else
      printf '%s missing\n' "$manifest_path"
    fi
  done
}

state_matches() {
  state_file="$1"
  shift
  [ -r "$state_file" ] || return 1
  current_state="$(manifest_state "$@")"
  saved_state="$(cat "$state_file")"
  [ "$saved_state" = "$current_state" ]
}

write_state() {
  state_file="$1"
  shift
  manifest_state "$@" > "$state_file"
}

current_torch_state() {
  printf 'flavor=%s\nindex=%s\n' "$TORCH_FLAVOR" "$HEIMGEIST_TORCH_INDEX_URL"
}

torch_state_matches() {
  [ -r "$TORCH_STATE_FILE" ] || return 1
  saved_state="$(cat "$TORCH_STATE_FILE")"
  [ "$saved_state" = "$(current_torch_state)" ]
}

write_torch_state() {
  current_torch_state > "$TORCH_STATE_FILE"
}

python_deps_usable() {
  "$VENV_DIR/bin/python" - <<'PY' >/dev/null 2>&1
import fastapi
import httpx
import pydantic
import sqlalchemy
import uvicorn
import whisper
PY
}

node_deps_usable() {
  node - <<'NODE' >/dev/null 2>&1
require('react')
require('vite')
require('wait-on')
NODE
  if [ "$?" -ne 0 ]; then
    return 1
  fi

  [ -x node_modules/.bin/tauri ] || [ -x node_modules/.bin/tauri.cmd ]
}

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

ensure_native_build_dependencies
ensure_node_runtime
ensure_python_runtime
ensure_rust_runtime

TORCH_FLAVOR="$(resolve_torch_flavor)"
RECREATE_VENV=0

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

NEEDS_LEGACY_PYTHON_STATE=0
if [ ! -r "$PYTHON_DEPS_STATE_FILE" ]; then
  NEEDS_LEGACY_PYTHON_STATE=1
fi
if [ -z "$HEIMGEIST_TORCH_INDEX_URL" ] && [ ! -r "$TORCH_STATE_FILE" ]; then
  NEEDS_LEGACY_PYTHON_STATE=1
fi

if [ "$RECREATE_VENV" -eq 0 ] && [ "$NEEDS_LEGACY_PYTHON_STATE" -eq 1 ] && python_deps_usable; then
  if [ ! -r "$PYTHON_DEPS_STATE_FILE" ]; then
    write_state "$PYTHON_DEPS_STATE_FILE" backend/requirements.txt
  fi
  if [ -z "$HEIMGEIST_TORCH_INDEX_URL" ] && [ ! -r "$TORCH_STATE_FILE" ]; then
    write_torch_state
  fi
fi

if [ -d node_modules ] && [ ! -r "$NODE_DEPS_STATE_FILE" ] && node_deps_usable; then
  write_state "$NODE_DEPS_STATE_FILE" package.json package-lock.json
fi

NEED_PYTHON_DEPS_INSTALL="$RECREATE_VENV"
NEED_TORCH_INSTALL="$RECREATE_VENV"
NEED_NODE_DEPS_INSTALL=0

if [ "$HEIMGEIST_FORCE_BOOTSTRAP" = "1" ]; then
  NEED_PYTHON_DEPS_INSTALL=1
  NEED_TORCH_INSTALL=1
  NEED_NODE_DEPS_INSTALL=1
fi

if [ "$NEED_PYTHON_DEPS_INSTALL" -eq 0 ] && ! state_matches "$PYTHON_DEPS_STATE_FILE" backend/requirements.txt; then
  NEED_PYTHON_DEPS_INSTALL=1
fi

if [ "$NEED_TORCH_INSTALL" -eq 0 ] && ! torch_state_matches; then
  NEED_TORCH_INSTALL=1
fi

if [ "$NEED_PYTHON_DEPS_INSTALL" -eq 1 ]; then
  echo "Installing Python dependencies"
  "$VENV_DIR/bin/python" -m pip install --upgrade pip
  if [ "$NEED_TORCH_INSTALL" -eq 1 ]; then
    install_selected_torch "$TORCH_FLAVOR"
    write_torch_state
  fi
  "$VENV_DIR/bin/python" -m pip install -r backend/requirements.txt
  write_state "$PYTHON_DEPS_STATE_FILE" backend/requirements.txt
elif [ "$NEED_TORCH_INSTALL" -eq 1 ]; then
  install_selected_torch "$TORCH_FLAVOR"
  write_torch_state
fi

if [ ! -d node_modules ]; then
  NEED_NODE_DEPS_INSTALL=1
elif ! state_matches "$NODE_DEPS_STATE_FILE" package.json package-lock.json; then
  NEED_NODE_DEPS_INSTALL=1
fi

if [ "$NEED_NODE_DEPS_INSTALL" -eq 1 ]; then
  echo "Installing Node dependencies"
  npm install --no-fund --no-audit
  mkdir -p node_modules
  write_state "$NODE_DEPS_STATE_FILE" package.json package-lock.json
fi

npm run dev
