#!/usr/bin/env bash
# 미리보기(쪽 넘김 확인)용 도구 설치: Java, python3-uno, LibreOffice H2Orestart 확장
# render.py 자체는 Python 3 표준 라이브러리만 사용(preview.py는 lxml, python3-uno 필요)
set -e
here="$(cd "$(dirname "$0")" && pwd)"
command -v soffice >/dev/null || sudo apt-get install -y libreoffice-writer
command -v java >/dev/null || sudo DEBIAN_FRONTEND=noninteractive apt-get install -y default-jre-headless libreoffice-java-common
python3 -c "import uno" 2>/dev/null || sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-uno
python3 -c "import lxml" 2>/dev/null || pip install lxml
if ! unopkg list 2>/dev/null | grep -q ebandal.libreoffice.H2Orestart; then
  mkdir -p "$here/vendor"
  [ -f "$here/vendor/H2Orestart.oxt" ] || curl -fsSL -o "$here/vendor/H2Orestart.oxt" \
    https://github.com/ebandal/H2Orestart/releases/download/v0.7.14/H2Orestart.oxt
  unopkg add "$here/vendor/H2Orestart.oxt"
fi
unopkg list 2>/dev/null | grep -A1 "Identifier: ebandal" || true
echo "준비 완료"
