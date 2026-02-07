#!/bin/bash
#
# netns_setup.sh - 네트워크 네임스페이스 설정 스크립트
#
# 공인 IP가 두 개인 서버에서 특정 프로그램을 특정 공인 IP로
# 통신하도록 네트워크 네임스페이스를 설정합니다.
#
# 사용법:
#   sudo bash netns_setup.sh create   # 네임스페이스 생성
#   sudo bash netns_setup.sh delete   # 네임스페이스 삭제
#   sudo bash netns_setup.sh status   # 상태 확인
#   sudo bash netns_setup.sh exec <명령어>  # 네임스페이스 안에서 명령어 실행
#

set -euo pipefail

# ============================================================
# 설정 - 환경에 맞게 수정하세요
# ============================================================
NAMESPACE="ns_eth1"
INTERFACE="eth1"
INTERFACE_IP="192.168.0.81"
NETMASK="24"
GATEWAY="192.168.0.1"        # ip route | grep default 로 확인
DNS_SERVER="8.8.8.8"
# ============================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[OK]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

check_root() {
    [[ $EUID -eq 0 ]] || error "root 권한이 필요합니다. sudo로 실행하세요."
}

do_create() {
    check_root

    ip netns list 2>/dev/null | grep -qw "$NAMESPACE" && \
        error "네임스페이스 '$NAMESPACE'가 이미 존재합니다. 먼저 delete 하세요."

    ip link show "$INTERFACE" &>/dev/null || \
        error "인터페이스 '$INTERFACE'를 찾을 수 없습니다."

    # 네임스페이스 생성
    ip netns add "$NAMESPACE"
    info "네임스페이스 '$NAMESPACE' 생성"

    # 인터페이스를 네임스페이스로 이동
    ip link set "$INTERFACE" netns "$NAMESPACE"
    info "'$INTERFACE'를 네임스페이스로 이동"

    # 네임스페이스 내부 설정
    ip netns exec "$NAMESPACE" ip link set lo up
    ip netns exec "$NAMESPACE" ip addr add "${INTERFACE_IP}/${NETMASK}" dev "$INTERFACE"
    ip netns exec "$NAMESPACE" ip link set "$INTERFACE" up
    ip netns exec "$NAMESPACE" ip route add default via "$GATEWAY"
    info "네임스페이스 내 네트워크 설정 완료"

    # DNS 설정
    mkdir -p "/etc/netns/${NAMESPACE}"
    echo "nameserver ${DNS_SERVER}" > "/etc/netns/${NAMESPACE}/resolv.conf"
    info "DNS 설정 완료"

    # 연결 테스트
    echo ""
    echo "--- 네임스페이스 내 인터페이스 ---"
    ip netns exec "$NAMESPACE" ip -br addr
    echo ""
    echo "--- 네임스페이스 라우팅 ---"
    ip netns exec "$NAMESPACE" ip route
    echo ""

    if ip netns exec "$NAMESPACE" ping -c 2 -W 3 8.8.8.8 &>/dev/null; then
        info "외부 연결 테스트 성공"
    else
        warn "외부 연결 실패 - 게이트웨이(${GATEWAY})를 확인하세요"
    fi

    echo ""
    echo "============================================"
    echo " 프로그램 실행 방법"
    echo "============================================"
    echo ""
    echo " # eth0 (기본) - 그냥 실행"
    echo " python main.py"
    echo ""
    echo " # eth1 (네임스페이스) - 아래 명령으로 실행"
    echo " sudo ip netns exec $NAMESPACE sudo -u \$(logname) bash -c \\"
    echo "   'cd /path/to/pynereal && source venv/bin/activate && python main.py'"
    echo ""
}

do_delete() {
    check_root

    ip netns list 2>/dev/null | grep -qw "$NAMESPACE" || {
        warn "네임스페이스 '$NAMESPACE'가 존재하지 않습니다."
        return 0
    }

    ip netns delete "$NAMESPACE"
    rm -rf "/etc/netns/${NAMESPACE}"
    info "네임스페이스 '$NAMESPACE' 삭제 완료"

    warn "'$INTERFACE'가 기본 네임스페이스로 복귀했습니다. IP 재설정이 필요할 수 있습니다:"
    echo "  sudo ip addr add ${INTERFACE_IP}/${NETMASK} dev ${INTERFACE}"
    echo "  sudo ip link set ${INTERFACE} up"
}

do_status() {
    echo "--- 네임스페이스 목록 ---"
    if ip netns list 2>/dev/null | grep -qw "$NAMESPACE"; then
        echo "  $NAMESPACE: 존재함"
        echo ""
        echo "--- 네임스페이스 내 인터페이스 ---"
        ip netns exec "$NAMESPACE" ip -br addr 2>/dev/null
        echo ""
        echo "--- 네임스페이스 라우팅 ---"
        ip netns exec "$NAMESPACE" ip route 2>/dev/null
    else
        echo "  $NAMESPACE: 없음"
    fi
    echo ""
    echo "--- 기본 네임스페이스 인터페이스 ---"
    ip -br addr
}

do_exec() {
    check_root

    ip netns list 2>/dev/null | grep -qw "$NAMESPACE" || \
        error "네임스페이스 '$NAMESPACE'가 존재하지 않습니다. 먼저 create 하세요."

    REAL_USER="${SUDO_USER:-$(logname 2>/dev/null || echo $USER)}"
    exec ip netns exec "$NAMESPACE" sudo -u "$REAL_USER" "$@"
}

case "${1:-}" in
    create) do_create ;;
    delete) do_delete ;;
    status) do_status ;;
    exec)   shift; do_exec "$@" ;;
    *)
        echo "사용법: sudo bash $0 {create|delete|status|exec <명령어>}"
        echo ""
        echo "  create              네임스페이스 생성 및 인터페이스 이동"
        echo "  delete              네임스페이스 삭제 및 인터페이스 복귀"
        echo "  status              현재 상태 확인"
        echo "  exec <명령어>       네임스페이스 안에서 명령어 실행"
        echo ""
        echo "예시:"
        echo "  sudo bash $0 create"
        echo "  sudo bash $0 exec bash -c 'cd /home/user/pynereal && source venv/bin/activate && python main.py'"
        echo "  sudo bash $0 exec curl ifconfig.me    # 공인IP 확인"
        exit 1
        ;;
esac
